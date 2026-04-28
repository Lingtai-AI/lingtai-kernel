# IMAP Addon Hardening — Full Rewrite on `imapclient`

**Status:** approved 2026-04-28
**Scope:** `src/lingtai/addons/imap/` and its tests
**Driver bug:** new mail sometimes triggers the listener and sometimes does not, even though `imap(action="check")` confirms the message is sitting in INBOX

## 1. Goals & non-goals

### Goals
- New mail arrives → agent gets notified, **reliably**, including when humans concurrently read on phone/web.
- Survive long-lived connection failures: NAT timeouts, server logouts at 30 min, transient network blips.
- Status accurately reflects connection state — no more `connected: false` while working.
- All existing `imap(action=...)` tool surfaces (`check`, `read`, `send`, `reply`, `search`, `move`, `delete`, `flag`, `folders`, `accounts`, contacts) keep working without behavioral change visible to agents.

### Non-goals
- No CONDSTORE / QRESYNC. Overkill — we don't sync flag state, just deliver new arrivals.
- No async / asyncio rewrite. Stay sync; pairs with the existing thread-based addon model.
- No SMTP rewrite. The bugs are on the IMAP/listener side; SMTP via `smtplib` works fine.
- No protocol changes to `manager.py`'s tool surface (`_accounts()` response shape changes only).

## 2. Why

The current implementation has three structural problems that combine to produce the observed "sometimes wakes, sometimes not" symptom:

1. **`SEARCH UNSEEN + _processed_uids` set** is the wrong delivery model when humans share the inbox. If the human reads a message on their phone before the listener wakes, `SEARCH UNSEEN` returns nothing and the message is lost forever (no future SEARCH will surface it).
2. **Hand-rolled IDLE on top of `imaplib`** speaks raw socket bytes (`imap.send`, `imap.readline`, `select.select`) and breaks on the first untagged response. Server keep-alives (`* OK Still here`) and unrelated mailbox events (EXPUNGE, RECENT) cause spurious wake-and-no-mail cycles. Half-dead sockets that don't raise errors silently never reconnect.
3. **No `UIDVALIDITY` check, no IDLE re-issue cap, no NOOP keep-alive.** Gmail will silently drop IDLE around the 30-minute mark; NAT routers will silently drop idle TCP earlier. Either way, the next "new mail" event lands in the void.

`imaplib` is a low-level protocol library. The IMAP ecosystem has solved these problems in `imapclient`, which is sync (matches our threading model), pure-Python, MIT-licensed, ~10 years mature, and used by Mailpile / archivebox / Gmvault.

## 3. Architecture

Two threads per `IMAPAccount` (same shape as today, replaced implementation):

```
IMAPAccount
├── Tool-call connection  (imapclient.IMAPClient, lock-protected)
│   └── used by manager.py for action="check"/"read"/"send" etc.
└── Listener thread
    ├── Listener connection  (imapclient.IMAPClient, dedicated)
    └── Loop:  IDLE 9 min slice  →  drain  →  reconcile UIDs  →  back to IDLE
                  ↑  every 29 min total: full re-issue                │
                  └────────── reconnect on any error ─────────────────┘
```

Two connections, same as today. The driver decision is to use a real IMAP library instead of raw socket I/O.

## 4. New-mail detection — UIDNEXT watermark

Replace `SEARCH UNSEEN + _processed_uids` with a per-(account, folder) **UIDNEXT watermark**.

State persisted to `working_dir/imap/<account>.state.json`:

```json
{
  "INBOX": {
    "uidvalidity": 12345678,
    "last_delivered_uid": 4821
  }
}
```

### Reconcile algorithm

On each reconcile (called after every EXISTS event, on every (re)connect, and after every NOOP):

1. `EXAMINE INBOX` → returns `UIDVALIDITY` and `UIDNEXT`.
2. **UIDVALIDITY mismatch** (state file's value differs from server's) → mailbox was renumbered server-side. Reset state for this folder, set watermark to current `UIDNEXT`, log a warning, deliver nothing this round. Old UIDs are no longer meaningful.
3. **No state file yet** (first launch, or post-migration reset) → bootstrap path. Read `UIDNEXT`, set `last_delivered_uid = UIDNEXT - 1`, persist. Deliver nothing this round. **First-launch carve-out:** to give a sensible UX for agents that already had unread mail before the upgrade, the bootstrap path additionally fetches messages with the `\Unseen` flag and delivers them once — this is the *only* place SEEN state is consulted, and it runs at most once per (account, folder) per state-file lifetime. Acknowledged tradeoff: any *read* mail older than this point will be missed; new mail (delivered after bootstrap completes) is never missed.
4. **Normal path:** `SEARCH UID <last_delivered_uid+1>:*` → fetch headers for those UIDs.
5. Deliver each new header through the `on_message` callback.
6. `last_delivered_uid = max(uids)`. Persist atomically (`os.replace` of a tmp file).

