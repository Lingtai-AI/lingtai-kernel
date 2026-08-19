"""Context intrinsic — the department that owns the agent's context.

An LTP v2 family (``../CONTRACT.md``): one model-facing root ``context`` with
four fixed canonical action children, each owning its own strict ``input``
object::

    molt      -> shed the conversation, keep the durable stores
    summarize -> record compact replacements in runtime history (record-only)
    rebuild   -> recompose the full prompt, apply summaries, replay provider context
    manual    -> return the installed context-manual skill

This package replaces the former ``psyche`` family, which mixed two unrelated
concerns behind one root: the context lifecycle (molt) and the agent's name.
``psyche`` no longer exists at any model-visible or registry level, and there
is no compatibility alias — ``psyche(...)`` is an unknown tool and the two name
actions now live on ``system`` (``lingtai.tools.system.name``). What arrived
here from elsewhere is the context-hygiene half of ``system``: the public
``system(action='summarize')`` action became the two explicit actions
``context.summarize`` and ``context.rebuild``, and is gone from ``system``.

The molt semantics are moved, not redesigned: summary/session-journal gating,
refusal-before-shed, keep_tool_calls/keep_last, archive/snapshot/rebuild,
``_tc_id`` transport handling, the post-molt notification, forced system molt,
and every durable-store path are exactly what they were. Only the public root
and action name changed (``psyche.context_molt`` -> ``context.molt``). The
durable molt *event* key is deliberately NOT renamed; see
``kernel/agent_session.py`` ``MOLT_BOUNDARY_EVENT``.

Two names spelled ``summarize`` coexist at different envelope levels, and they
are unrelated:

  * ``context(action='summarize')`` — this family's record-only ACTION;
  * the optional root ``summarize`` boolean — the cross-cutting a-priori
    result-summarization presentation control every LTP v2 family advertises
    (``kernel/tool_result_summary.py``).

The root boolean is stripped by the generic dispatcher and is never domain
input; no child here declares a ``summarize`` property. ``context.summarize``
records and never rebuilds; ``context.rebuild`` is the only active operation
that first recomposes every canonical prompt source, then applies pending/new
summaries, then requests provider replay. The public action — not a boolean —
is the discriminator.

Per-action behavior, inputs, and result/error shapes live in ``CONTRACT.md``;
the model-facing text lives in the schema descriptions below and in the
``context-manual`` skill. Neither is restated here.

Sub-modules:
    _snapshots.py — Snapshot and summary persistence for the molt machinery.
    _molt.py      — Context molt core and the system-initiated forced molt.
"""
from __future__ import annotations

from typing import Any, Mapping

# --- Re-exports from sub-modules for backward compatibility ---

# Snapshots (used by consultation, inquiry, etc.)
from ._snapshots import SNAPSHOT_SCHEMA_VERSION, _write_molt_snapshot, _write_molt_summary  # noqa: F401

# Molt (the public surface)
from ._molt import _context_molt, context_forget  # noqa: F401
from .._manual import load_installed_manual  # noqa: F401
from ..tool_family import (
    TRIGGER_UNSUPPORTED_INPUT_FIELD,
    ChildTool,
    DiagnosticDescriptor,
    ToolFamily,
)
from ..tool_family.manual import build_manual_child

# The summarize/rebuild engine. It stays in ``system/summarize.py`` — moving
# the ~700-line engine and its marker constants is not required to move public
# ownership, and ``kernel``/adapters already import ``SUMMARIZE_MARKER`` and
# ``mark_pending_summaries_done`` from there for the forced-rebuild path. This
# family owns the public actions; that module remains the private engine.
from ..system.summarize import _summarize as _summarize_engine


# ---------------------------------------------------------------------------
# Canonical child input schemas — one strict, closed object per action.
# ---------------------------------------------------------------------------
#
# Each action's own ``input`` is declared exactly once here. ``ToolFamily``
# composes the model-facing schema and the dispatch allow-list from these same
# objects, so the two can never drift: the child's canonical name IS the public
# ``action`` value IS the dispatch key.

