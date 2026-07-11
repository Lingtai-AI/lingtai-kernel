---
name: email-contract
tool: email
contract_version: 1
related_files:
  - src/lingtai/tools/email/__init__.py
  - src/lingtai/tools/email/ANATOMY.md
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Email capability contract

`email` is the agent's filesystem-based mailbox: send, read, reply, search,
archive, and manage a private contact book. There is no separate `mail`
intrinsic — delivery, IMAP/SMTP bridging, and read tracking all route through
this one tool. The implementation lives in `src/lingtai/tools/email/`; the code is the
source of truth.

## Routing Card

**Use this when:**
- You are editing the internal mailbox tool: send/check/read/reply/search/
  archive/delete or the contact book.
- You are reviewing mailbox on-disk layout, unread digest republishing, or the
  duplicate-send loop guard.
- You need the ambiguous-reply (`#145`) return-route handling or the abs/peer
  send modes.

**Do not use this for:**
- Notification surface reads/dismissals: use the `notification` tool
  (`src/lingtai/tools/notification/CONTRACT.md`). The email tool only *publishes* its
  unread digest to `.notification/email.json`; it does not own dismissal verbs.
- Cron/scheduled sends: recurring sends were removed in favor of cron. The email
  tool is request/response only.
- Code navigation only: read `src/lingtai/tools/email/ANATOMY.md`.

**Fast paths:** action list -> §Tool surface; mailbox layout -> §State &
storage; abs/peer reply routing -> §Anchored claims.

## Scope

- Canonical tool name: `email`.
- `email(action='unread', ...)` is reserved for kernel-synthesized unread-mail
  digests and is rejected when invoked directly (use `check`).
- Non-goals: the standalone `notification` verbs, scheduling, external calendar/
  Gmail MCP bridges.

## Tool surface

The schema (`src/lingtai/tools/email/schema.py`) accepts `action` from a fixed enum plus
shared address/filter fields. Dispatch is `EmailManager.handle`
(`src/lingtai/tools/email/manager.py`).

| Action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `send` | `address` (str or list) | `subject`, `message`, `cc`, `bcc`, `attachments`, `delay`, `mode` (`peer`/`abs`), `type` | `{status: "sent", to, cc, bcc, delay}` | `{error: "address is required"}`; `{error: "invalid mode: ..."}`; `{error, limit_chars, actual_chars}` on body > 50k chars; `{status: "blocked", warning}` on duplicate loop |
| `check` | — | `folder` (`inbox`/`sent`/`archive`), `n`, `filter{sort,from,subject,contains,after,before,unread_only,has_attachments,truncate}` | `{status: "ok", total, showing, emails: [...]}`, plus `truncated_by_budget` when a 10k-token cap trims | (returns ok with empty list) |
| `read` | `email_id` (str or list) | `folder` | `{status: "ok", emails: [...]}`, plus `not_found` + `hint` for stale ids | `{error: "email_id is required"}` |
| `dismiss` | `email_id` (str or list) | — | `{status: "ok", dismissed: [...]}`, plus `already_handled`, `not_found`, `hint` | `{error: "email_id is required"}` |
| `reply` | `email_id`, `message` | `subject`, `cc`, `bcc` | send result (`{status: "sent", ...}`) | `{error: "email_id is required for reply"}`; `{error: "message is required for reply"}`; `{error: "Email not found: ..."}`; ambiguous-route `{error: ...}` |
| `reply_all` | `email_id`, `message` | `subject`, `cc`, `bcc` | send result (`{status: "sent", ...}`) | same as `reply` |
| `search` | `query` (regex) | `folder` | `{status: "ok", total, emails: [...]}` | `{error: "query is required for search"}`; `{error: "Invalid regex: ..."}` |
| `archive` | `email_id` (str or list) | — | `{status: "ok", archived: [...]}`, plus `not_found` + `hint` | `{error: "email_id is required"}` |
| `delete` | `email_id` (str or list) | `folder` (`inbox`/`archive`) | `{status: "ok", deleted: [...]}`, plus `not_found` + `hint` | `{error: "email_id is required"}`; `{error: "Cannot delete from folder: ..."}` |
| `contacts` | — | — | `{status: "ok", contacts: [...]}` | — |
| `add_contact` | `address`, `name` | `note` | `{status: "added"/"updated", contact}` | `{error: "address is required"}`; `{error: "name is required"}` |
| `remove_contact` | `address` | — | `{status: "removed", address}` | `{error: "address is required"}`; `{error: "Contact not found: ..."}` |
| `edit_contact` | `address` | `name`, `note` | `{status: "updated", contact}` | `{error: "address is required"}`; `{error: "Contact not found: ..."}` |

