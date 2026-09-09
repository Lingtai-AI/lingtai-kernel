---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/news-and-rss.md
  - src/lingtai/tools/web_search/manual/reference/search-strategies.md
  - src/lingtai/tools/web_search/manual/assets/api-endpoints.json
  - src/lingtai/tools/web_search/manual/assets/site-templates.json
maintenance: |
  Keep this public social-data route aligned with endpoint/site assets. Avoid
  duplicating news, real-time, or generic search recipes; preserve platform
  isolation, rate-limit, credential, and no-login boundaries.
---
# Public social data

Use only public, authorized platform APIs or pages. For discovery start with
`web` search; this reference owns platform-specific recovery, not a new action.

| Platform | First route | Hazard |
|---|---|---|
| Reddit | Append `.json` to a public URL; send a descriptive User-Agent. | Pace requests, honor `Retry-After`, and never access private/quarantined content. |
| Hacker News | Firebase `/v0/<list>stories.json`, then `/item/<id>.json`. | `null`, deleted, or dead items are normal; bound recursive comments. |
| Mastodon | The target instance's public `/api/v1`/`api/v2` endpoint. | Instances are separate; `content` is HTML and pagination uses `Link`. |
| X/Twitter | Public page metadata or an authorized official API. | Access, policy, and availability change; do not bypass login or use leaked credentials. |
| GitHub | Public REST API (`api.github.com`). | Search and core quotas differ; inspect current `/rate_limit`; code search may need a token. |

Normalize only fields actually returned, preserve source links and timestamps,
and clean HTML before presenting it. Check HTTP status and empty responses;
do not infer a private post from a public URL. Use the endpoint/site assets for
historical hints to verify, not current quota guarantees.

A challenge, login wall, or rate limit is a stop/slow-down signal. Choose one
other authorized public source explicitly or report the limitation; never solve
CAPTCHA, impersonate a user, rotate identities to evade controls, or chain
unapproved fallback services. News feeds belong to
[news-and-rss.md](news-and-rss.md); current facts belong to
[realtime-data.md](realtime-data.md).

Field checks: Reddit listings paginate through `data.after`/`after` (or `before`);
HTTP 200 with no children is not proof a post never existed. Comments commonly
return `[post_listing, comment_listing]`; `replies` can be empty text or a listing.
HN self-posts may have HTML `text` and no URL; bound comment recursion. Mastodon
account IDs are not usernames and public visibility is instance-dependent.
GitHub `/issues` includes pull requests (look for `pull_request`); repository
`size` is in KB, not bytes. Verify the current API shape before batching.
