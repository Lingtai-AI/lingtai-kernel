"""Tool glossary loader — kernel-owned resource mechanics.

First-party (and opt-in third-party) tool packages own three strict Markdown
glossary resources — ``glossary-en.md``, ``glossary-zh.md``,
``glossary-wen.md`` — that map the package's canonical English tool
identifiers (function names, JSON property names, action/enum values) into the
selected prompt language.  The renderer strips frontmatter and appends only the
body to that tool's full ``## tools`` description.  A glossary is opaque prompt
text: it never participates in schema construction, argument normalization,
dispatch, or provider serialization.

Design contract (parent synthesis):

- Resources are read with ``importlib.resources`` so wheels and third-party
  distributions work, not ``Path(module.__file__)``.
- Only an allowlisted normalized language is selected; an unchecked config
  string is never interpolated into a path.
- The exact strict frontmatter schema is validated before a body is returned.
  The single shared :func:`parse_glossary` grammar is used by **both** the
  runtime loader and the owning-package validator, so there is exactly one
  source of truth for "what is a valid glossary resource."
- Returns ``base_description.rstrip()`` unchanged when the body is
  empty/unavailable; otherwise returns ``base_description.rstrip() + "\\n\\n" +
  body.strip()``.
- Results are cached by ``(tool_package, resolved_language)`` for the process
  lifetime, including failures, with at most one Python warning per
  package/language/problem so rebuilding the prompt every turn cannot flood logs.
  The cache and warn-once set are protected by a process-wide lock so
  concurrent threads cannot corrupt them or emit duplicate warnings.
- Fail-closed for localized prompt text but fail-open for tool availability:
  a packaging/content defect cannot remove a tool or corrupt its schema.

This module must not import from ``tools`` or ``lingtai`` (kernel isolation).
"""

from __future__ import annotations

import threading
import warnings
from importlib import resources as importlib_resources

import yaml

__all__ = [
    "SUPPORTED_TOOL_GLOSSARY_LANGUAGES",
    "GlossaryValidationError",
    "parse_glossary",
    "normalize_tool_glossary_language",
    "load_tool_glossary",
    "append_tool_glossary",
]


# ---------------------------------------------------------------------------
# Language allowlist and normalization
# ---------------------------------------------------------------------------

SUPPORTED_TOOL_GLOSSARY_LANGUAGES: tuple[str, ...] = ("en", "zh", "wen")

_KNOWN: frozenset[str] = frozenset(SUPPORTED_TOOL_GLOSSARY_LANGUAGES)


def normalize_tool_glossary_language(language: object) -> str:
    """Normalize *language* to one allowlisted glossary tag.

    Glossary-local normalization; does not change human-facing/kernel
    operational ``t()`` behavior.

    1. Non-string → ``en``.
    2. Strip, ``_``→``-``, ASCII-casefold/lowercase.
    3. Exact tag in allowlist → use it.
    4. Primary subtag before first ``-`` in allowlist → use that primary tag
       (``ZH_cn``→``zh``, ``en-US``→``en``, ``wen-Hant``→``wen``).
    5. Otherwise → ``en`` (``fr``, empty, malformed → ``en``).

    ``wen`` is the canonical repository tag; it is never aliased to BCP-47
    ``lzh``.
    """
    if not isinstance(language, str):
        return "en"
    tag = language.strip().replace("_", "-").casefold()
    if tag in _KNOWN:
        return tag
    primary = tag.split("-", 1)[0]
    if primary in _KNOWN:
        return primary
    return "en"


# ---------------------------------------------------------------------------
# Strict glossary grammar — single shared parser/loader
# ---------------------------------------------------------------------------

_FILENAME_BY_LANG: dict[str, str] = {lang: f"glossary-{lang}.md" for lang in _KNOWN}

