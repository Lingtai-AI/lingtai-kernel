"""Shared frontmatter primitive — kernel-owned.

LingTai's resident prompt layers are authored as skill-style artifacts: a
single file carrying a leading ``---`` YAML frontmatter block (developer- and
coding-agent-facing metadata explaining *why* the fragment exists and its
purpose) followed by the Markdown body that is rendered into the LLM prompt.
When prompt/guidance frontmatter carries ``related_files``, that field is a
maintained inner-link list for source files that should be crawled together. The
prompt catalog uses it bidirectionally: the principle map links to each
prompt/guidance source, each source links back to the principle map, the guidance
INDEX links to every guidance section, and each guidance section links back to
the INDEX. It is not a test list or a place to dump indirect dependencies.

This module owns the tiny dependency-free frontmatter parser used by both the
kernel (prompt catalog / guidance loader) and the wrapper (curated MCP skills).
It lives in ``lingtai_kernel`` so the kernel never has to import from the
``lingtai`` wrapper (the kernel↔wrapper import direction is one-way; see
``ANATOMY.md``). ``mcp_servers/_skill.py`` re-exports :func:`split_frontmatter`
for back-compat.

The parser handles only the flat ``key: value`` and ``key: |`` / ``key: >``
block-scalar forms our frontmatter uses; it is not a general YAML parser. All
values come back as strings — callers that need typed fields (ints, bools) must
coerce explicitly. It tolerates files with no frontmatter, returning an empty
mapping and the original text unchanged.
"""

from __future__ import annotations


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split skill-style text into ``(frontmatter dict, body markdown)``.

    Tiny dependency-free parser for the leading ``---`` YAML block. Handles the
    flat ``key: value`` and ``key: |``/``key: >`` block-scalar forms used by our
    frontmatter; it does not attempt to be a general YAML parser. Returns an
    empty mapping and the original text when no frontmatter is present.

    All values are returned as ``str``. Callers needing ``int``/``bool`` fields
    must coerce them explicitly (e.g. ``int(meta["schema_version"])``).
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    # lines[0] is the opening '---'; find the closing fence.
    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\n").strip() == "---":
            end = i
            break
    if end is None:
        return {}, text

    fm_lines = [ln.rstrip("\n") for ln in lines[1:end]]
    body = "".join(lines[end + 1:]).lstrip("\n")

    meta: dict[str, str] = {}
    key: str | None = None
    block_parts: list[str] = []

    def _flush() -> None:
        if key is not None:
            meta[key] = " ".join(" ".join(block_parts).split())

    for raw in fm_lines:
        if key is not None and (raw.startswith((" ", "\t")) or not raw.strip()):
            # Continuation line of a block scalar (key: | / key: >).
            block_parts.append(raw.strip())
            continue
        if ":" in raw and not raw.startswith((" ", "\t")):
            _flush()
            k, _, v = raw.partition(":")
            key = k.strip()
            block_parts = []
            v = v.strip()
            if v in ("|", ">", "|-", ">-", "|+", ">+"):
                # Block scalar — value continues on indented lines below.
                continue
            block_parts.append(v.strip("'\""))
    _flush()
    return meta, body


def strip_frontmatter(text: str) -> str:
    """Return the Markdown body with any leading frontmatter removed.

    Convenience wrapper over :func:`split_frontmatter` for the common case where
    only the body is needed — for example when mirroring a packaged section into
    the prompt manager, where the frontmatter is developer-facing metadata that
    must never leak into the LLM prompt or the final rendered ``system.md``.

    Files with no frontmatter are returned unchanged.
    """
    _meta, body = split_frontmatter(text)
    return body