_MOLT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": 'Your session retrospective (~10,000 tokens). Write as a record — what happened, what you learned, what remains. The four stores must be tended BEFORE molt. Saved to `system/summaries/molt_<count>_<ts>.md` and replayed to the next you. See context-manual for full writing guidance. This is domain input the molt itself consumes, not the root `summarize` post-processing control.',
        },
        "session_journal_path": {
            "type": "string",
            "description": 'REQUIRED. The path to the session-journal entry you wrote for the just-finished segment BEFORE molting: knowledge/session-journal/<entry>/KNOWLEDGE.md (a per-segment sub-entry, NOT the parent index). Must be inside your workdir, exist, be non-empty UTF-8, have valid YAML frontmatter with `name` and `description`, and identify itself as session knowledge via `type: session-journal` or `session_journal: true`. The molt is refused before any context is shed if this is missing or invalid. See context-manual §4.',
        },
        "keep_tool_calls": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": 'Optional list of tool-call IDs to replay across the molt, in your chosen order. If any ID is not found, molt is refused before anything is shed. Keep short — durable stores are primary persistence. Pass null to keep none. See context-manual.',
        },
        "keep_last": {
            "type": ["integer", "null"],
            "description": 'Optional requested minimum number of recent conversation entries to replay into the fresh session (default: 20 when null). The retained suffix may contain more entries when needed to preserve one adjacent assistant tool-call/result batch whole. Pass 0 to archive everything. Overlapping entries with keep_tool_calls are deduplicated. See context-manual.',
        },
    },
    "required": ["summary", "session_journal_path", "keep_tool_calls", "keep_last"],
    "additionalProperties": False,
}

# ``molt``'s own static, mechanical diagnostic for a foreign ``input`` field
# (cross-action, e.g. a ``summarize``/``rebuild`` key, or wholly unknown, e.g.
# a smuggled ``files``): declared once, adjacent to ``_MOLT_INPUT_SCHEMA``,
# per ``tool_family/CONTRACT.md`` "Diagnostics sidecar". The generic
# dispatcher only ever supplies the structural ``location`` around this
# verbatim text — it does not (and must not) claim `session_journal_path`
# has to be relative; the existing in-workdir-absolute-normalizes-to-relative
# policy is unchanged and unrelated to this diagnostic.
_MOLT_UNSUPPORTED_INPUT_DIAGNOSTIC = DiagnosticDescriptor(
    code="CTX_MOLT_UNSUPPORTED_INPUT_FIELD",
    expected_form=(
        "an input object containing only summary, session_journal_path, "
        "keep_tool_calls, and keep_last"
    ),
    reason="molt rejects foreign action input before it can shed context",
    fix="remove the foreign field or choose the action that owns it",
)

#: Per-child diagnostic sidecars, keyed by child name then structural
#: trigger. Only ``molt`` opts in today; a child absent here (``summarize``,
#: ``rebuild``, ``manual``) gets exactly the generic dispatcher's legacy
#: three-key failure for a foreign ``input`` field, unchanged.
_CHILD_DIAGNOSTICS: dict[str, Mapping[str, DiagnosticDescriptor]] = {
    "molt": {TRIGGER_UNSUPPORTED_INPUT_FIELD: _MOLT_UNSUPPORTED_INPUT_DIAGNOSTIC},
}

#: One item of the summarize/rebuild ``items`` array. Declared once and reused
#: by both schemas below so the two branches cannot drift.
_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool_call_id": {
            "type": "string",
            "description": "The id of the prior tool-result block to summarize.",
        },
        "summary": {
            "type": "string",
            "description": "Your agent-authored summary of that tool result.",
        },
    },
    "required": ["tool_call_id", "summary"],
    "additionalProperties": False,
}

_SUMMARIZE_ITEMS_DESCRIPTION = (
    "REQUIRED, non-empty. The tool results to replace with your own compact "
    "summaries, each with 'tool_call_id' (the id of the prior tool-result "
    "block) and 'summary' (your agent-authored text). Supports multiple items "
    "per call. The original is NOT deleted — it remains retrievable from "
    "events.jsonl by tool_call_id. Pick targets from "
    "`_meta.agent_meta.agent_state.current_tool_result_chars.top_results`. "
    "This action RECORDS ONLY: the active provider context may still carry "
    "the old raw results until context(action='rebuild') applies them."
)

_REBUILD_ITEMS_DESCRIPTION = (
    "Optional — omit it entirely for the ordinary call. Every rebuild first "
    "re-reads and recomposes ALL canonical system-prompt sections from durable "
    "and configured sources, then applies summaries, then requests provider "
    "replay with the new prompt/history. context(action='rebuild', input={}) is "
    "valid even with zero pending summaries; an explicit null means the same. "
    "Pass items to record those summaries after prompt composition and apply "
    "them in the same call. Same item shape as context(action='summarize')."
)