Missing/absent `action` returns `{error: "action is required"}`; an unrecognized
action returns `{error: "Unknown email action: ..."}`. `email(action='unread')`
returns an explicit reserved-action `{status: "error", message: ...}` from the
module dispatcher before reaching the manager. When `boot()` has not run,
`handle()` returns `{error: "Internal: email manager not initialized..."}`.

## State & storage

All paths are relative to the agent working directory (`agent._working_dir`).

```text
mailbox/inbox/<uuid>/message.json     — received mail (one dir per message)
mailbox/sent/<uuid>/message.json      — sent copies (send + reply/reply_all)
mailbox/archive/<uuid>/message.json   — mail moved out of inbox by archive
mailbox/read.json                     — read-tracking id set (JSON)
mailbox/contacts.json                 — contact book (list of {address,name,note})
.notification/email.json              — republished unread digest (producer-owned)
```

- `read`/`dismiss`/`reply`/`reply_all` mark inbox ids read and call
  `_rerender_unread_digest`, which rewrites `.notification/email.json`.
- `archive`/`delete` remove ids from the read set (`mailbox/read.json`) and, for
  inbox mutations, rerender the digest.
- `send` writes one `sent/<uuid>/message.json` per call (a single record even
  for multi-recipient/cc/bcc) and spawns per-recipient `_mailman` delivery
  threads; `bcc` is stored in the sent record but not exposed to recipients.
- Contacts are written atomically via a tempfile + `os.replace`.

## Cross-platform invariants

- All mailbox and contact file access is via `pathlib.Path` and
  `shutil.move`/`shutil.rmtree`; no shell-outs. DOCUMENT: contact writes use
  `tempfile.mkstemp` + `os.replace` for atomicity — `os.replace` is atomic on a
  single filesystem on both POSIX and Windows.
- Delivery runs on daemon `threading.Thread` workers (`_mailman`); no
  subprocess/PTY. DOCUMENT (do not change).
- All message JSON is read/written with `encoding="utf-8"`. DOCUMENT.
- `mode='abs'` uses the absolute `str(working_dir)` as the return address and
  embeds a `_return_route` so cross-network replies resolve unambiguously.
  DOCUMENT — this is a path-as-address assumption, not a platform behavior.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| The email intrinsic registers exactly one `email` tool (no separate `mail` intrinsic) | `src/lingtai/tools/email/__init__.py:boot` | `tests/test_layers_email.py::test_email_intrinsic_no_mail_intrinsic` |
