---
name: web-manual-agent-native-browser
description: >
  Agent-native browser automation with chrome-devtools-mcp: real Chrome over
  CDP, dedicated lightweight profile, snapshot-first workflow, SPA form
  filling (fill_form + JS native setter for Angular), receipt uploads, and
  when to choose it over static browse.
version: 1.0.1
last_changed_at: "2026-09-04T00:00:00Z"
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-3-playwright.md
maintenance: |
  Keep this bundled web-search reference synchronized with chrome-devtools-mcp
  releases and with LingTai MCP registration mechanics (mcp-manual).
---

# Agent-native browser automation (chrome-devtools-mcp)

> Part of the [web-manual](../SKILL.md) skill.

**When it applies:** forms, logins, JS-heavy SPAs (Angular/React), uploads, or
anything that needs real interaction — the `web` browse action is static,
read-only HTTP GET and explicitly does **not** handle JavaScript, PDF, login,
cookies, or forms. When a task needs an actual browser session, use
chrome-devtools-mcp (Google's official Chrome DevTools MCP) instead of
browser automation from scratch.

**Why agent-native:** chrome-devtools-mcp drives **real Chrome** over CDP
against a dedicated lightweight profile (see §2 below — not the user's actual
heavy default profile, which cold-starts too slowly for the MCP tool
timeout). Cookies and SSO sessions still survive between turns within that
dedicated profile, so a human can log in once there (SSO/Duo) and the agent
continues. It is the tested
first choice over playwright-mcp / browser-use / stagehand / steel for
interactive work; playwright scripts remain the fallback for pure headless
scraping (see [tier-3-playwright.md](./tier-3-playwright.md)).

## 1. Setup

```bash
npm i -g chrome-devtools-mcp
```

Then register it as a LingTai MCP server (three pieces):

1. `mcp_registry.jsonl` — the registry entry (name, transport `stdio`,
   source, homepage).
2. `init.json` — an `mcp.<name>` activation entry pointing at the server
   (see mcp-manual for exact field names).
3. `system(action="refresh")` — load the new MCP surface.

## 2. The dedicated-profile fix (CRITICAL)

Launch chrome-devtools-mcp against a **dedicated lightweight profile**, not the
user's real Chrome profile:

- Real profile (often GBs) cold-starts slower than the MCP tool timeout
  (>120s), which leaves Chrome in a stuck `browser already running` state.
- A dedicated profile at e.g. `~/.lingtai/chrome-agent-profile` launches in
  ~500ms and is stable.

Set `--user-data-dir` (or the MCP config equivalent) to the dedicated path.
This profile can still be the one where the human performs SSO login once;
the session persists across navigations.

## 3. Core workflow (snapshot-first)

1. `list_pages` — see what tabs exist (pages have numeric ids).
2. `navigate_page` (type `url`, url) — go to the target.
3. `take_snapshot` — get the accessibility tree with `uid`s for every
   element. Always use the latest snapshot; prefer snapshot over screenshot
   for reading structure.
4. Act: `click` (uid), `fill_form` (uid+value list for new forms), `fill`
   (uid, value for simple inputs), `type_text`, `press_key`, `select_page`.
5. Verify: `evaluate_script` or another `take_snapshot`; never trust a click
   blindly.
6. `take_screenshot` (filePath) — save a visual when a human needs to see it.

```text
list_pages -> navigate_page(url) -> take_snapshot -> fill_form/click ->
evaluate_script(verify) -> take_screenshot(filePath)
```

## 4. SPA form filling (Angular/React — the #1 gotcha)

- **`fill` does NOT dispatch the events an Angular model needs.** On Angular
  (e.g. Concur Expense), a `fill` that types a value leaves the form model
  unchanged and the readonly derived fields (amount, totals) never update.
- **New forms:** use `fill_form` (it drives native events correctly).
- **Editing existing fields:** use `evaluate_script` with the native setter +
  input/change/blur event dispatch:

```js
() => {
  const setVal = (el, v) => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    el.dispatchEvent(new Event('blur', {bubbles: true}));
  };
  const input = /* find by label text / name / current value */;
  setVal(input, 'new value');
  return input.value;
}
```

- Readonly derived fields (e.g. mileage amount = distance x rate) often
  recalculate **on save** — change the input, click Save, then verify the
  derived value in the returned page.

## 5. Uploads and file paths

- `upload_file` needs a path the agent can read; `/tmp/...` works reliably.
  Paths under the agent workspace root or OneDrive-style paths may be
  rejected — copy the file to `/tmp` first.
- To upload a receipt that only exists as an email confirmation, generate a
  PDF from the email text with headless Chrome:

```bash
chrome --headless --disable-gpu --print-to-pdf=/tmp/receipt.pdf file:///tmp/receipt.html
```

## 6. Tool argument discipline

- Chrome MCP tools are **flat** — they reject the LingTai
  `action/input/reasoning` envelope. Pass only the tool's own arguments
  (e.g. `navigate_page` takes `type`+`url`; `take_snapshot` takes nothing).
- Prefer `take_snapshot` with a `filePath` for large pages so the a11y tree is
  not dumped inline.
- A click can fail with "not interactive" when the element is covered or the
  snapshot is stale — take a fresh snapshot, or fall back to
  `evaluate_script` clicking the DOM button directly.

## 7. Human-in-the-loop logins

For SSO sites (Duo, Google SSO, university portals): navigate to the login
page in the agent Chrome, tell the human it is ready, let them log in once,
then continue the workflow. Do not ask for passwords in chat; the human types
into the browser directly.

## 8. Choosing between web browse and chrome-devtools-mcp

| Need | Use |
|---|---|
| Read a static article/page | `web(action="browse")` |
| Search first, then read a result | `web(action="search")` + browse |
| JS-only page, PDF extraction (scrape) | extract_page.py / playwright refs |
| Forms, login, SPA interaction, uploads | chrome-devtools-mcp |
| Verify a submission / capture proof | chrome-devtools-mcp + screenshot |
