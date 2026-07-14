"""Lifecycle — start, stop, heartbeat, signal-file detection, refresh, preset fallback.

The agent's life support: starting, stopping, breathing, detecting signal
files (.sleep, .suspend, .refresh, .prompt, .clear, .inquiry, .rules,
.interrupt), tracking uptime, managing AED timeout, and running periodic
snapshots.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..config import IDLE_SLEEP_TIMEOUT_SECONDS
from ..refresh_watcher import RefreshWatcherRequest
from ..snapshot import SourceRevisionPort


# Key source files to hash for the runtime fingerprint.  A small curated
# list keeps startup cost low (<5ms) while still catching the most common
# source-level drift vectors.
_FP_KEY_FILES: list[str] = [
    "base_agent/__init__.py",
    "base_agent/lifecycle.py",
    "base_agent/turn.py",
    "nudge/__init__.py",
    "nudge/kernel_version.py",
    "intrinsics/system/__init__.py",
    "meta_block.py",
    "notifications.py",
    "workdir.py",
]


def _capture_runtime_fingerprint(
    source_revision_port: SourceRevisionPort,
) -> dict:
    """Capture a dual fingerprint of the running lingtai.kernel source.

    Returns a dict with:
      - ``git_rev``: short git HEAD hash, or ``None`` if unavailable
      - ``source_digest``: SHA-256 hex prefix (12 chars) of key source files
      - ``captured_at``: ISO-8601 timestamp
    """
    # Resolve the lingtai.kernel package source directory
    try:
        import lingtai.kernel
        pkg_dir = Path(lingtai.kernel.__file__).resolve().parent  # type: ignore[arg-type]
    except Exception:
        pkg_dir = None

    # Native-short revision query; process failure translation belongs to the Port.
    git_rev = (
        source_revision_port.current_revision(None, 2.0)
        if pkg_dir is not None
        else None
    )

    # Hash key source files
    source_digest: str | None = None
    if pkg_dir is not None:
        h = hashlib.sha256()
        for rel in _FP_KEY_FILES:
            fp = pkg_dir / rel
            try:
                h.update(fp.read_bytes())
            except OSError:
                h.update(b"\x00")  # missing file marker
        source_digest = h.hexdigest()[:12]

    return {
        "git_rev": git_rev,
        "source_digest": source_digest,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _active_stuck_threshold_s() -> float:
    """Issue #164 — seconds of no-progress ACTIVE before the watchdog fires.

    Defaults to 600s (~10 min). Overridable via
    ``LINGTAI_ACTIVE_STUCK_THRESHOLD_S`` so operators can tune for noisy
    LLM providers without changing kernel code.
    """
    try:
        return max(30.0, float(os.environ.get("LINGTAI_ACTIVE_STUCK_THRESHOLD_S", "600")))
    except (TypeError, ValueError):
        return 600.0


def _start(agent) -> None:
    """Start the agent's main loop thread."""
    from ..token_ledger import sum_token_ledger

    agent._sealed = True
    if agent._thread and agent._thread.is_alive():
        return
    agent._shutdown.clear()

    # Initialize snapshot storage only when the opt-in policy is enabled.
    if agent._config.snapshot_interval is not None:
        agent._snapshot_port.initialize()

    # Capture startup time for uptime tracking
    from datetime import datetime, timezone
    agent._started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    agent._uptime_anchor = agent._lifecycle_clock.monotonic_seconds()

    # Export assembled system prompt to system/system.md
    agent._flush_system_prompt()

    # Publish process liveness before the heavier restore/listener startup
    # work. During this phase the heartbeat loop only emits runtime metadata;
    # signal and notification handling starts after the main loop exists.
    agent._heartbeat_runtime_ready = False
    _start_heartbeat(agent)

    # Restore chat session and token state from filesystem if available
    chat_history_file = agent._working_dir / "history" / "chat_history.jsonl"
    if chat_history_file.is_file():
        try:
            # Mark stale spill manifests before the LLM sees them so
            # expired sidecar files are flagged honestly.
            from ..tool_result_artifacts import mark_expired_spill_manifests
            try:
                expired = mark_expired_spill_manifests(agent._working_dir)
                if expired:
                    agent._log("spill_manifests_expired_on_restore", count=expired)
            except Exception:
                pass  # best-effort; don't block startup

            messages = [
                json.loads(line)
                for line in chat_history_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            agent.restore_chat({"messages": messages})
            agent._log("session_restored")
            agent._rehydrate_appendix_tracking()
        except Exception as e:
            from ..logging import get_logger
            get_logger().warning(f"[{agent.agent_name}] Failed to restore chat history: {e}")

    # Rehydrate any still-open WorkerStillRunning recovery artifacts into a
    # high-priority notification so the next process re-surfaces the unfinished
    # turn after refresh/relaunch.
    try:
        from .worker_recovery import rehydrate_worker_hang_recovery

        rehydrated = rehydrate_worker_hang_recovery(agent)
        if rehydrated:
            agent._log("worker_hang_recovery_rehydrated", count=rehydrated)
    except Exception as e:
        try:
            agent._log("worker_hang_recovery_rehydrate_failed", error=str(e)[:300])
        except Exception:
            pass
    # Rebuild the AGENT-SESSION (since-current-molt) from the durable trajectory
    # and seed the token counters from it, so a refresh/restart preserves the
    # since-molt ``token_usage.session`` totals instead of restoring LIFETIME
    # ledger totals. This is the fix for the #679-class defect the session spec
    # names (docs/references/runtime-vs-agent-session-objects.md §4.1/§5): the
    # ledger sum is a lifetime aggregate, so restoring from it made the injected
    # since-molt ``session`` half report lifetime numbers after a refresh.
    #
    # The rebuild uses the optimized path (indexed sqlite → bounded reverse scan
    # → full-scan last resort), so the normal case does NOT full-scan
    # events.jsonl. The lifetime ledger is still read as a compatibility fallback
    # only if the event-based rebuild is unavailable (e.g. no trajectory yet).
    try:
        agent_session = agent.rebuild_agent_session()
        seeded = False
        if agent_session is not None and agent_session.rebuild_tier != "none":
            agent.restore_token_state(
                {
                    "input_tokens": agent_session.input_tokens,
                    "output_tokens": agent_session.output_tokens,
                    "thinking_tokens": agent_session.thinking_tokens,
                    "cached_tokens": agent_session.cached_tokens,
                    "api_calls": agent_session.api_calls,
                }
            )
            seeded = True
            agent._log(
                "agent_session_rebuilt",
                molt_count=agent_session.molt_count,
                rebuild_tier=agent_session.rebuild_tier,
                events_scanned=agent_session.rebuild_events_scanned,
                api_calls=agent_session.api_calls,
                input_tokens=agent_session.input_tokens,
                cached_tokens=agent_session.cached_tokens,
            )
        if not seeded:
            # No usable trajectory (brand-new agent, or corrupt/absent events):
            # fall back to the lifetime ledger accumulator for back-compat.
            ledger_path = agent._working_dir / "logs" / "token_ledger.jsonl"
            totals = sum_token_ledger(ledger_path)
            agent.restore_token_state(totals)
    except Exception as e:
        from ..logging import get_logger
        get_logger().warning(
            f"[{agent.agent_name}] Failed to rebuild/restore token state: {e}"
        )
        # Last-resort: never leave the counters unrestored on an unexpected error.
        try:
            ledger_path = agent._working_dir / "logs" / "token_ledger.jsonl"
            agent.restore_token_state(sum_token_ledger(ledger_path))
        except Exception:
            pass

    # Start MailService listener if configured
    if agent._mail_service is not None:
        try:
            agent._mail_service.listen(on_message=lambda payload: agent._on_mail_received(payload))
        except RuntimeError:
            pass  # Already listening — that's fine

    # Capture runtime fingerprint for drift detection
    try:
        agent._runtime_fingerprint = _capture_runtime_fingerprint(
            agent._source_revision_port
        )
    except Exception:
        agent._runtime_fingerprint = None

    agent._thread = threading.Thread(
        target=agent._run_loop,
        daemon=True,
        name=f"agent-{agent.agent_name or agent._working_dir.name}",
    )
    agent._thread.start()
    agent._heartbeat_runtime_ready = True
    # Boot state is IDLE (fire-eligible) — start the timer here.
    agent._start_soul_timer()


def _reset_uptime(agent) -> None:
    """Reset the uptime anchor used for runtime uptime reporting."""
    agent._uptime_anchor = agent._lifecycle_clock.monotonic_seconds()


def _stop(agent, timeout: float = 5.0) -> None:
    """Signal shutdown and wait for the agent thread to exit.

    Heartbeat is stopped LAST (just before the workdir-lease release) so external
    observers — TUI launcher, `lingtai-tui list`, `lingtai-tui purge` — see
    `.agent.heartbeat` as fresh and present for the entire teardown window.
    Otherwise the file vanishes seconds before the Python process actually
    exits, and a quick relaunch races a still-living interpreter into the
    same workdir. See workdir-race investigation 2026-05-09.

    Daemon resources are also reclaimed before liveness is withdrawn: daemon
    ThreadPoolExecutor workers and external CLI process groups can otherwise
    keep this interpreter visible in `ps` after heartbeat/lock are gone, which
    makes refresh watchers race the duplicate-process guard.
    """
    agent._log("agent_stop")
    agent._cancel_soul_timer()
    agent._shutdown.set()
    # Wake a run loop blocked in inbox.get; its post-dequeue shutdown check
    # consumes this sentinel without dispatching a turn.
    inbox = getattr(agent, "inbox", None)
    if inbox is not None:
        from ..message import _make_message, MSG_TC_WAKE
        inbox.put(_make_message(MSG_TC_WAKE, "system", ""))
    # Stop any programmable Task Card watcher threads deterministically. The
    # loops also observe ``_shutdown`` (daemon threads), but this joins and
    # clears them without any filesystem deletion (Jason #7258/#7259).
    _task_card_controller = getattr(agent, "_task_card_controller", None)
    if _task_card_controller is not None:
        try:
            _task_card_controller.shutdown_for_agent_stop(reason="agent_stop")
        except Exception:
            pass
    if agent._thread:
        agent._thread.join(timeout=timeout)
    _shutdown_daemon_runtime(agent, reason="agent_stop")
    agent._session.close()

    # Stop MailService if configured
    if agent._mail_service is not None:
        try:
            agent._mail_service.stop()
        except Exception:
            pass

    # Close the event journal if configured.
    if agent._event_journal is not None:
        try:
            agent._event_journal.close()
        except Exception:
            pass

    # Persist final state, stop heartbeat, release the workdir lease — order
    # matters. See docstring above; heartbeat must remain fresh until this point.
    agent._workdir.write_manifest(agent._build_manifest())
    _stop_heartbeat(agent)
    agent._workdir_lease.release()


def _shutdown_daemon_runtime(agent, *, reason: str) -> None:
    """Best-effort daemon cleanup before parent liveness is released."""
    mgr = None
    try:
        get_capability = getattr(agent, "get_capability", None)
        if callable(get_capability):
            mgr = get_capability("daemon")
        if mgr is None:
            mgr = getattr(agent, "_capability_managers", {}).get("daemon")
    except Exception as e:
        try:
            agent._log("daemon_lifecycle_lookup_failed", reason=reason, error=str(e))
        except Exception:
            pass
        return

    shutdown = getattr(mgr, "shutdown_for_agent_stop", None)
    if not callable(shutdown):
        return
    try:
        shutdown(reason=reason)
    except Exception as e:
        # Stop/refresh teardown must continue even if daemon cleanup races with
        # already-finished workers. Keep heartbeat/lock alive until this point,
        # log the failure, then proceed to the rest of stop.
        try:
            agent._log("daemon_lifecycle_shutdown_failed", reason=reason, error=str(e))
        except Exception:
            pass


def _start_heartbeat(agent) -> None:
    """Start the heartbeat daemon thread."""
    if agent._heartbeat_thread is not None:
        return
    # Do not inherit a prior final-stop signal on a new heartbeat thread.
    agent._heartbeat_stop.clear()
    agent._heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(agent,),
        daemon=True,
        name=f"heartbeat-{agent.agent_name or agent._working_dir.name}",
    )
    agent._heartbeat_thread.start()
    agent._log("heartbeat_start")