_SUMMARIZE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "description": _SUMMARIZE_ITEMS_DESCRIPTION,
            "items": _ITEM_SCHEMA,
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

# ``items`` is genuinely OPTIONAL here — it is deliberately absent from
# ``required``, unlike every other optional field in this package.
#
# The usual LTP v2 convention is "REQUIRED but nullable", because a strict
# provider schema has no other way to express an optional field. That
# convention is wrong for this action: ``context(action='rebuild', input={})``
# is the *ordinary* call (apply the already-pending summaries), not an edge
# case, so the model-visible schema must accept a bare ``{}``. Listing ``items``
# in ``required`` would advertise a contract the handler does not have — the
# handler accepts ``{}`` — and would make the documented ordinary call
# schema-invalid.
#
# ``type`` stays ``["array", "null"]`` so an explicit ``{"items": null}`` from a
# provider that always materializes declared properties is still accepted;
# ``_strip_nulls`` turns that back into "absent" before the engine sees it. So
# ``{}`` and ``{"items": null}`` are the same ordinary pure-rebuild call, and
# both are schema-valid.
_REBUILD_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": ["array", "null"],
            "description": _REBUILD_ITEMS_DESCRIPTION,
            "items": _ITEM_SCHEMA,
        },
    },
    "required": [],
    "additionalProperties": False,
}


def _summarize_action(agent, args: dict) -> dict:
    """``context(action='summarize')`` — record only, never rebuild.

    Delegates to the shared private engine with the rebuild discriminator
    pinned off. The engine's item validation, per-item results, pending
    markers, raw-log recovery hints, partial/error behavior, and diagnostics
    are used verbatim; this wrapper only fixes which mode is requested, so the
    public action cannot silently rebuild.
    """
    return _summarize_engine(agent, {**args, "rebuild": False})


def _rebuild_action(agent, args: dict) -> dict:
    """Perform the one active full context reconstruction operation.

    Ordering is contractual: first re-read/recompose every canonical prompt
    source through Agent's shared refresh/molt path, then let the summary engine
    record/apply pending or newly supplied summaries, then request provider
    history replay. A bare input remains meaningful with zero pending summaries.
    """
    reconstruct = getattr(agent, "_reconstruct_context", None)
    if not callable(reconstruct):
        return {
            "status": "error",
            "reason": "context_reconstruction_unavailable",
            "message": "This agent does not provide the canonical context reconstruction hook.",
        }
    try:
        reconstruct()
    except Exception as exc:
        try:
            agent._log("context_reconstruction_failed", error=type(exc).__name__)
        except Exception:
            pass
        return {
            "status": "error",
            "reason": "context_reconstruction_failed",
            "message": f"Canonical prompt reconstruction failed: {type(exc).__name__}.",
        }

    result = _summarize_engine(agent, {**args, "rebuild": True})
    if isinstance(result, dict):
        result["prompt_reconstructed"] = True
        result["prompt_reconstruction"] = (
            "All canonical prompt sources were re-read and recomposed before "
            "summary processing."
        )
    return result


# The one canonical child registry: name, canonical input schema, and the
# underlying ``(agent, args)`` handler. Declaring names, order, schemas, and
# handlers in a single place is what makes schema-vs-dispatch drift
# structurally impossible — there is no second list to forget to update, so a
# child can never be schema-advertised but dispatch-rejected.
#
# Order here is the model-facing ``action`` enum order and the ``input.oneOf``
# branch order: the lifecycle operation first, then the two context-hygiene
# operations in the order they are used (record, then apply).
#
# ``manual`` is absent: ``build_manual_child`` owns that child's schema and
# handler, and :func:`_build_children` appends it last.
_CHILD_SPECS: tuple[tuple[str, dict[str, Any], Any], ...] = (
    ("molt", _MOLT_INPUT_SCHEMA, _context_molt),
    ("summarize", _SUMMARIZE_INPUT_SCHEMA, _summarize_action),
    ("rebuild", _REBUILD_INPUT_SCHEMA, _rebuild_action),
)

#: Public action order, derived from the one registry (``manual`` last).
ACTION_ORDER: tuple[str, ...] = tuple(name for name, _s, _h in _CHILD_SPECS) + ("manual",)

#: The installed intrinsic-skill directory ``manual`` reads. This is the
#: ``load_installed_manual`` skill name, not the family name.
_MANUAL_SKILL_NAME = "context-manual"

