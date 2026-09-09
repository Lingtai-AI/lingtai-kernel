---
name: web-manual-operation-contract
description: >
  Exact web action contract for unfamiliar or consequential calls: references and
  cursors, strict settings, complete delivery, provider routing, and public URL
  boundaries.
version: 2.0.0
last_changed_at: "2026-09-09T10:53:00Z"
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/__init__.py
  - src/lingtai/tools/web_search/settings.py
  - src/lingtai/tools/web_search/_spill.py
  - src/lingtai/tools/browser/core.py
  - src/lingtai/tools/web_search/CONTRACT.md
  - tests/test_unified_web_capability.py
  - tests/test_web_output_spill.py
  - tests/test_web_settings_action.py
maintenance: |
  This reference owns exact Web procedure semantics. Keep it aligned with the
  schema, settings readers, delivery helper, BrowserEngine, and parent router;
  do not duplicate its rules into a second action or silently weaken complete
  output, no-fallback, same-Agent reference, or public-URL guarantees.
---
# Web operation contract

Use this after the short [web-manual](../SKILL.md) route when exact behavior
matters. It documents existing actions; it adds no action or capability.

## Action envelope and first-call rules

The closed root is required `action`, `input`, and `reasoning`, plus optional
boolean `summarize` (default `false`). Action branches are strict: `search`
input is exactly `{"query":"..."}`; `settings` and `manual` input are `{}`;
browse has required `url`, `link_ref`, `cursor`, `extract`, and `max_chars`,
with unused optionals represented as JSON `null`; `extract` accepts only
`article` or null. `summarize` is root-only and failed results remain exact and
unsummarized.

Search returns the selected provider's complete finite result list with no
LingTai count cap or per-field slice. Results are `{title,url,snippet}` plus a
same-Agent `link_ref` iff `url` is usable HTTP(S). A provider narrative with no
citation is retained once as `url:""`, `link_ref:null`; no URL is invented.
Search does not fetch pages or accept an action-level `engine`.

Browse accepts exactly one public HTTP(S) URL or same-Agent `link_ref`. A
`cursor` may accompany that same target to locate its cached snapshot; it is
not a third target, cannot work alone, and never requests another page. It
returns an existing snapshot without a network refetch, not newly fetched data. Fresh
and cursor-bearing calls return the complete extracted document, never a first
page; fresh success never mints `next_cursor`. A stale or cross-Agent reference,
invalid cursor, target mismatch, or evicted snapshot fails with its typed error
(`BROWSE_SNAPSHOT_UNAVAILABLE` for the delivery-time eviction case).

Browse is a static, read-only, SSRF/DNS-vetted HTTP GET. It preserves
provenance, source hash, links, and untrusted-content marking. It does not
execute JavaScript, extract PDFs, log in, use cookies/forms, or invoke search
as a hidden fallback.

## Settings and manual action

`web(action="settings", input={}, reasoning="inspect effective web settings")`
is SHOW only. Success is exactly `{"settings":[...]}` with rows containing
only `key`, `current`, `default`, `configurable`, and `comment`; all credential
values are `<redacted>`. `configurable:true` points to an authorized external
procedure and grants no write access. No set/reset/writer, receipt, or process
environment mutation exists. An unavailable source returns one bounded
`SETTINGS_UNAVAILABLE` failure with no partial rows.

The nine parent anchors route to these owner sections. SHOW fails as one
`SETTINGS_UNAVAILABLE` result if current engine/output truth is unavailable;
there are no partial rows. An admitted but credential-missing engine may still
appear as selected in SHOW: availability and backend eligibility are checked by
search, so a successful SHOW is not a connectivity or usable-provider test.

### provider
`provider` reports applied singular flat `provider=` composition: `automatic`
for true no-config setup, or `null` for multi-engine/injected/explicit-default
composition without a singular flat value. Its displayed default remains
`automatic`, not `null`. Authorized launcher/manifest or embedding `setup()`
changes require recomposing/relaunching the owner. There is no
`LINGTAI_WEB_PROVIDER` peer; the hot engine selector does not rewrite this row.