This kills the SEEN-flag race entirely — the SEEN flag is no longer in the delivery path.

### Bootstrap & migration

On first launch after upgrade, no state file exists in the new schema → step 3 above runs. The migration module deletes any pre-existing state file from the old schema (detected by *absence* of the `last_delivered_uid` key), idempotent, logged once.

## 5. Listener loop robustness

```
while not stop_event.is_set():
    try:
        connect_listener()                  # imapclient.IMAPClient + login + select INBOX
        reconcile_on_connect()              # catch up anything that arrived while we were down
        cycle_deadline = monotonic() + 29*60
        imap.idle()
        while monotonic() < cycle_deadline:
            if stop_event.wait(0): break
            responses = imap.idle_check(timeout=540)   # ~9 min slice
            # responses is a list of tuples; relevant shapes:
            #   (msg_num, b'EXISTS')   — new mail (or any mailbox size change)
            #   (msg_num, b'RECENT')   — server-side "recent" counter changed
            #   (msg_num, b'EXPUNGE')  — message removed (we ignore for delivery)
            #   (b'OK', b'Still here') — keep-alive
            interesting = [r for r in responses
                           if len(r) >= 2 and r[1] in (b'EXISTS', b'RECENT')]
            if interesting:
                imap.idle_done()
                reconcile()
                imap.idle()
            # else: keep-alive, EXPUNGE, or empty → stay in IDLE
            else:
                # slice expired with silence — probe the socket
                imap.idle_done()
                imap.noop()                  # raises if socket is half-dead
                imap.idle()
        imap.idle_done()                     # 29-min cap → full re-issue
    except (socket.error, IMAPClient.Error, OSError, imapclient.exceptions.IMAPClientError) as e:
        log.warning("listener error on %s: %s", account, e)
        try: imap.logout()
        except Exception: pass
        backoff()                            # 1, 2, 5, 10, 60s capped, never advances on success
        # outer loop reconnects
```

Key changes vs current code:

- **9-minute IDLE slices, 29-minute hard cap.** RFC-compliant, NAT-tolerant, Gmail-friendly.
- **NOOP keep-alive when slice expires silently.** Detects half-dead sockets that `idle_check` cannot.
- **`reconcile_on_connect` runs on every (re)connect.** Anything that arrived during downtime gets delivered. Today's code can lose mail across reconnect windows.
- **Drain all `idle_check` responses, not just the first line.** Current code breaks on the first untagged byte, even keep-alives. New code processes the full response list and only acts on EXISTS / RECENT / EXPUNGE.
- **`stop_event` is checked with timeout, never with raw `time.sleep(1)` loops.** Already true today; preserved.

## 6. Connection state — fixing the "connected: false" lie

`connected` becomes a real check, not a `None` test:

```python
@property
def connected(self) -> bool:
    if self._tool_imap is None:
        return False
    try:
        with self._lock:
            self._tool_imap.noop()
        return True
    except Exception:
        return False
```

`_accounts()` action returns three booleans per account:

| field | meaning |
|---|---|
| `tool_connected` | tool-call connection alive (NOOP succeeded) |
| `listener_connected` | listener thread alive AND its IMAP connection responsive |
| `listening` | listener thread is in IDLE right now (vs reconnecting / backoff) |

The "known display bug" caveat in `manual/SKILL.md` is removed.

## 7. File / module layout

```
src/lingtai/addons/imap/
├── __init__.py        unchanged surface (setup() signature stable)
├── service.py         unchanged surface (IMAPMailService)
├── manager.py         minor — _accounts() returns the new richer status dict
├── account.py         REWRITE — imapclient-based, UID watermark, robust loop
├── _watermark.py      NEW — small module: state load/save, UIDVALIDITY reset
├── _migrate.py        NEW — one-shot deletion of pre-rewrite state files
├── manual/SKILL.md    minor — drop the "known display bug" caveat
└── config.example.json unchanged
```

