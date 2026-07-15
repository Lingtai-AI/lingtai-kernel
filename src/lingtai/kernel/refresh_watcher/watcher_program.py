"""Core-owned renderer for the detached relaunch-watcher program source.

This module owns the exact watcher program text that used to be built
inline inside ``base_agent.lifecycle._perform_refresh``: the
``.refresh``/``.refresh.taken`` handshake poll, ACK/lock deadlines, relaunch
retry with health check, stale same-agent duplicate-process cleanup, event
redaction, and terminal-failure artifact/notification publication. Moving the
text-assembly logic here makes *rendering* it importable, directly callable
production code instead of a string literal buried in lifecycle control
flow — it does not turn the watcher's own policy into independently
executable/importable code: the returned value is still generated ``python
-c`` program *source text*, run later as a detached subprocess, not a
function this process calls. For Core-produced requests, the generated program
preserves the previously shipped runtime behavior; this slice does not claim
textual byte identity, redesign retry/heartbeat/duplicate policy, or
introduce a process-supervision Port; that remains a later slice.

The rendered program's stale same-agent duplicate-process guard
(``_is_same_agent_run``) imports the canonical Core process-command matcher,
``lingtai.kernel.process_match.match_agent_run``, at runtime via
``from lingtai.kernel.process_match import match_agent_run`` in the generated
source, rather than embedding a second local ``match_agent_run`` definition —
the same matcher ``lingtai.cli._check_duplicate_process`` already uses.

Identity fields cross the request boundary as
``RefreshWatcherRequest.identity_fields_json`` — a JSON object snapshot, not
a live dict or a shallow tuple-of-pairs — because the producer
(``runtime_identity_event_fields()``) returns a dict with a *nested* mutable
``kernel_runtime`` sub-dict (the same object as the module-level identity
cache, not a copy); no shallow container shape prevents that nested value
from staying mutable and aliased. ``_decode_identity_fields`` decodes and
validates the snapshot back to a dict, failing loudly on invalid JSON or a
non-object top-level value, before ``render_watcher_script`` embeds it as the
rendered program's ``identity_fields = {...!r}`` literal.

The one deliberate behavior change: ``_failure_metadata()`` previously bounded
and redacted only ``last_stderr_tail``; ``last_cleanup_error`` and
``last_relaunch_error`` (raw ``str(exception)`` values) passed through
unbounded and unredacted. Both are now bounded and redacted the same way as
``last_stderr_tail`` before the terminal-failure artifact/notification/event
are published.

``render_watcher_script`` is a pure function of a ``RefreshWatcherRequest``
(see ``lingtai.kernel.refresh_watcher``) to program-source text; it names no
``subprocess``, ``os``, ``os.environ``, POSIX, interpreter-path, or
environment-variable-name vocabulary and performs no OS calls itself.
Building the launched process's actual environment — capturing
``os.environ``, and applying the ``env_overwrite`` policy bit under whatever
concrete environment-variable name the transport uses — is entirely adapter
mechanism: see ``lingtai.adapters.posix.refresh_watcher.build_watcher_env``
and its ``ENV_OVERWRITE_VAR``. This module does not define or reference that
variable name; Core knows only the boolean ``request.env_overwrite`` policy
bit, never the concrete env-var transport. The POSIX adapter
(`lingtai.adapters.posix.refresh_watcher`) is the only caller that launches
the rendered text as a real detached process.
"""
from __future__ import annotations

import json

from . import RefreshWatcherRequest

MAX_ATTEMPTS = 12
HEALTH_CHECK_WAIT = 10
STDERR_TAIL_CHARS = 1200


def _decode_identity_fields(identity_fields_json: str) -> dict:
    """Decode+validate ``RefreshWatcherRequest.identity_fields_json``.

    Must parse as JSON and decode to a JSON *object* (a Python ``dict``) —
    the rendered program embeds it as a ``identity_fields = {...!r}`` literal
    merged into every logged event via ``**identity_fields``, which requires
    a mapping. Fails loudly (raises) on invalid JSON or a non-object
    top-level value, rather than silently falling back to ``{}`` and
    generating a watcher program whose event logging silently dropped the
    caller's runtime-identity fields.
    """
    try:
        decoded = json.loads(identity_fields_json)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "RefreshWatcherRequest.identity_fields_json is not valid JSON: "
            f"{identity_fields_json!r}"
        ) from exc
    if not isinstance(decoded, dict):
        raise ValueError(
            "RefreshWatcherRequest.identity_fields_json must decode to a "
            f"JSON object, got {type(decoded).__name__}: {identity_fields_json!r}"
        )
    return decoded