def _stop_heartbeat(agent) -> None:
    """Stop the heartbeat (called only by stop/shutdown)."""
    thread = agent._heartbeat_thread
    agent._heartbeat_thread = None  # signals the loop to exit
    # Wake the cadence before joining; withdraw the heartbeat only after exit.
    agent._heartbeat_stop.set()
    if thread is not None:
        thread.join(timeout=5.0)
    # Withdraw own liveness through the injected presence Port (best-effort
    # inside the adapter), preserving the manifest-persist → heartbeat-withdraw
    # → workdir-lease-release teardown order owned by ``_stop``.
    agent._agent_presence.withdraw_heartbeat()
    agent._log("heartbeat_stop", heartbeat=agent._heartbeat)


def _heartbeat_loop(agent) -> None:
    """Beat every 1 second. AED if agent is STUCK.

    Loop exit is governed solely by `agent._heartbeat_thread is None`, which
    `_stop_heartbeat` flips at the very end of `_stop`. The loop deliberately
    keeps writing fresh timestamps even after `agent._shutdown.is_set()` so
    the heartbeat file remains a faithful "this Python process is alive"
    signal across the entire teardown — preventing duplicate-launch races
    in the TUI. Signal-file detection IS gated on `_shutdown` below so we
    don't reprocess `.suspend`/`.refresh` mid-teardown.
    """
    from ..state import AgentState

    while agent._heartbeat_thread is not None:
        _write_heartbeat_tick(agent)

        # Once shutdown is signalled, keep beating the file (above) but stop
        # consuming signal files — the run loop is exiting and reprocessing
        # `.suspend`/`.refresh` here would emit spurious state-change events.
        if agent._shutdown.is_set() or not getattr(agent, "_heartbeat_runtime_ready", True):
            # _shutdown keeps beating; only final heartbeat stop wakes the wait.
            agent._heartbeat_stop.wait(1.0)
            continue

        # --- signal file detection ---
        interrupt_file = agent._working_dir / ".interrupt"
        if interrupt_file.is_file():
            try:
                interrupt_file.unlink()
            except OSError:
                pass
            agent._cancel_event.set()
            agent._log("interrupt_received", source="signal_file")

        # .refresh = full refresh with relaunch (identical to system(action='refresh'))
        refresh_file = agent._working_dir / ".refresh"
        if refresh_file.is_file():
            taken_file = agent._working_dir / ".refresh.taken"
            try:
                refresh_file.rename(taken_file)
            except OSError:
                pass
            # Delegate to _perform_refresh which handles the full flow:
            # save chat history, spawn watcher process, deferred relaunch.
            _perform_refresh(agent)
            # Signal shutdown so the heartbeat loop exits and the watcher
            # can detect the lock release.  The _shutdown gate above
            # prevents the heartbeat from reprocessing .refresh on the
            # next tick.
            agent._shutdown.set()

        # .suspend = SUSPENDED (full process death, external only)
        suspend_file = agent._working_dir / ".suspend"
        if suspend_file.is_file():
            try:
                suspend_file.unlink()
            except OSError:
                pass
            agent._cancel_event.set()
            agent._set_state(AgentState.SUSPENDED, reason="suspend signal")
            agent._shutdown.set()
            agent._log("suspend_received", source="signal_file")

        # .sleep = ASLEEP (sleep, listeners stay alive)
        sleep_file = agent._working_dir / ".sleep"
        if sleep_file.is_file():
            try:
                sleep_file.unlink()
            except OSError:
                pass
            agent._cancel_event.set()
            agent._set_state(AgentState.ASLEEP, reason="sleep signal")
            agent._asleep.set()
            agent._log("sleep_received", source="signal_file")

        # .prompt = inject text input as [system] message
        prompt_file = agent._working_dir / ".prompt"
        if prompt_file.is_file():
            try:
                content = prompt_file.read_text(encoding="utf-8").strip()
            except OSError:
                content = ""
            try:
                prompt_file.unlink()
            except OSError:
                pass
            if content:
                agent.send(content, sender="system")
                agent._log("prompt_received", source="signal_file")

        # .clear = force a full molt (context wipe + recovery summary).
        clear_file = agent._working_dir / ".clear"
        if clear_file.is_file():
            try:
                source = clear_file.read_text(encoding="utf-8").strip() or "admin"
            except OSError:
                source = "admin"
            try:
                clear_file.unlink()
            except OSError:
                pass
            try:
                context_forget = agent._intrinsic_hook("psyche", "context_forget")
                if context_forget is not None:
                    context_forget(agent, source=source)
                agent._log("clear_received", source=source)
            except Exception as clear_err:
                from ..logging import get_logger
                get_logger().error(
                    f"[{agent.agent_name}] .clear signal failed: {clear_err}",
                )

        # .inquiry = soul inquiry (from TUI /btw or auto-insight)
        inquiry_file = agent._working_dir / ".inquiry"
        taken_file = agent._working_dir / ".inquiry.taken"
        if inquiry_file.is_file() and not taken_file.is_file():
            try:
                inquiry_file.rename(taken_file)
            except OSError:
                pass
            else:
                try:
                    content = taken_file.read_text(encoding="utf-8").strip()
                except OSError:
                    content = ""
                if content:
                    lines = content.split("\n", 1)
                    if len(lines) == 2 and lines[0] in ("human", "insight", "agent"):
                        source, question = lines[0], lines[1].strip()
                    else:
                        source, question = "human", content.strip()
                    if question:
                        def _inquiry_done(q: str, s: str, tf) -> None:
                            try:
                                agent._run_inquiry(q, source=s)
                            finally:
                                try:
                                    tf.unlink()
                                except OSError:
                                    pass
                        threading.Thread(
                            target=_inquiry_done,
                            args=(question, source, taken_file),
                            daemon=True,
                        ).start()
                    else:
                        try:
                            taken_file.unlink()
                        except OSError:
                            pass
                else:
                    try:
                        taken_file.unlink()
                    except OSError:
                        pass

        # .rules = network rules signal
        _check_rules_file(agent)

        # --- Nudges ---
        # Per-agent periodic checks that publish to `.notification/nudge.json`
        # when something needs the agent's attention (e.g. a newer lingtai
        # wheel is installed on disk than the version this process imported).
        # Each check throttles itself; the dispatcher wraps individual calls
        # so a misbehaving check cannot block the heartbeat loop. See
        # `nudge/ANATOMY.md`.
        try:
            from ..nudge import run_checks as _run_nudge_checks
            _run_nudge_checks(agent)
        except Exception as nudge_err:
            from ..logging import get_logger
            get_logger().warning(
                f"[{agent.agent_name}] nudge dispatch failed: {nudge_err}"
            )

        try:
            _maybe_sleep_after_idle_timeout(agent)
        except Exception as idle_sleep_err:
            agent._log("idle_sleep_timeout_failed", error=str(idle_sleep_err))
            print(
                f"[{agent.agent_name}] idle sleep timeout failed: "
                f"{idle_sleep_err}"
            )

        # --- Notification sync ---
        # Poll the `.notification/` directory for changes.  The sync
        # method is a no-op when the fingerprint is unchanged, so this
        # call is cheap on the steady-state path.  On change it strips
        # the prior wire block and reinjects per current state (IDLE
        # pair / ACTIVE meta-stash / ASLEEP wake-then-pair).  See
        # base_agent/__init__.py:_sync_notifications and
        # the notification filesystem design rationale.
        try:
            agent._sync_notifications()
            # After sync, if a Telegram notification just arrived, set up
            # the automatic Task Card context for this turn.
            agent._setup_telegram_task_card()
        except Exception as notif_err:
            from ..logging import get_logger
            get_logger().warning(
                f"[{agent.agent_name}] notification sync failed: {notif_err}"
            )

        if agent._state == AgentState.STUCK:
            now = agent._lifecycle_clock.monotonic_seconds()
            if agent._aed_start is None:
                agent._aed_start = now
            if now - agent._aed_start > agent._config.aed_timeout:
                agent._log("aed_timeout", seconds=now - agent._aed_start)
                agent._set_state(AgentState.ASLEEP, reason="AED timeout")
                agent._save_chat_history()
                agent._asleep.set()
        else:
            agent._aed_start = None

        # Issue #164 — ACTIVE-without-progress watchdog.
        #
        # Fires once per stuck episode (latched by ``_active_stuck_logged``)
        # when the agent has been ACTIVE for longer than the configured
        # threshold without any progress event (wake, llm_call, llm_response,
        # tool_call, tool_result, notification_pair_injected, agent_state).
        # The companion symptom — a ``notification_deferred_active`` storm —
        # is included in the log fields so a single grep on
        # ``active_without_progress`` exposes both halves of the failure.
        #
        # We deliberately do NOT auto-recover here: the failure modes seen
        # in dev-2/dev-1/spiritualblisslingtaibot all benefited from human
        # inspection before .clear/refresh. Auto-restart could mask a
        # repeatable bug behind silent retries.
        if agent._state == AgentState.ACTIVE and not agent._active_stuck_logged:
            threshold = _active_stuck_threshold_s()
            no_progress_for = agent._lifecycle_clock.wall_seconds() - agent._last_progress_at
            if no_progress_for > threshold:
                agent._log(
                    "active_without_progress",
                    no_progress_seconds=round(no_progress_for, 1),
                    threshold_seconds=threshold,
                    state_since=agent._state_changed_at,
                    active_turn_kind=agent._active_turn_kind,
                    active_turn_id=agent._active_turn_id,
                    deferred_notifications=agent._deferred_notifications_count,
                    deferred_oldest_at=agent._deferred_notifications_oldest_at,
                )
                agent._write_status_snapshot()
                agent._active_stuck_logged = True

        # Periodic snapshot (Time Machine) — off by default
        if agent._config.snapshot_interval is not None:
            now_mono = agent._lifecycle_clock.monotonic_seconds()
            if now_mono - agent._last_snapshot >= agent._config.snapshot_interval:
                agent._snapshot_port.snapshot()
                agent._last_snapshot = now_mono

            # Periodic GC — every 24 hours
            if now_mono - agent._last_gc >= 86400:
                agent._snapshot_port.collect_garbage()
                agent._last_gc = now_mono

        agent._heartbeat_stop.wait(1.0)