| `send` routes through the mailman delivery thread | `src/lingtai/tools/email/manager.py:_send` | `tests/test_layers_email.py::test_email_send_through_mailman` |
| A cc'd send writes exactly one `sent/` record | `src/lingtai/tools/email/manager.py:_send` | `tests/test_layers_email.py::test_email_send_cc_one_sent_record` |
| Bodies over the 50k-char hard limit are refused at send time | `src/lingtai/tools/email/manager.py:_send` (`EMAIL_BODY_CHAR_LIMIT`) | `tests/test_layers_email.py::test_email_send_rejects_body_over_hard_limit` |
| Identical consecutive sends are blocked as a loop | `src/lingtai/tools/email/manager.py:_send` | `tests/test_layers_email.py::test_email_blocks_identical_consecutive_send` |
| `read` marks inbox mail read | `src/lingtai/tools/email/manager.py:_read` / `primitives._mark_read` | `tests/test_layers_email.py::test_email_read_marks_as_read` |
| `dismiss` marks read without returning bodies and rerenders the digest | `src/lingtai/tools/email/manager.py:_dismiss` | `tests/test_layers_email.py::test_email_dismiss_marks_read_and_returns_no_bodies`, `tests/test_layers_email.py::test_email_dismiss_rerenders_notification` |
| `archive` moves inbox mail into `archive/` | `src/lingtai/tools/email/manager.py:_archive` | `tests/test_layers_email.py::test_email_archive_moves_to_archive` |
| `delete` refuses the `sent` folder | `src/lingtai/tools/email/manager.py:_delete` | `tests/test_layers_email.py::test_email_delete_from_sent_rejected` |
| `search` compiles the query as a regex and rejects bad patterns | `src/lingtai/tools/email/manager.py:_search` | `tests/test_layers_email.py::test_email_search_invalid_regex` |
| Scheduled/recurring sends are removed from the schema and not routed | `src/lingtai/tools/email/schema.py` | `tests/test_layers_email.py::test_email_schedule_removed_from_schema`, `tests/test_layers_email.py::test_email_schedule_payload_is_not_routed` |
| Sender identity is carried on inbound mail and surfaced on read | `src/lingtai/tools/email/manager.py:_inject_identity` | `tests/test_email_identity.py` |
| Abs-mode replies resolve via `_return_route`, guarding the `#145` ambiguous self-route | `src/lingtai/tools/email/manager.py:_resolve_reply_target` | `tests/test_email_abs_reply_route.py` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| One `email` tool, no `mail` alias | `tests/test_layers_email.py::test_email_intrinsic_no_mail_intrinsic` | Boot an agent and inspect registered tools | Duplicate/ghost mail surface confuses routing |
| Read-state mutations rerender `.notification/email.json` | `tests/test_layers_email.py::test_email_dismiss_rerenders_notification` | Read a message, inspect `.notification/email.json` | Stale unread badge; dropped-mail illusion |
| Oversize bodies refused at send, not truncated at read | `tests/test_layers_email.py::test_email_send_rejects_body_over_hard_limit` | Send a >50k-char body | Oversize payloads bloat persistent notifications |
| Duplicate-send loop guard holds | `tests/test_layers_email.py::test_email_blocks_identical_consecutive_send` | Send the same message twice in a row | Runaway send loops |
| Abs replies do not self-misroute | `tests/test_email_abs_reply_route.py` | Reply to mail from a same-named agent in another network | Replies silently land in own inbox (#145) |

Run before merging email changes:

```bash
python -m pytest tests/test_layers_email.py tests/test_email_identity.py tests/test_email_abs_reply_route.py tests/test_system_dismiss.py -q
```

## Schema and glossary ownership

- **Canonical identifiers:** function names, JSON property names, action/enum
  values, required fields, defaults, and bounds are canonical English literals.
  The schema (`get_schema()`) and description (`get_description()`) are
  language-independent; the optional `lang` argument is accepted for source
  compatibility but ignored.
- **Provider wire:** provider adapters send the global `WIRE_TOOL_DESCRIPTION`
  constant as the top-level tool description; `FunctionSchema.description`
  holds the full canonical prose rendered into `## tools`.
- **Glossary resources:** this package owns `glossary-en.md`, `glossary-zh.md`,
  and `glossary-wen.md`. Each has strict YAML frontmatter
  (`kind: tool-glossary`, `schema_version: 1`, `tool_package: tools.<pkg>`,
  `language: <lang>`). English body is empty; zh/wen bodies contain concise
  terminology mappings that quote immutable English identifiers and never offer
  localized aliases.
- **Fallback:** exact normalized language lookup, then English, then no
  appendix. Fail-closed for localized text; fail-open for tool availability.
- **Update triggers:** changing a function name, action/enum value, property
  name, or user-visible concept requires reviewing all three glossary files in
  the same PR.
- **Validation:** `python -m lingtai.tools.glossary_validator --check`.
