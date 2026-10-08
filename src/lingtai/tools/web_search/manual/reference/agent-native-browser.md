---
name: web-manual-agent-native-browser
description: >
  Separate interactive Chrome DevTools MCP route for forms, login, SPA pages,
  uploads, and human verification that static Web browse intentionally cannot do.
version: 2.0.0
last_changed_at: "2026-09-09T10:53:00Z"
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-3-playwright.md
maintenance: |
  Keep this separate-channel route aligned with the current Chrome DevTools MCP
  and mcp-manual guidance. It is not a Web action; preserve human consent,
  profile isolation, and no-password-in-chat boundaries.
---
# Interactive browser route

Use this instead of `web browse` for forms, login/SSO, JavaScript-heavy SPAs,
uploads, submission verification, or any task requiring a real browser. `web`
browse is static read-only HTTP GET and does not execute JavaScript, use cookies,
log in, or submit forms.

Use the authorized Chrome DevTools MCP registration and a dedicated lightweight
profile, never the user's real heavy profile. A human must type passwords and
complete SSO/CAPTCHA directly; do not request or store credentials in chat.
Check the current `mcp-manual` for registration and installation procedure;
this page grants neither.

## Safe interaction loop

`list_pages` → `navigate_page` → fresh `take_snapshot` → `fill_form`/`click` →
new snapshot or `evaluate_script` to verify → screenshot only when proof is
needed. Tools in this channel use their own flat arguments, not Web's
`action`/`input` envelope. Use the latest snapshot; never trust a click blindly.

For SPA forms, prefer `fill_form` for new forms. If editing an existing
Angular/React field leaves derived values unchanged, use the native prototype value setter through `evaluate_script`, then dispatch
bubbling `input` and `change` events and blur the field. Re-read derived values
after saving; never treat DOM text alone as application-state proof. Uploads require a readable path in the explicitly authorized location;
never move or expose a private file merely to make an upload work.

Keep the profile/session and uploads within that authorized location. Stop and
ask the human when access, consent, payment, CAPTCHA, or a protected resource
is required. Do not bypass login, paywall, robots, or access control. For pure
headless public scraping, use the [Tier 3](tier-3-playwright.md) procedure
instead.

Use the [Chrome DevTools MCP upstream guide](https://github.com/ChromeDevTools/chrome-devtools-mcp)
for current `--user-data-dir`/profile and tool schemas. For a large accessibility
snapshot, use `take_snapshot`'s supported `filePath` output instead of repeatedly
injecting the whole tree. These are MCP operations, not new Web inputs.