### model
`model` reports applied flat `model=` or `provider-default`; ambiguous composition
has `current:null` but default stays `provider-default`. No `LINGTAI_WEB_MODEL`
peer exists. Change only through authorized Web composition and recompose/relaunch,
not SHOW or the engine selector.

### api-key
The flat `api_key`/`api_key_env` route is a setup snapshot. Both current and
default are always `<redacted>`; there is no generic `LINGTAI_WEB_API_KEY`.
Change only the authorized private launcher/secret store or Web composition,
then recompose/relaunch. Never place credentials in requests, prompts, reports
or either settings document.

### engines
`engines` reports sorted immutable admitted names. Displayed no-config default
is `anthropic`, `duckduckgo`, `gemini`, `openai`; admission is not availability.
The map has no environment peer and the selector cannot install/add an engine.
An authorized composition edit requires recomposing/relaunching the owner.

### search-engine
`search.engine` is read on each search/SHOW: `LINGTAI_WEB_ENGINE`, then strict
`<agent-workdir>/settings/web.search.json`, then the composed runtime fallback.
The exact file is `{"schema_version":1,"engine":"<admitted>"}`. The displayed
default is that composed fallback, not necessarily DuckDuckGo. If no fallback
can be selected (for example only settings-gated engines without a selector),
SHOW returns `SETTINGS_UNAVAILABLE` rather than a partial row with null engine.
Invalid env/file selection fails without falling through; unavailable or
credential-missing selections fail search. Browse/manual never read this file.
After an authorized env/file edit, the next search/SHOW reads it; verify both
SHOW and a permitted search. Changing launcher environment requires its own
relaunch procedure when the running process cannot see the edit.

### output-max-chars
`output.max_chars` is shared by search/browse and hot-read by SHOW:
`LINGTAI_WEB_MAX_CHARS`, then `<agent-workdir>/settings/web.json`, then `50000`.
The exact file is `{"schema_version":1,"max_chars":<integer 1..100000>}`;
booleans, unknown/duplicate keys, malformed/wrong-version/non-integer/out-of-range,
symlink/non-regular/oversize/unstable/bad-UTF-8 owner files fail loudly before
provider/fetch work. Present invalid env also fails. Search/output files are
separate, never merged; manual reads neither.

Browse validates the shared output setting **before applying** its nullable
per-call `max_chars` override: an override cannot rescue an invalid shared source.
A valid override changes only that call's delivery and does not change SHOW;
its source/revision is `call_override`, hash null. Authorized env/file edits
apply on the next applicable call; SHOW again to verify. This changes no access
policy. Search has no per-call threshold input.

### openai-api-key
`credentials.openai_api_key` current/default are redacted. True no-config setup
uses `OPENAI_API_KEY`, never the Agent's LLM service credentials. Before lazy
construction the engine's declared env route is read live; afterward its
cached service and initialization-failure state persist for that manager.
Changing a key does not replace a cached service: recompose/relaunch through
the authorized owner procedure, then verify SHOW redaction and a permitted
search. SHOW cannot prove that a replacement key is in use.

### anthropic-api-key
Same redaction, authorized private change and cached service lifetime as above;
the canonical route is `ANTHROPIC_API_KEY`. Credentials do not bypass explicit
hot selection or canonical Anthropic backend eligibility.

### gemini-api-key
Same redaction, authorized private change and cached service lifetime as above;
the canonical route is `GEMINI_API_KEY`. Credentials do not bypass explicit hot
selection or canonical Gemini backend eligibility.

The zero-input manual action reads the installed `capabilities/web/SKILL.md`
and performs no provider construction, network request, or settings read. It
works even when settings are malformed; a missing installation is a truthful
degraded result and non-empty input is `INVALID_ARGUMENT`.