def _maybe_sleep_after_idle_timeout(agent, *, now_mono: float | None = None) -> None:
    """Move long-idle agents to ASLEEP using a hidden fixed runtime timeout.

    The timeout replaces the old agent-visible stamina countdown. It is not
    configurable from init.json and is not exposed through prompt/status/meta;
    it only keeps idle/asleep lifecycle semantics from collapsing completely.
    """
    from ..state import AgentState

    if agent._state != AgentState.IDLE:
        return

    now = agent._lifecycle_clock.monotonic_seconds() if now_mono is None else now_mono
    idle_since = getattr(agent, "_idle_since_monotonic", None)
    if idle_since is None:
        agent._idle_since_monotonic = now
        return

    elapsed = now - idle_since
    if elapsed < IDLE_SLEEP_TIMEOUT_SECONDS:
        return

    agent._log(
        "idle_sleep_timeout",
        idle_seconds=round(elapsed, 1),
        timeout_seconds=IDLE_SLEEP_TIMEOUT_SECONDS,
    )
    agent._set_state(AgentState.ASLEEP, reason="idle sleep timeout")
    agent._save_chat_history()
    agent._asleep.set()


def _write_heartbeat_tick(agent) -> None:
    """Write one real runtime heartbeat and best-effort status snapshot."""
    # Wall clock (``lifecycle_clock.wall_seconds()``), not monotonic. Deliberate:
    # heartbeat is written to a file and read by the presence store's liveness
    # observation in a DIFFERENT process, so it must be a cross-process wall
    # timestamp. The clock is now the injected Core LifecycleClockPort (see
    # kernel/lifecycle_clock/CONTRACT.md). Publication of the raw float goes
    # through the injected AgentPresenceStorePort (best-effort inside the
    # adapter), which writes exactly ``str(value)`` with no newline.
    agent._heartbeat = agent._lifecycle_clock.wall_seconds()

    agent._agent_presence.publish_heartbeat(agent._heartbeat)

    try:
        agent._write_status_snapshot()
    except Exception:
        pass