**Why split out `_watermark.py` and `_migrate.py`:** the two pieces with the trickiest correctness concerns (atomic persistence, migration once-and-only-once) are isolated into ~80-line files with their own unit tests. Without the split, `account.py` stays 1100+ lines of mixed concerns. Each module answers one question:

- `_watermark.py`: where is the next message we owe the agent?
- `_migrate.py`: have we cleaned up the legacy state file yet?
- `account.py`: how do we stay connected and turn IMAP events into `on_message` calls?

## 8. Tests

The existing `tests/test_addon_imap_account.py` (1009 lines) is heavily coupled to `imaplib` mocks and will be rewritten wholesale. Replacement layers:

1. **Pure unit tests** for `_watermark.py` and `_migrate.py` — no IMAP at all. Atomicity, UIDVALIDITY reset semantics, missing-file handling, idempotent migration.
2. **Mocked-`IMAPClient` tests** for `account.py` — mock `imapclient.IMAPClient` at the class level, drive the listener loop with synthetic IDLE responses. Must hit:
   - First-connect bootstrap (no state file → watermark = UIDNEXT, no flood)
   - UIDVALIDITY change → reset path (warning logged, watermark reset, no delivery)
   - EXISTS response → reconcile fires, watermark advances
   - NOOP keep-alive failure → reconnect fires
   - `idle_check` raising socket error → reconnect fires
   - 29-minute cap → graceful re-issue without losing pending events
   - `stop_event.set()` during IDLE → loop exits cleanly within the 9-min slice
   - Backoff progression: 1, 2, 5, 10, 60, 60... resets after successful idle
3. **One end-to-end smoke test** against a real Gmail test inbox, gated behind `IMAP_LIVE_TEST=1` env var. Skipped by default in CI; runnable locally before release.

`tests/test_addon_imap_manager.py` (469 lines) keeps its structure — manager surface barely changes, only `_accounts()` response shape.

## 9. Migration & rollout

1. Add `imapclient>=3.0` to `pyproject.toml` deps. (Pure Python, MIT license, ~1.6k stars, 10+ years stable.)
2. Ship the rewrite alongside `_migrate.py` that, on addon load, deletes any `working_dir/imap/<account>.state*` file matching the legacy schema (detected by absence of `last_delivered_uid` key). Logs a single line per migration. Idempotent on subsequent loads.
3. First-launch behavior: any current INBOX UNSEEN delivered once at startup, watermark set to current `UIDNEXT`, normal mode from then on.
4. Bump kernel version (minor — no API breaks); add changelog entry pointing at this spec.

## 10. Risks & open questions

- **`imapclient` dependency**: pure-Python, 10y mature, used by major projects. Low risk. Adds one dep — acceptable for an addon that is itself opt-in.
- **Multi-folder watching**: today only INBOX is watched. Spec keeps this. The watermark dict is keyed by folder name, so adding Drafts / Sent watching later is mechanical.
- **Atomicity on Windows**: `os.replace` is atomic on POSIX and Windows. Confirmed.
- **Test-suite churn**: 1009 lines of tests rewritten. Necessary cost of switching the underlying library; the mocks were `imaplib`-shaped and won't survive.
- **Listener thread join timeout**: today's `stop_listening` joins for 10 seconds. With 9-minute IDLE slices the slice itself can take 9 minutes to exit. Mitigation: `idle_check(timeout=540)` is interruptible by `idle_done()`, which we call from the cleanup path; outer loop checks `stop_event` after every slice. Net effect: stop completes within ~1 second in practice. Verified by test.

## References

- [RFC 2177 — IMAP4 IDLE command](https://datatracker.ietf.org/doc/html/rfc2177)
- [IMAPClient 3.0 advanced docs (IDLE pattern)](https://imapclient.readthedocs.io/en/3.0.1/advanced.html)
- [Mozilla bug 468490 — IDLE does not reconnect after server timeout](https://bugzilla.mozilla.org/show_bug.cgi?id=468490)
- [dev.to — UIDNEXT watermark for "new since last check"](https://dev.to/kehers/imap-new-messages-since-last-check-44gm)
- [Home Assistant issue 86407 — IMAP delays & Gmail timeout discussion](https://github.com/home-assistant/core/issues/86407)
- [Limilabs — IMAP IDLE notes on NAT & keepalive](https://www.limilabs.com/blog/imap-idle)