## Output size and complete artifacts

Search and browse construct complete canonical content before comparing the
threshold. Search measures its rendered JSON result list; browse measures the
JSON serialization of the complete structured `blocks` array, not the smaller
joined-text artifact. At or below the threshold, return complete content with
`delivery:"inline"` and exact `content_chars`. Above it, atomically write the
complete content under workdir-relative `tmp/tool-results/` and return
`delivery:"artifact"`, the Web artifact marker, relative `file_path`, exact
`content_chars`/`content_sha256`, format/encoding, content scope/kind, and
output-setting source/revision/hash. Search omits `results`; browse omits
`blocks`, `partial`, `next_cursor`, and `returned_chars`. There is no preview
or lossy prefix; read the artifact with `file.read`, continuing from `next_offset`
until complete (handle long-line truncation via the File read manual). Completeness
is provider-response/extracted-document scope, not the entire web/site or rendered
JS page; link metadata is separately bounded and may carry truncation warnings.

For browse, the artifact file is complete joined plain text, so an envelope may
also report `delivery_decision_chars` and
`delivery_decision_basis:"structured_blocks"`; `content_chars` still describes
only the file. An atomic write failure is `ARTIFACT_WRITE_FAILED`, never an
inline truncation. Web's marker is recognized by the shared artifact guard, so
the generic preventive spill does not re-spill this envelope.

## Provider routing and explicit fallback

Admission is exactly `openai`, `anthropic`, `gemini`, and `duckduckgo`.
Anthropic/Gemini are active settings-only opt-ins: a valid hot selection also
requires exact matching canonical Agent backend identity. Aliases, Claude Code,
`custom`, OpenRouter, and other wire-compatible names fail
`PROVIDER_BACKEND_INELIGIBLE`. Composition kwargs selecting them raise
`SettingsOnlyProviderError`; retired MiniMax/Zhipu raise
`RetiredProviderError`. No provider selection infers another Agent's service
or credential.

In true no-config setup, canonical OpenAI is the live default when its standard
credential route is available; otherwise DuckDuckGo. Custom composition remains
bounded by its admitted set and declared default; a settings-gated-only set can
have no default. Injected services can also make a composed engine available.
Only typed `OpenAISearchError` on the selected `openai` engine triggers exactly
one DuckDuckGo runtime attempt. This fallback constructs the no-key DDG service
if no injected DDG service exists, even when DDG is absent from the admitted map.
A successful attempt retains selected `engine:"openai"`, actual
`actual_engine:"duckduckgo"`, a comment, and bounded failure provenance. If
that attempt fails, return `SEARCH_FAILED` with both bounded classes. Typed
non-OpenAI failures and programming exceptions fail without retry; raw SDK
text, request bodies, credentials, and exception details never surface. A
successful empty provider response is `[]`, not an error.

The separate compatibility path maps a genuinely unrecognized/inherited legacy
provider name to one DuckDuckGo spec tagged `legacy_fallback_from` before a
call. It does not apply to deliberately retired names and is not a runtime
retry.

## One explicit recovery route and public boundary

If static browse reports typed unsupported content or `NO_TEXT_BLOCKS`, choose
one named, authorized procedure: Tier 0 for a PDF, a source-specific API for
structured data, or the documented Playwright/academic route. Use the compact
[tier index](tier-quick-refs/SKILL.md) and do not silently chain routes or
advertise them as `web` actions.

For a documented public HTTP fallback only, if `curl` is empty or clearly
incomplete, retry once with a crawler User-Agent such as `OAI-SearchBot`,
`Claude-User`, or `Bytespider`. Keep it public, read-only, rate-respecting,
and limited to the page's public representation. Never impersonate a person,
bypass login, paywall, robots directives, CAPTCHA, or access control; never
chain identities. Forms, logins, JS-heavy SPAs, uploads, and human verification
belong to [agent-native browser](agent-native-browser.md), not `web browse`.