def render_watcher_script(request: RefreshWatcherRequest) -> str:
    """Render the complete, self-contained watcher program source.

    The returned text is a standalone Python program: it re-derives every
    value it needs (handshake paths, relaunch command, identity fields) from
    literals embedded by this function, and it imports only stdlib plus the
    kernel's redaction helper at runtime. It carries no reference to this
    process's live objects.
    """
    taken_path = request.taken_path
    lock_path = request.lock_path
    events_path = request.events_path
    stderr_log = request.stderr_log
    working_dir_str = request.working_dir
    cmd = list(request.cmd)
    agent_name = request.agent_name
    address = request.address
    identity_fields = _decode_identity_fields(request.identity_fields_json)

    return (
        "import time, subprocess, os, sys, json, signal\n"
        "from datetime import datetime, timezone\n"
        f"taken = {taken_path!r}\n"
        f"lock = {lock_path!r}\n"
        f"events = {events_path!r}\n"
        f"stderr_log = {stderr_log!r}\n"
        f"wd = {working_dir_str!r}\n"
        f"cmd = {cmd!r}\n"
        f"name = {agent_name!r}\n"
        f"addr = {address!r}\n"
        f"identity_fields = {identity_fields!r}\n"
        f"MAX_ATTEMPTS = {MAX_ATTEMPTS}\n"
        f"HEALTH_CHECK_WAIT = {HEALTH_CHECK_WAIT}\n"
        # The watcher writes events.jsonl through its own log() below, bypassing
        # the in-process CompositeLoggingService.redact_for_trajectory. Secret-
        # shaped values reach these events via stderr_tail (relaunched-process
        # stderr, e.g. a config traceback echoing a token), cmdline, and error
        # strings, so redact the whole event dict here before persisting. Use the
        # kernel's redact_for_trajectory (not just redact_text value-walking) so
        # the watcher gets the same key-aware redaction as normal trajectory
        # logging: values under secret-named keys are removed even when they do
        # not match a known token shape. The kernel redactor is the single source
        # of truth; fail open to identity if it cannot be imported so the watcher
        # never crashes over redaction, but record a non-secret marker so the
        # degradation is diagnosable rather than silent.
        "try:\n"
        "    from lingtai.kernel.trace_redaction import redact_for_trajectory as _redact_for_trajectory\n"
        "    _REDACTOR_IMPORT_OK = True\n"
        "except Exception:\n"
        "    def _redact_for_trajectory(value):\n"
        "        return value\n"
        "    _REDACTOR_IMPORT_OK = False\n"
        # Terminal-failure visibility (PR #292): when all relaunch attempts are
        # exhausted the watcher writes logs/refresh_failed_permanent.json and a
        # high-priority system notification carrying this failure_state so the
        # dead agent is diagnosable rather than silently gone. failure_state is
        # mutated in place across attempts by the relaunch loop and cleanup
        # helpers below.
        f"STDERR_TAIL_CHARS = {STDERR_TAIL_CHARS}\n"
        "failure_artifact = os.path.join(wd, 'logs', 'refresh_failed_permanent.json')\n"
        "RECOVERY_GUIDANCE = [\n"
        "    'Inspect logs/refresh_relaunch.log and logs/events.jsonl for the relaunch failure.',\n"
        "    'Run system(action=\"cpr\") or manually restart the agent after resolving the blocker.',\n"
        "    'If a duplicate PID is listed, verify it is this same agent before terminating it.',\n"
        "    'Do not delete .agent.lock by path; the kernel lock is advisory fd-based.',\n"
        "]\n"
        "failure_state = {\n"
        "    'attempts': MAX_ATTEMPTS,\n"
        "    'last_pid': None,\n"
        "    'last_duplicate_pid': None,\n"
        "    'last_relaunch_pid': None,\n"
        "    'last_heartbeat_age': None,\n"
        "    'last_heartbeat_status': 'unknown',\n"
        "    'last_stderr_tail': '',\n"
        "    'last_cleanup_action': 'not_attempted',\n"
        "    'last_cleanup_result': 'not_attempted',\n"
        "    'last_cleanup_error': None,\n"
        "    'last_relaunch_error': None,\n"
        "    'stderr_log': stderr_log,\n"
        "    'recovery_guidance': RECOVERY_GUIDANCE,\n"
        "}\n"
        "def log(typ, **kw):\n"
        "    entry = {'type': typ, 'address': addr, 'agent_name': name, 'ts': time.time(), **identity_fields, **kw}\n"
        "    if not _REDACTOR_IMPORT_OK:\n"
        "        entry['redaction_unavailable'] = True\n"
        "    else:\n"
        "        try:\n"
        "            entry = _redact_for_trajectory(entry)\n"
        "        except Exception:\n"
        "            entry = {'type': typ, 'address': addr, 'agent_name': name,\n"
        "                     'ts': entry.get('ts'), 'redaction_unavailable': True,\n"
        "                     'redaction_error': True}\n"
        "    try:\n"
        "        with open(events, 'a') as f:\n"
        "            f.write(json.dumps(entry) + '\\n')\n"
        "    except OSError:\n"
        "        pass\n"
        "def _now_iso():\n"
        "    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')\n"
        "def _bounded(text, limit=STDERR_TAIL_CHARS):\n"
        "    if text is None:\n"
        "        return ''\n"
        "    text = str(text)\n"
        "    return text[-limit:]\n"
        "def _write_json_atomic(path, payload):\n"
        "    os.makedirs(os.path.dirname(path), exist_ok=True)\n"
        "    tmp = f'{path}.tmp.{os.getpid()}'\n"
        "    with open(tmp, 'w', encoding='utf-8') as f:\n"
        "        json.dump(payload, f, ensure_ascii=False)\n"
        "        f.write('\\n')\n"
        "    os.replace(tmp, path)\n"
        "def _heartbeat_snapshot():\n"
        "    age = heartbeat_age()\n"
        "    if age is None:\n"
        "        return None, 'missing'\n"
        "    if age < 30:\n"
        "        return age, 'fresh'\n"
        "    return age, 'stale'\n"
        "def _read_stderr_tail():\n"
        "    try:\n"
        "        with open(stderr_log, encoding='utf-8', errors='replace') as f:\n"
        "            return _bounded(f.read())\n"
        "    except OSError:\n"
        "        return ''\n"
        "def _redact_bounded(text):\n"
        "    text = _bounded(text)\n"
        "    if not text:\n"
        "        return text\n"
        "    if _REDACTOR_IMPORT_OK:\n"
        "        try:\n"
        "            return _redact_for_trajectory(text)\n"
        "        except Exception:\n"
        "            return '<REDACTED:redaction-error>'\n"
        "    return '<REDACTED:redaction-unavailable>'\n"
        "def _failure_metadata():\n"
        "    meta = dict(failure_state)\n"
        "    meta['attempts'] = MAX_ATTEMPTS\n"
        "    meta['last_stderr_tail'] = _redact_bounded(meta.get('last_stderr_tail'))\n"
        "    meta['last_cleanup_error'] = _redact_bounded(meta.get('last_cleanup_error'))\n"
        "    meta['last_relaunch_error'] = _redact_bounded(meta.get('last_relaunch_error'))\n"
        "    if any(\n"
        "        v == '<REDACTED:redaction-error>' or v == '<REDACTED:redaction-unavailable>'\n"
        "        for v in (meta['last_stderr_tail'], meta['last_cleanup_error'], meta['last_relaunch_error'])\n"
        "    ):\n"
        "        meta['redaction_unavailable'] = True\n"
        "    meta['artifact_path'] = failure_artifact\n"
        "    return meta\n"
        "def _append_system_notification(meta):\n"
        "    notif_dir = os.path.join(wd, '.notification')\n"
        "    target = os.path.join(notif_dir, 'system.json')\n"
        "    current = {}\n"
        "    try:\n"
        "        with open(target, encoding='utf-8') as f:\n"
        "            current = json.load(f)\n"
        "    except (OSError, ValueError, TypeError):\n"
        "        current = {}\n"
        "    if not isinstance(current, dict):\n"
        "        current = {}\n"
        "    events_list = current.get('data', {}).get('events', [])\n"
        "    if not isinstance(events_list, list):\n"
        "        events_list = []\n"
        "    event_id = f'evt_refresh_{int(time.time()*1000):x}_{os.getpid()}'\n"
        "    body = (\n"
        "        f'Refresh failed permanently after {MAX_ATTEMPTS} attempts. '\n"
        "        'Inspect logs/refresh_relaunch.log and restart with system(action=\"cpr\") '\n"
        "        'or a manual launch after resolving the blocker.'\n"
        "    )\n"
        "    events_list.append({\n"
        "        'event_id': event_id,\n"
        "        'source': 'refresh',\n"
        "        'ref_id': 'refresh_failed_permanent',\n"
        "        'body': body,\n"
        "        'at': _now_iso(),\n"
        "        'metadata': meta,\n"
        "    })\n"
        "    events_list = events_list[-20:]\n"
        "    payload = {\n"
        "        'header': f'{len(events_list)} system notification' + ('' if len(events_list) == 1 else 's'),\n"
        "        'icon': '!',\n"
        "        'priority': 'high',\n"
        "        'published_at': _now_iso(),\n"
        "        'instructions': 'Read the refresh event metadata, inspect the relaunch log, then recover with cpr or a manual restart.',\n"
        "        'data': {'events': events_list},\n"
        "    }\n"
        "    _write_json_atomic(target, payload)\n"
        "    return event_id\n"
        "def _publish_refresh_failed_permanent():\n"
        "    meta = _failure_metadata()\n"
        "    artifact = {\n"
        "        'type': 'refresh_failed_permanent',\n"
        "        'address': addr,\n"
        "        'agent_name': name,\n"
        "        'created_at': _now_iso(),\n"
        "        'metadata': meta,\n"
        "    }\n"
        "    alert_id = None\n"
        "    alert_error = None\n"
        "    try:\n"
        "        _write_json_atomic(failure_artifact, artifact)\n"
        "    except Exception as e:\n"
        "        alert_error = str(e)\n"
        "    try:\n"
        "        alert_id = _append_system_notification(meta)\n"
        "    except Exception as e:\n"
        "        alert_error = str(e) if alert_error is None else alert_error + '; ' + str(e)\n"
        "    if alert_error:\n"
        "        log('refresh_failed_permanent_alert_error', error=_bounded(alert_error, 500),\n"
        "            artifact_path=failure_artifact)\n"
        "    else:\n"
        "        log('refresh_failed_permanent_alert_published', alert_id=alert_id,\n"
        "            artifact_path=failure_artifact)\n"
        "    return alert_id, meta\n"
        "deadline = time.time() + 60\n"
        "log('refresh_watcher_start')\n"
        "# Phase 1: wait for .refresh.taken\n"
        "while not os.path.exists(taken) and time.time() < deadline:\n"
        "    time.sleep(0.5)\n"
        "if not os.path.exists(taken):\n"
        "    log('refresh_watcher_timeout', phase='ack')\n"
        "    sys.exit(1)\n"
        "log('refresh_watcher_ack')\n"
        "# Phase 2: wait for .agent.lock to clear\n"
        "while os.path.exists(lock) and time.time() < deadline:\n"
        "    time.sleep(0.5)\n"
        "if os.path.exists(lock):\n"
        "    log('refresh_watcher_timeout', phase='lock')\n"
        "    sys.exit(1)\n"
        "# Phase 3: relaunch with health check and retry\n"
        "def heartbeat_age():\n"
        "    hb = os.path.join(wd, '.agent.heartbeat')\n"
        "    try:\n"
        "        hb_ts = float(open(hb).read().strip())\n"
        "        return time.time() - hb_ts\n"
        "    except (ValueError, OSError):\n"
        "        return None\n"
        "def is_alive():\n"
        "    age = heartbeat_age()\n"
        "    return age is not None and age < 30\n"
        "def _pid_cmd(pid):\n"
        "    try:\n"
        "        return subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='],\n"
        "            stderr=subprocess.DEVNULL, text=True).strip()\n"
        "    except Exception:\n"
        "        return ''\n"
        "def _extract_duplicate_pid(stderr_tail):\n"
        "    for line in stderr_tail.splitlines():\n"
        "        line = line.strip()\n"
        "        if not line.startswith('PID '):\n"
        "            continue\n"
        "        parts = line.split(None, 2)\n"
        "        if len(parts) >= 2 and parts[1].rstrip(':').isdigit():\n"
        "            return int(parts[1].rstrip(':'))\n"
        "    return None\n"
        "from lingtai.kernel.process_match import match_agent_run\n"
        "def _is_same_agent_run(pid):\n"
        "    if not pid or pid == os.getpid():\n"
        "        return False\n"
        "    try:\n"
        "        os.kill(pid, 0)\n"
        "    except OSError:\n"
        "        return False\n"
        "    cmdline = _pid_cmd(pid)\n"
        "    return match_agent_run(cmdline, wd) is not None\n"
        "def _cleanup_stale_duplicate(stderr_tail, attempt):\n"
        "    pid = _extract_duplicate_pid(stderr_tail)\n"
        "    failure_state['last_pid'] = pid\n"
        "    failure_state['last_duplicate_pid'] = pid\n"
        "    failure_state['last_cleanup_action'] = 'inspect_duplicate_guard'\n"
        "    if not _is_same_agent_run(pid):\n"
        "        failure_state['last_cleanup_result'] = 'skipped_not_same_agent'\n"
        "        return False\n"
        "    age = heartbeat_age()\n"
        "    failure_state['last_heartbeat_age'] = age\n"
        "    failure_state['last_heartbeat_status'] = 'fresh' if age is not None and age < 30 else ('stale' if age is not None else 'missing')\n"
        "    if age is not None and age < 60:\n"
        "        log('refresh_watcher_duplicate_alive', attempt=attempt, pid=pid, heartbeat_age=age)\n"
        "        failure_state['last_cleanup_result'] = 'skipped_fresh_heartbeat'\n"
        "        return False\n"
        "    log('refresh_watcher_stale_duplicate_terminate', attempt=attempt, pid=pid,\n"
        "        heartbeat_age=age, cmdline=_pid_cmd(pid)[-300:])\n"
        "    failure_state['last_cleanup_action'] = 'terminate_stale_duplicate'\n"
        "    try:\n"
        "        os.kill(pid, signal.SIGTERM)\n"
        "    except OSError as e:\n"
        "        log('refresh_watcher_stale_duplicate_term_error', attempt=attempt,\n"
        "            pid=pid, error=str(e))\n"
        "        failure_state['last_cleanup_result'] = 'sigterm_error'\n"
        "        failure_state['last_cleanup_error'] = str(e)\n"
        "        return False\n"
        "    deadline = time.time() + 5\n"
        "    while time.time() < deadline:\n"
        "        try:\n"
        "            os.kill(pid, 0)\n"
        "        except OSError:\n"
        "            log('refresh_watcher_stale_duplicate_gone', attempt=attempt, pid=pid)\n"
        "            failure_state['last_cleanup_result'] = 'terminated'\n"
        "            return True\n"
        "        time.sleep(0.2)\n"
        "    try:\n"
        "        os.kill(pid, signal.SIGKILL)\n"
        "        log('refresh_watcher_stale_duplicate_killed', attempt=attempt, pid=pid)\n"
        "        failure_state['last_cleanup_result'] = 'sigkill_sent'\n"
        "        return True\n"
        "    except OSError as e:\n"
        "        log('refresh_watcher_stale_duplicate_kill_error', attempt=attempt,\n"
        "            pid=pid, error=str(e))\n"
        "        failure_state['last_cleanup_result'] = 'sigkill_error'\n"
        "        failure_state['last_cleanup_error'] = str(e)\n"
        "        return False\n"
        "for attempt in range(1, MAX_ATTEMPTS + 1):\n"
        "    # Check if already alive before relaunching\n"
        "    if is_alive():\n"
        "        log('refresh_watcher_already_alive', attempt=attempt)\n"
        "        sys.exit(0)\n"
        "    # Clean signal files so the new process boots cleanly (like CPR)\n"
        "    for sig in ('.suspend', '.sleep', '.interrupt'):\n"
        "        try:\n"
        "            os.unlink(os.path.join(wd, sig))\n"
        "        except OSError:\n"
        "            pass\n"
        "    log('refresh_watcher_relaunch', attempt=attempt)\n"
        "    try:\n"
        "        with open(stderr_log, 'a') as serr:\n"
        "            serr.write(f'--- relaunch attempt {attempt} ---\\n')\n"
        "            serr.flush()\n"
        "            proc = subprocess.Popen(cmd,\n"
        "                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
        "                stderr=serr, start_new_session=True)\n"
        "    except Exception as e:\n"
        "        log('refresh_watcher_relaunch_error', attempt=attempt, error=str(e))\n"
        "        hb_age, hb_status = _heartbeat_snapshot()\n"
        "        failure_state['last_heartbeat_age'] = hb_age\n"
        "        failure_state['last_heartbeat_status'] = hb_status\n"
        "        failure_state['last_stderr_tail'] = _read_stderr_tail()\n"
        "        failure_state['last_cleanup_action'] = 'not_applicable'\n"
        "        failure_state['last_cleanup_result'] = 'launch_error'\n"
        "        failure_state['last_cleanup_error'] = None\n"
        "        failure_state['last_relaunch_error'] = str(e)\n"
        "        if attempt < MAX_ATTEMPTS:\n"
        "            time.sleep(HEALTH_CHECK_WAIT)\n"
        "        continue\n"
        "    log('refresh_watcher_relaunched', attempt=attempt, pid=proc.pid)\n"
        "    failure_state['last_relaunch_pid'] = proc.pid\n"
        "    failure_state['last_relaunch_error'] = None\n"
        "    # Wait for the new process to start writing heartbeat\n"
        "    time.sleep(HEALTH_CHECK_WAIT)\n"
        "    hb = os.path.join(wd, '.agent.heartbeat')\n"
        "    if os.path.exists(hb):\n"
        "        try:\n"
        "            hb_ts = float(open(hb).read().strip())\n"
        "            if time.time() - hb_ts < HEALTH_CHECK_WAIT + 10:\n"
        "                log('refresh_watcher_success', attempt=attempt, pid=proc.pid)\n"
        "                sys.exit(0)\n"
        "        except (ValueError, OSError):\n"
        "            pass\n"
        "    # Process not alive — log failure and retry\n"
        "    stderr_tail = ''\n"
        "    try:\n"
        "        with open(stderr_log, encoding='utf-8', errors='replace') as f:\n"
        "            lines = f.readlines()\n"
        "            stderr_tail = ''.join(lines[-20:])\n"
        "    except OSError:\n"
        "        pass\n"
        "    hb_age, hb_status = _heartbeat_snapshot()\n"
        "    failure_state['last_heartbeat_age'] = hb_age\n"
        "    failure_state['last_heartbeat_status'] = hb_status\n"
        "    failure_state['last_stderr_tail'] = _bounded(stderr_tail)\n"
        "    failure_state['last_cleanup_action'] = 'not_applicable'\n"
        "    failure_state['last_cleanup_result'] = 'no_duplicate_guard'\n"
        "    failure_state['last_cleanup_error'] = None\n"
        "    log('refresh_watcher_relaunch_dead', attempt=attempt, pid=proc.pid,\n"
        "        stderr_tail=stderr_tail[-500:])\n"
        "    if 'another lingtai agent is already running' in stderr_tail:\n"
        "        _cleanup_stale_duplicate(stderr_tail, attempt)\n"
        "alert_id, meta = _publish_refresh_failed_permanent()\n"
        "log('refresh_failed_permanent', alert_id=alert_id, **meta)\n"
    )


__all__ = ["render_watcher_script", "MAX_ATTEMPTS", "HEALTH_CHECK_WAIT", "STDERR_TAIL_CHARS"]