#: Envelope metadata the family threads to a handler out-of-band rather than
#: as action ``input``. ``_tc_id`` is the wire tool_use_id
#: ``base_agent._dispatch_tool`` injects into every intrinsic's args; ``molt``
#: is the one operation that genuinely *consumes* it (it locates the molt's own
#: ToolCallBlock in the live interface to replay it), so — unlike ``soul``/
#: ``notification``/``system``, which merely drop it — this family strips it
#: from the closed root and hands it to that one handler directly. Root
#: ``reasoning``/``_reasoning`` is threaded the same way for the post-molt
#: reminder, exactly as ``avatar`` threads its spawn mission brief.
_MOLT_ENVELOPE_KEYS = ("_tc_id", "_reasoning", "reasoning", "_initiator")


def _strip_nulls(action_input: Mapping[str, Any]) -> dict[str, Any]:
    """Drop explicit nulls so "absent" and "null" mean the same downstream.

    Strict provider schemas express an optional field as a REQUIRED nullable
    property, so the model sends ``{"keep_tool_calls": null, "keep_last": null}``
    for a plain molt. ``rebuild.items`` is the one field this package leaves
    genuinely optional (a bare ``{}`` is its ordinary call), but a provider that
    materializes every declared property may still send ``{"items": null}``. The
    handlers key off ``args.get(...)`` / ``"x" not in args``, so stripping nulls
    here makes absent and null identical downstream — including ``rebuild``'s
    no-new-items path — without touching the handlers themselves.
    """
    return {key: value for key, value in action_input.items() if value is not None}


def _build_children(agent, envelope: Mapping[str, Any] | None = None) -> list[ChildTool]:
    """Build the four children from the one canonical registry.

    ``agent`` may be ``None`` for the module-level schema-only family, whose
    children are never dispatched — only their schemas are read.

    ``envelope`` carries the out-of-band metadata keys in
    :data:`_MOLT_ENVELOPE_KEYS`. ``ToolFamily`` correctly passes no envelope
    field to any child, so the one handler that consumes transport metadata
    (``molt``, which needs ``_tc_id`` to locate and replay its own
    ToolCallBlock) receives it here, merged beneath the validated ``input``
    rather than smuggled through it.
    """
    extra = dict(envelope or {})

    def _bind(handler, name: str):
        def _dispatch(action_input: Mapping[str, Any]) -> dict:
            args = _strip_nulls(action_input)
            if name == "molt":
                # Envelope metadata never overwrites validated action input.
                for key, value in extra.items():
                    args.setdefault(key, value)
            return handler(agent, args)

        return _dispatch

    return [
        ChildTool(
            name,
            schema,
            _bind(handler, name),
            title=f"{name} input",
            diagnostics=_CHILD_DIAGNOSTICS.get(name),
        )
        for name, schema, handler in _CHILD_SPECS
    ] + [build_manual_child(agent, _MANUAL_SKILL_NAME)]


# Composes the model-facing schema. Building it at import time is also the
# registry's duplicate/reserved-name collision check: a collision raises
# ``ToolFamilyError`` here rather than shipping silently. It never dispatches —
# ``context`` is an intrinsic *module*, not a per-Agent manager object, so
# there is no instance to hang a family off; ``handle()`` binds one to the
# passed agent per call from this same registry.
_FAMILY = ToolFamily("context", _build_children(None))


# ---------------------------------------------------------------------------
# Schema / description
# ---------------------------------------------------------------------------

#: This family's own per-action routing prose. The generic composer writes a
#: neutral "Required operation within the context family." description; the
#: guidance the model actually needs to pick an action replaces it in
#: :func:`get_schema` rather than being lost.
_ACTION_ENUM_DESCRIPTION = (
    'Required operation. '
    'molt: shed your conversation context, keep the durable stores. Requires '
    '`summary` and a valid `session_journal_path` — tend the four stores '
    'BEFORE molting. See context-manual.\n'
    'summarize: record your own compact replacements for prior tool results in '
    'runtime history. RECORD ONLY — it does not rebuild, so the active '
    'provider context may still carry the old raw results.\n'
    'rebuild: re-read and recompose ALL canonical prompt sources, then apply '
    'pending/new summaries, then replay provider context with the new prompt and '
    'history. Bare input is valid even with zero pending summaries. Prefer one '
    'tactical rebuild; do not loop rebuild.\n'
    'manual: return the installed context-manual skill without performing any '
    'context operation.\n'
    'Your name is not here: use system(action=\'name_set\'|\'name_nickname\').'
)