def _perform_refresh(
    agent,
    *,
    skip_chat_history_save: bool = False,
    skip_save_reason: str | None = None,
) -> None:
    """Refresh = .refresh handshake + deferred relaunch.

    Self-sufficient across all call sites — heartbeat, tool-call (intrinsic
    ``system(action='refresh')``), and AED preset-fallback in ``turn.py`` all
    call directly. Two filesystem signals drive the watcher subprocess:

      1. ``.refresh.taken`` must exist before the watcher's ack deadline.
      2. ``.agent.lock`` must clear before the watcher's lock deadline.

    The heartbeat path renames ``.refresh`` → ``.refresh.taken`` before
    invoking us and sets ``agent._shutdown`` immediately after. Direct
    callers do neither — so we normalize the handshake here and then set
    ``_shutdown`` / ``_cancel_event`` ourselves so the watcher's second
    phase can complete.
    """
    # When the worker interface is poisoned, the in-memory ChatInterface may
    # still be mutated by a stuck worker thread — saving it would serialize
    # unsafe state. Fail closed: skip the save and rebuild from disk.
    poisoned = bool(getattr(agent, "_llm_worker_interface_poisoned", False))
    effective_skip_save = skip_chat_history_save or poisoned
    effective_skip_reason = (
        skip_save_reason
        or ("worker_still_running_interface_unsafe" if poisoned else None)
    )
    agent._log(
        "refresh_start",
        skip_chat_history_save=effective_skip_save,
        skip_save_reason=effective_skip_reason,
    )
    if effective_skip_save:
        agent._log("refresh_chat_history_save_skipped", reason=effective_skip_reason)
    else:
        agent._save_chat_history()
    # Bound-method dispatch — _build_launch_cmd lives on BaseAgent (returns
    # None) and Agent (returns the real `lingtai-agent run` cmd). A prior version
    # called a module-level _build_launch_cmd shadow that always returned
    # None, silently no-opping every user refresh on the Agent subclass —
    # see issue #7, confirmed in vivo against deepseek_pro 2026-05-05.
    cmd = agent._build_launch_cmd()
    if cmd is None:
        agent._log("refresh_no_launch_cmd")
        return

    # A real launch command means this refresh will actually spawn a watcher.
    # Fail loudly here, before any handshake or shutdown mutation, if the
    # agent has no RefreshWatcherPort — raw BaseAgent construction allows
    # omitting it (see kernel/refresh_watcher/CONTRACT.md), but an omitted
    # Port must never orphan an agent mid-handshake or leave it silently
    # unable to relaunch.
    if agent._refresh_watcher is None:
        raise RuntimeError(
            "_perform_refresh requires a RefreshWatcherPort to spawn the "
            "relaunch watcher, but this agent was constructed without one "
            "(refresh_watcher=None). Inject a RefreshWatcherPort (e.g. "
            "PosixRefreshWatcherAdapter) at BaseAgent construction."
        )

    working_dir = agent._working_dir
    refresh_path = working_dir / ".refresh"
    taken_path_obj = working_dir / ".refresh.taken"
    # Handshake normalization — make the on-disk state look the same
    # regardless of caller. The watcher polls for `.refresh.taken`; we
    # guarantee it exists before spawning the watcher, then remove any
    # remaining `.refresh` so the heartbeat doesn't fire a duplicate
    # watcher on its next tick.
    handshake_source = None
    if taken_path_obj.exists():
        handshake_source = "preexisting_taken"
    elif refresh_path.exists():
        try:
            refresh_path.rename(taken_path_obj)
            handshake_source = "renamed_refresh"
        except OSError:
            # Rename failed (e.g. cross-device, race). Fall back to a
            # synthesized ack so the watcher can still proceed.
            try:
                taken_path_obj.touch()
                handshake_source = "synthesized_after_rename_failed"
            except OSError:
                handshake_source = "ack_write_failed"
    else:
        try:
            taken_path_obj.touch()
            handshake_source = "synthesized_direct_call"
        except OSError:
            handshake_source = "ack_write_failed"
    if not taken_path_obj.exists():
        # Do not spawn a watcher or shut the agent down unless the ack
        # invariant is actually established. Otherwise an unusual
        # filesystem failure could turn a failed refresh into a dead
        # agent with no relaunch. If .refresh still exists, leave it for
        # the heartbeat path or a later retry rather than consuming it.
        agent._log("refresh_ack_failed", handshake=handshake_source)
        return

    # If both files happen to exist (heartbeat renamed but a later
    # consumer rewrote .refresh), remove the stale .refresh so the
    # heartbeat does not spawn a second watcher.
    try:
        refresh_path.unlink(missing_ok=True)
    except OSError:
        pass

    taken_path = str(taken_path_obj)
    lock_path = str(working_dir / ".agent.lock")
    events_path = str(working_dir / "logs" / "events.jsonl")
    agent_name = agent.agent_name
    address = agent._working_dir.name
    working_dir_str = str(working_dir)
    stderr_log = str(working_dir / "logs" / "refresh_relaunch.log")
    # A tuple-of-pairs would only be shallowly immutable: the runtime-identity
    # dict's nested `kernel_runtime` value is itself a mutable dict (in fact
    # the same object as runtime_identity.py's module-level cache), so a
    # shallow container copy would still alias and expose it. Snapshotting to
    # a JSON string at this boundary is genuinely immutable at any nesting
    # depth — see RefreshWatcherRequest.identity_fields_json's docstring.
    identity_fields_json = json.dumps(agent._runtime_identity_event_fields)
    request = RefreshWatcherRequest(
        taken_path=taken_path,
        lock_path=lock_path,
        events_path=events_path,
        stderr_log=stderr_log,
        working_dir=working_dir_str,
        cmd=tuple(cmd),
        agent_name=agent_name,
        address=address,
        identity_fields_json=identity_fields_json,
    )
    agent._refresh_watcher.spawn_detached(request)
    agent._log("refresh_deferred_relaunch",
               cmd=cmd[0], handshake=handshake_source)
    # Lock-clear signaling — direct callers (intrinsic system tool call,
    # AED preset fallback) reach this function without going through the
    # heartbeat's `_shutdown.set()` step at lifecycle.py:212. Without
    # `_shutdown` set the run loop never exits and `.agent.lock` never
    # releases, so the watcher times out at phase='lock'. Setting these
    # events here makes the watcher's second phase complete uniformly
    # regardless of caller; the heartbeat path's redundant `_shutdown.set()`
    # is idempotent.
    cancel_event = getattr(agent, "_cancel_event", None)
    if cancel_event is not None:
        try:
            cancel_event.set()
        except Exception:
            pass
    shutdown_event = getattr(agent, "_shutdown", None)
    if shutdown_event is not None:
        try:
            shutdown_event.set()
        except Exception:
            pass