_EXPECTED_FRONTMATTER_KEYS = frozenset(
    {"kind", "schema_version", "tool_package", "language"}
)


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader subclass that raises on duplicate YAML mapping keys.

    ``yaml.safe_load`` silently keeps the last value of a duplicate key,
    which is a latent defect for strict-frontmatter validation. This loader
    replaces the default mapping constructor with one that tracks every key
    and raises on the first duplicate.
    """


def _construct_no_duplicates(loader: _UniqueKeyLoader, node, deep: bool = False):
    """Construct a mapping, raising on duplicate keys."""
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        value = loader.construct_object(value_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"frontmatter key is not hashable: {key!r}",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"duplicate frontmatter key: {key!r}",
                key_node.start_mark,
            )
        mapping[key] = value
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_no_duplicates,
)


class GlossaryValidationError(ValueError):
    """Raised when a glossary resource exists but fails strict validation.

    The runtime loader catches this, falls back to English, and — if the
    English resource also fails — returns the empty sentinel. The validator
    collects these for reporting.
    """


def parse_glossary(
    text: str,
    *,
    tool_package: str,
    language: str,
) -> str:
    """Parse and validate strict glossary frontmatter; return the body.

    This is the single shared grammar used by **both** the runtime loader
    (:func:`load_tool_glossary`) and the owning-package validator
    (``tools.glossary_validator``). Raises :class:`GlossaryValidationError`
    on any violation.

    Frontmatter must be exactly::

        ---
        kind: tool-glossary
        schema_version: 1
        tool_package: <tool_package>
        language: <language>
        ---

    The body (everything after the closing fence) is returned verbatim.
    """
    # Split frontmatter from body — only the YAML block between the
    # fences is parsed; the body is opaque Markdown that must not reach
    # the YAML loader (a closing --- inside the body would be a YAML
    # document separator).
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise GlossaryValidationError("first physical line must be exactly '---'")
    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            end = i
            break
    if end is None:
        raise GlossaryValidationError("missing closing '---' fence")
    fm_text = "".join(lines[1:end])
    body = "".join(lines[end + 1 :])

    # Parse with the duplicate-key-rejecting loader.
    try:
        doc = yaml.load(fm_text, Loader=_UniqueKeyLoader)  # noqa: S506
    except yaml.YAMLError as exc:
        raise GlossaryValidationError(f"YAML parse error: {exc}") from exc

    if not isinstance(doc, dict):
        raise GlossaryValidationError("frontmatter is not a YAML mapping")

    # Exactly four fields; no unknown keys.
    actual_keys = set(doc.keys())
    if actual_keys != _EXPECTED_FRONTMATTER_KEYS:
        extra = actual_keys - _EXPECTED_FRONTMATTER_KEYS
        missing = _EXPECTED_FRONTMATTER_KEYS - actual_keys
        parts: list[str] = []
        if extra:
            parts.append(f"unknown fields: {sorted(extra)}")
        if missing:
            parts.append(f"missing fields: {sorted(missing)}")
        raise GlossaryValidationError("; ".join(parts))

    kind = doc["kind"]
    if kind != "tool-glossary":
        raise GlossaryValidationError(f"kind must be 'tool-glossary', got {kind!r}")

    sv = doc["schema_version"]
    # YAML integer 1; bool (True/False) and str ("1") must be rejected.
    if not isinstance(sv, int) or isinstance(sv, bool) or sv != 1:
        raise GlossaryValidationError(f"schema_version must be integer 1, got {sv!r}")

    pkg = doc["tool_package"]
    if not isinstance(pkg, str) or pkg != tool_package:
        raise GlossaryValidationError(
            f"tool_package must be {tool_package!r}, got {pkg!r}"
        )

    lang = doc["language"]
    if not isinstance(lang, str) or lang != language:
        raise GlossaryValidationError(f"language must be {language!r}, got {lang!r}")

    return body


# ---------------------------------------------------------------------------
# Cached loader
# ---------------------------------------------------------------------------

_EMPTY: str = ""

# Process-wide re-entrant lock protecting _cache, _warned, and the complete
# first-load path. Resource files are tiny and loaded at most once per key;
# serializing a cache miss guarantees one read and one warning under concurrency.
_lock = threading.RLock()

# Cache: (tool_package, resolved_language) -> body string.
# Failures are cached as _EMPTY so the warning fires at most once.
_cache: dict[tuple[str, str], str] = {}

# Warn-once set: (tool_package, language, problem) tuples that have already
# emitted a warning.
_warned: set[tuple[str, str, str]] = set()


def _warn_once(
    tool_package: str,
    language: str,
    problem: str,
    filename: str,
    *,
    detail: str | None = None,
) -> None:
    """Emit at most one warning per stable package/language/problem class."""
    key = (tool_package, language, problem)
    with _lock:
        if key in _warned:
            return
        _warned.add(key)
    rendered_problem = problem if not detail else f"{problem}: {detail}"
    warnings.warn(
        f"tool glossary {tool_package}/{filename}: {rendered_problem} — "
        f"localized appendix omitted (tool availability unaffected).",
        stacklevel=4,
    )


# Exception types that resource loading can raise; all are caught and
# converted to a bounded warning + empty result so a packaging or content
# defect never removes a tool from the prompt.
_RESOURCE_ERRORS = (
    FileNotFoundError,
    ModuleNotFoundError,
    ImportError,
    OSError,
    UnicodeDecodeError,
    TypeError,
    ValueError,
)


def _read_resource(tool_package: str, language: str) -> str | None:
    """Read and validate a single glossary resource.

    Returns the validated body, or ``None`` if the resource is absent or
    invalid (caller handles fallback).

    All expected exceptions from resource loading or validation are caught here
    so a malformed package cannot crash the prompt builder. Missing resources
    and all other failures emit one bounded warning per package/language/problem
    class via :func:`_warn_once`.
    """
    filename = _FILENAME_BY_LANG[language]
    try:
        text = (
            importlib_resources.files(tool_package)
            .joinpath(filename)
            .read_text(encoding="utf-8")
        )
    except _RESOURCE_ERRORS as exc:
        problem = (
            "missing-resource"
            if isinstance(exc, FileNotFoundError)
            else type(exc).__name__
        )
        _warn_once(
            tool_package,
            language,
            problem,
            filename,
            detail=str(exc) or None,
        )
        return None

    try:
        body = parse_glossary(text, tool_package=tool_package, language=language)
    except GlossaryValidationError as exc:
        _warn_once(
            tool_package,
            language,
            "invalid-frontmatter",
            filename,
            detail=str(exc),
        )
        return None

    # English body must be empty.
    if language == "en" and body.strip():
        _warn_once(
            tool_package,
            language,
            "invalid-body",
            filename,
            detail="English body must be empty",
        )
        return None

    # Non-English body must be non-empty.
    if language != "en" and not body.strip():
        _warn_once(
            tool_package,
            language,
            "invalid-body",
            filename,
            detail="non-English body must be non-empty",
        )
        return None

    return body.strip()


def load_tool_glossary(tool_package: str, language: object) -> str:
    """Load the validated glossary body for *tool_package* in *language*.

    Normalizes *language*, looks up the selected resource, and on failure
    falls back to English.  Returns ``""`` when no valid body is available
    (including for English, which is intentionally empty).  Results are cached
    for the process lifetime.

    Thread-safe: the cache and warn-once state are protected by a
    process-wide lock so concurrent prompt rebuilds cannot corrupt them
    or emit duplicate warnings.
    """
    if not tool_package or not isinstance(tool_package, str):
        return _EMPTY

    lang = normalize_tool_glossary_language(language)
    cache_key = (tool_package, lang)

    # Serialize the complete first-load path. Glossary resources are tiny and
    # immutable; holding the re-entrant lock across the read guarantees one
    # resource lookup and one bounded warning for concurrent prompt rebuilds.
    with _lock:
        if cache_key in _cache:
            return _cache[cache_key]

        body = _read_resource(tool_package, lang)

        # Fallback: selected non-English → English → empty. A valid English
        # resource intentionally returns the empty string; preserve that cached
        # sentinel instead of treating it as a reason to read the file again.
        if body is None and lang != "en":
            en_key = (tool_package, "en")
            if en_key in _cache:
                en_body = _cache[en_key]
            else:
                loaded_en = _read_resource(tool_package, "en")
                en_body = loaded_en if loaded_en is not None else _EMPTY
                _cache[en_key] = en_body
            body = en_body

        result = body if body is not None else _EMPTY
        _cache[cache_key] = result
        return result


def append_tool_glossary(
    base_description: str,
    *,
    tool_package: str | None,
    language: object,
) -> str:
    """Append the selected glossary body to *base_description*.

    Returns ``base_description.rstrip()`` unchanged when *tool_package* is
    ``None`` or the body is empty/unavailable; otherwise returns
    ``base_description.rstrip() + "\\n\\n" + body``.
    """
    if not tool_package:
        return base_description.rstrip()
    body = load_tool_glossary(tool_package, language)
    if not body:
        return base_description.rstrip()
    return base_description.rstrip() + "\n\n" + body