def get_description(lang: str = "en") -> str:
    return 'Your context: shed it, compact it, rebuild it. One tool, four actions, each with its own strict input object: context(action=..., input={...}, reasoning=\'why\'). molt: 凝蜕 — shed the conversation, keep the durable stores; requires a written session journal. summarize: record compact replacements for bulky prior tool results (records only, does NOT rebuild). rebuild: re-read and recompose every canonical prompt source, then apply pending/new summaries, then replay provider context with the new prompt/history; bare input is valid even with zero pending summaries. manual: return the installed context-manual skill. Your name lives on system(action=\'name_set\'|\'name_nickname\'); your 灵台 and pad are lingtai(...) and pad(...). Note the two levels: the ACTION named summarize is this domain operation, while the optional ROOT summarize boolean is the unrelated result-presentation control — leave it false here (results are small), and call manual with summarize=false so the exact molt procedure is not summarized away.'


def get_schema(lang: str = "en") -> dict:
    # Composed by the generic ToolFamily infra from each child's own canonical
    # ``input_schema`` above, rather than hand-assembled: root ``action`` +
    # per-action ``input`` + required ``reasoning`` + optional ``summarize``,
    # with a root ``allOf`` correlating each ``action`` const to that exact
    # action's ``input`` shape on both the Chat and Responses wires.
    #
    # ``lang`` is accepted for source compatibility and ignored: schema prose
    # is canonical English and language-independent.
    schema = _FAMILY.build_schema()
    schema["properties"]["action"]["description"] = _ACTION_ENUM_DESCRIPTION
    return schema


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _adapt_manual_result(mcp_result: dict) -> dict:
    """Flatten the reserved ``manual`` child's canonical result to this family's shape.

    The reserved child is registered unwrapped, so ``ToolFamily.handle()``
    returns its canonical ``content``/``structuredContent`` result verbatim.
    The public manual result predates that generic contract and must stay the
    flat ``load_installed_manual`` shape (``status``, ``manual``,
    ``manual_path``, plus ``error`` when degraded), so this Host-owned adapter
    runs strictly *after* dispatch — never inside the child, and never as a
    second envelope around it.
    """
    flat: dict[str, Any] = {
        "status": mcp_result.get("status", "ok"),
        "manual": mcp_result["content"][0]["text"],
        "manual_path": mcp_result["structuredContent"]["manual_path"],
    }
    if "error" in mcp_result:
        flat["error"] = mcp_result["error"]
    return flat


def handle(agent, args: dict) -> dict:
    """Handle the ``context`` family root — validate the envelope, dispatch one action.

    Envelope validation and cross-action rejection belong to the generic
    dispatcher (``tool_family/CONTRACT.md``). This function owns only what is
    context-specific: lifting the intrinsic transport/audit metadata out of the
    closed root, and the two post-dispatch presentation adaptations below.

    ``_tc_id`` is transport metadata ``base_agent._dispatch_tool`` injects into
    EVERY intrinsic's args (capabilities like ``web`` never see it). This is
    the one family that genuinely *consumes* it — ``molt`` locates the molt's
    own ToolCallBlock by that wire id to replay it into the fresh session — so
    it is stripped here, at this family's own Host boundary, and threaded to
    that single handler out-of-band. The shared ``_ROOT_FIELDS`` set is not
    widened for it, and no other action can observe it.
    """
    raw = dict(args or {})
    envelope = {key: raw.pop(key) for key in _MOLT_ENVELOPE_KEYS if key in raw}
    # ``reasoning``/``_reasoning`` remain admitted root fields for the generic
    # dispatcher, so put back the public spelling it knows about; only the
    # molt handler sees the copy in ``envelope``.
    for key in ("reasoning", "_reasoning"):
        if key in envelope:
            raw[key] = envelope[key]

    action = raw.get("action")
    result = ToolFamily("context", _build_children(agent, envelope)).handle(raw)

    if action == "manual" and "content" in result:
        return _adapt_manual_result(result)
    if result.get("error_code") == "ACTION_REQUIRED":
        # Preserve a context-shaped unknown-action error rather than the
        # generic envelope failure.
        return {
            "error": (
                f"Unknown context action: {action if action is not None else ''}. "
                f"Must be one of: {', '.join(ACTION_ORDER)}."
            )
        }
    return result