def _can_fallback_preset(agent) -> bool:
    """True if init.json has manifest.preset and active != default."""
    try:
        data = json.loads((agent._working_dir / "init.json").read_text(encoding="utf-8"))
        preset = data.get("manifest", {}).get("preset") or {}
        if not isinstance(preset, dict):
            return False
        active = preset.get("active")
        default = preset.get("default")
        return bool(active and default and active != default)
    except Exception:
        return False


def _check_rules_file(agent) -> None:
    """Consume .rules signal file, diff against system/rules.md, update if changed."""
    rules_file = agent._working_dir / ".rules"
    if not rules_file.is_file():
        return
    try:
        content = rules_file.read_text(encoding="utf-8").strip()
    except OSError:
        return
    # Always consume the signal file
    try:
        rules_file.unlink()
    except OSError:
        return
    if not content:
        return
    # Diff against canonical system/rules.md
    canonical = agent._working_dir / "system" / "rules.md"
    existing = ""
    if canonical.is_file():
        try:
            existing = canonical.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    if content == existing:
        return
    # Content changed — persist and refresh
    try:
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_text(content)
    except OSError:
        agent._log("rules_write_error", source="signal")
        return
    agent._prompt_manager.write_section("rules", content, protected=True)
    agent._flush_system_prompt()
    agent._log("rules_loaded", source="signal")
