"""Prompt-section catalog loaders — kernel-owned.

LingTai's resident runtime guidance (the static, rule-like material rendered
into the resident ``meta_guidance`` system-prompt section) is authored as a
skill-style Markdown catalog under ``lingtai/prompts/guidance/``:

  * ``INDEX.md`` — frontmatter-only manifest carrying the top-level payload
    fields (``schema_version``, ``guidance_version``, ``priority``,
    ``render_mode``) that used to live at the root of the old ``guidance.json``.
  * ``<id>.md`` — one file per guidance section, frontmatter (``id``, ``title``,
    ``summary``/``why`` documentation) + Markdown body.

This module loads that catalog and assembles the same ``dict`` shape the kernel
already consumes (``{schema_version, guidance_version, priority, render_mode,
sections:[{id,title,body}]}``), so the rest of the runtime — ``build_meta_guidance``,
the derived ``system/guidance.json`` mirror, and ``validate_runtime_guidance`` —
is unchanged. Guidance section order remains code-owned through
``GUIDANCE_SECTION_ORDER``; ordering is intentionally not frontmatter behavior
configuration.

Frontmatter values arrive as strings (see :mod:`lingtai_kernel._frontmatter`);
typed fields such as ``schema_version`` are coerced here. The body is preserved
verbatim except for a single trailing newline, which is stripped so the
assembled body is byte-identical to the old JSON-authored ``body`` value.
"""

from __future__ import annotations

from importlib import resources as _resources

from ._frontmatter import split_frontmatter

GUIDANCE_PACKAGE = "lingtai.prompts.guidance"
GUIDANCE_INDEX_FILENAME = "INDEX.md"

# Runtime guidance order is behavior, not authoring metadata. Keep it code-owned
# rather than embedding an `order` field in frontmatter, matching the resident
# prompt contract that `_DEFAULT_ORDER` / `_BATCHES` / `_raw_sections` remain
# code-level sources of truth.
GUIDANCE_SECTION_ORDER = (
    "summarize_best_practice",
    "summarize_reconstruction_threshold",
    "token_efficiency",
    "review_delegation_instruction_check",
    "notification_handling",
)


class PromptCatalogError(ValueError):
    """Raised when a packaged prompt/guidance catalog is structurally malformed.

    Like ``GuidanceSchemaError``, a bad packaged catalog is an authoring/build
    error surfaced to the test suite; the live loader degrades to ``{}`` rather
    than crashing an agent.
    """


def _coerce_section_body(body: str) -> str:
    """Return the section body byte-identical to the old JSON ``body`` value.

    Section files are authored as ``frontmatter + body + "\\n"``. The single
    trailing newline added for a clean file is stripped so the assembled body
    matches the JSON authoring (which carried no trailing newline inside the
    string). No other whitespace is touched.
    """
    if body.endswith("\n"):
        return body[:-1]
    return body


def load_guidance_catalog(package: str = GUIDANCE_PACKAGE) -> dict:
    """Load the guidance Markdown catalog into the runtime-guidance dict shape.

    Reads ``INDEX.md`` (top-level fields) and every code-listed ``<id>.md``
    section file in ``GUIDANCE_SECTION_ORDER``. Returns a dict::

        {
          "schema_version": int,
          "guidance_version": str,
          "priority": str,
          "render_mode": str,
          "sections": [{"id": str, "title": str, "body": str}, ...],
        }

    Raises :class:`PromptCatalogError` on a structurally malformed catalog
    (missing INDEX, non-int schema_version, missing/mismatched id/title/body,
    duplicate id, unexpected section file, or forbidden behavior config such as
    frontmatter `order`). Callers that want graceful degradation should catch it.
    """
    try:
        root = _resources.files(package)
    except (ModuleNotFoundError, FileNotFoundError, OSError) as exc:
        raise PromptCatalogError(f"missing guidance catalog package: {package}") from exc

    index_resource = root.joinpath(GUIDANCE_INDEX_FILENAME)
    try:
        index_text = index_resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise PromptCatalogError(f"guidance catalog missing {GUIDANCE_INDEX_FILENAME}") from exc
    index_meta, _ = split_frontmatter(index_text)

    for key in ("schema_version", "guidance_version", "priority", "render_mode"):
        if key not in index_meta:
            raise PromptCatalogError(f"guidance INDEX.md missing frontmatter key: {key!r}")

    try:
        schema_version = int(index_meta["schema_version"])
    except (TypeError, ValueError) as exc:
        raise PromptCatalogError("guidance INDEX.md schema_version must be an integer") from exc

    if len(set(GUIDANCE_SECTION_ORDER)) != len(GUIDANCE_SECTION_ORDER):
        raise PromptCatalogError("GUIDANCE_SECTION_ORDER contains duplicate ids")

    expected_files = {GUIDANCE_INDEX_FILENAME, *(f"{sid}.md" for sid in GUIDANCE_SECTION_ORDER)}
    extra_files = sorted(
        entry.name
        for entry in root.iterdir()
        if entry.name.endswith(".md") and entry.name not in expected_files
    )
    if extra_files:
        raise PromptCatalogError(f"unexpected guidance section file(s): {extra_files}")

    sections: list[dict] = []
    seen_ids: set[str] = set()
    for sid in GUIDANCE_SECTION_ORDER:
        name = f"{sid}.md"
        entry = root.joinpath(name)
        try:
            text = entry.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError) as exc:
            raise PromptCatalogError(f"unreadable guidance section file: {name}") from exc
        meta, body = split_frontmatter(text)
        file_id = meta.get("id")
        title = meta.get("title")
        if file_id != sid:
            raise PromptCatalogError(
                f"guidance section {name} id mismatch: expected {sid!r}, got {file_id!r}"
            )
        if not title:
            raise PromptCatalogError(f"guidance section {name} missing title frontmatter")
        if "order" in meta:
            raise PromptCatalogError(
                f"guidance section {name} must not carry order frontmatter; "
                "use GUIDANCE_SECTION_ORDER"
            )
        if sid in seen_ids:
            raise PromptCatalogError(f"duplicate guidance section id in catalog: {sid!r}")
        seen_ids.add(sid)
        sections.append({"id": sid, "title": title, "body": _coerce_section_body(body)})

    if not sections:
        raise PromptCatalogError("guidance catalog has no section files")

    return {
        "schema_version": schema_version,
        "guidance_version": index_meta["guidance_version"],
        "priority": index_meta["priority"],
        "render_mode": index_meta["render_mode"],
        "sections": sections,
    }
