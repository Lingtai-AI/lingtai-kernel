"""Tool surface — schemas, dispatch, inventory refresh, and tool registry.

The 2-layer tool dispatch: intrinsics (built-in) + capabilities/MCP.
"""
from __future__ import annotations

from ..config import tool_prose_section_enabled
from ..llm import FunctionSchema
from ..tool_glossary import append_tool_glossary
from ..types import UnknownToolError

# Canonical English reasoning-property description — language-independent.
# Formerly ``t(lang, "tool.reasoning_description")`` in the kernel i18n catalog;
# the model-facing schema must not vary by prompt language.
_REASONING_DESCRIPTION = (
    "Brief explanation of why you are calling this tool "
    "(recorded in your diary)."
)


def _disclosed_tools(agent) -> set[str]:
    """The process-local set of disclosed stub-bearing tool names.

    Defensive for partial test doubles that bypass ``BaseAgent.__init__``.
    """
    disclosed = getattr(agent, "_disclosed_tools", None)
    if disclosed is None:
        disclosed = agent._disclosed_tools = set()
    return disclosed


def _effective_tool_schema(agent, schema: FunctionSchema) -> FunctionSchema:
    """The schema the provider sees: the stub until the tool is disclosed."""
    if schema.stub is not None and schema.name not in _disclosed_tools(agent):
        return schema.stub
    return schema


def _disclose_tool(agent, name: str, *, reason: str) -> bool:
    """Make a stub-bearing tool's full schema provider-visible from the next round.

    Returns True only on the transition. Tools without a stub are always fully
    visible, so disclosing them is a no-op. Session-local by design: the set
    lives on the agent, ``_build_tool_schemas`` re-reads it on every send, and a
    relaunch starts collapsed again.
    """
    if name in _disclosed_tools(agent):
        return False
    schemas = getattr(agent, "_tool_schemas", None) or ()
    if not any(s.name == name and s.stub is not None for s in schemas):
        return False
    _disclosed_tools(agent).add(name)
    agent._token_decomp_dirty = True
    log = getattr(agent, "_log", None)
    if callable(log):
        log("tool_schema_disclosed", tool=name, reason=reason)
    return True


def _disclose_tools_for_sources(agent, sources) -> list[str]:
    """Disclose every stub-bearing tool that declared one of ``sources``.

    ``sources`` are ``.notification/`` channel names about to be delivered to
    the model; the registrant declared which channels reveal its tool via
    ``FunctionSchema.disclosure_sources``. Core matches names only — it does
    not know which technology stands behind a channel.
    """
    wanted = set(sources or ())
    if not wanted:
        return []
    disclosed = []
    # Defensive for partial test doubles that carry no tool surface at all.
    for schema in list(getattr(agent, "_tool_schemas", None) or ()):
        if schema.stub is None or wanted.isdisjoint(schema.disclosure_sources):
            continue
        if _disclose_tool(agent, schema.name, reason="notification"):
            disclosed.append(schema.name)
    return disclosed


def _dispatch_tool(agent, tc) -> dict:
    """Dispatch a tool call to the appropriate handler.

    Layer 1: intrinsics (built-in tools)
    Layer 2: MCP handlers (domain tools)

    Raises UnknownToolError if the tool name is not found.
    """
    if tc.name in agent._intrinsics:
        # Inject the wire tool_use_id so intrinsics that need to locate
        # their own ToolCallBlock in the live interface (notably
        # psyche._context_molt) can find it. Intrinsics that don't care
        # simply ignore the field.
        args = dict(tc.args or {})
        args["_tc_id"] = tc.id
        return agent._intrinsics[tc.name](args)
    elif tc.name in agent._tool_handlers:
        # Any call to a stub-bearing tool (its stub exposes only ``manual``)
        # discloses the full schema for the next provider round.
        _disclose_tool(agent, tc.name, reason="called")
        return agent._tool_handlers[tc.name](tc.args or {})
    elif tc.name == "bash" and "shell" in agent._tool_handlers:
        # One-way rolling compatibility for historical/pending calls.  Do not
        # register a second schema or expose ``bash`` in provider tools.
        return agent._tool_handlers["shell"](tc.args or {})
    else:
        raise UnknownToolError(tc.name)


def _refresh_tool_inventory_section(agent) -> None:
    """Rebuild the 'tools' section from current intrinsic + schema descriptions.

    OPT-IN, DEFAULT OFF. The prose this section renders is the same text the
    tool-calling schema already carries as its top-level ``description``, so
    rendering both puts two copies of every tool's prose into one turn's
    context — byte-identical duplication on the CLI-backed adapters
    (``claude_code``/``kimi_code``), which serialise the full schema
    description into their ``# AVAILABLE TOOLS`` block right next to this
    section. Unless ``LINGTAI_TOOL_PROSE_SECTION_ENABLED`` is truthy
    (:func:`lingtai.kernel.config.tool_prose_section_enabled`) the section is
    left unwritten — and any previously written copy is deleted, so flipping
    the switch off and rebuilding actually drops it — while
    :func:`lingtai.kernel.llm.base.wire_tool_description` puts the full prose
    on the provider wire instead of the ``WIRE_TOOL_DESCRIPTION`` pointer.
    Exactly one copy either way; no tool ever loses its guidance.

    When opted in, the old behavior is restored unchanged: each tool's full
    canonical English description (``get_description()`` for intrinsics,
    ``FunctionSchema.description`` for dynamic/MCP tools) is appended with the
    selected-language glossary body from its owning package, and provider wire
    descriptions revert to the ``WIRE_TOOL_DESCRIPTION`` pointer.

    Nested parameter/property descriptions inside ``parameters`` are untouched
    in both states.
    """
    if not tool_prose_section_enabled():
        agent._prompt_manager.delete_section("tools")
        return
    lang = agent._config.language
    lines = []
    for name in agent._intrinsics:
        if agent._intrinsic_registry.get(name, {}).get("official_plugin"):
            continue
        module = agent._intrinsic_modules.get(name)
        if module:
            base = module.get_description()
            pkg = getattr(module, "__package__", None)
            rendered = append_tool_glossary(base, tool_package=pkg, language=lang)
            lines.append(f"### {name}\n{rendered}")
    for registered in agent._tool_schemas:
        # Prose follows the wire: an undisclosed tool renders its stub's text.
        s = _effective_tool_schema(agent, registered)
        if s.description:
            rendered = append_tool_glossary(
                s.description, tool_package=s.glossary_package, language=lang
            )
            lines.append(f"### {s.name}\n{rendered}")
    if lines:
        agent._prompt_manager.write_section(
            "tools", "\n\n".join(lines), protected=True
        )


def _build_tool_schemas(agent) -> list[FunctionSchema]:
    """Build the complete tool schema list for the LLM.

    Every tool gets a 'reasoning' parameter injected — the agent must
    explain why it's calling this tool. Reasoning is logged as part of
    the agent's diary and stripped before the handler runs.

    A stub-bearing dynamic tool contributes its compact stub until it is
    disclosed (``_disclose_tool``); ``SessionManager`` calls this builder on
    every send, so a disclosure is visible on the very next provider round.
    """
    reasoning_prop = {
        "reasoning": {
            "type": "string",
            "description": _REASONING_DESCRIPTION,
        },
    }

    schemas = []

    # Intrinsic schemas — canonical English, language-independent.
    for name in agent._intrinsics:
        if agent._intrinsic_registry.get(name, {}).get("official_plugin"):
            continue
        module = agent._intrinsic_modules.get(name)
        if module:
            params = dict(module.get_schema())
            props = dict(params.get("properties", {}))
            props.update(reasoning_prop)
            params["properties"] = props
            schemas.append(
                FunctionSchema(
                    name=name,
                    description=module.get_description(),
                    parameters=params,
                )
            )

    # Capability + MCP schemas — inject reasoning into each
    for registered in agent._tool_schemas:
        s = _effective_tool_schema(agent, registered)
        params = dict(s.parameters)
        props = dict(params.get("properties", {}))
        props.update(reasoning_prop)
        params["properties"] = props
        schemas.append(
            FunctionSchema(
                name=s.name,
                description=s.description,
                parameters=params,
            )
        )

    return schemas


def _add_tool(
    agent,
    name: str,
    *,
    schema: dict | None = None,
    handler=None,
    description: str = "",
    system_prompt: str = "",
    glossary_package: str | None = None,
    stub: FunctionSchema | None = None,
    disclosure_sources: tuple[str, ...] = (),
    _official_mount_token=None,
) -> None:
    """Register a dynamic tool at the common model-facing mount boundary.

    Official names are statically reserved by the kernel. Only the private
    registrar-owned mount route may publish one; ordinary ``add_tool`` callers
    retain the historical same-name replacement behavior for every other name.

    ``stub`` (a compact same-name ``FunctionSchema``) defers provider
    disclosure of ``schema``: the stub is advertised until the tool is called
    or a notification from ``disclosure_sources`` is delivered. The handler
    and the full ``schema`` are registered immediately either way.
    """
    from ..tool_plugin import (
        OFFICIAL_TOOL_PLUGIN_NAMES,
        OfficialToolNameCollisionError,
        _OFFICIAL_MOUNT_TOKEN,
    )
    if (
        name in OFFICIAL_TOOL_PLUGIN_NAMES
        and _official_mount_token is not _OFFICIAL_MOUNT_TOKEN
    ):
        raise OfficialToolNameCollisionError(
            f"tool name {name!r} is reserved for an official plugin and cannot "
            "be mounted by a generic or external MCP route"
        )
    if agent._sealed:
        raise RuntimeError("Cannot modify tools after start()")
    if handler is not None:
        agent._tool_handlers[name] = handler
    if schema is not None:
        if stub is not None and stub.name != name:
            raise ValueError(
                f"stub for tool {name!r} must carry the same name, got {stub.name!r}"
            )
        # Remove any existing schema with same name
        agent._tool_schemas = [s for s in agent._tool_schemas if s.name != name]
        agent._tool_schemas.append(
            FunctionSchema(
                name=name,
                description=description,
                parameters=schema,
                system_prompt=system_prompt,
                glossary_package=glossary_package,
                stub=stub,
                disclosure_sources=tuple(disclosure_sources),
            )
        )
    # Update the live session's tools if one exists
    if agent._chat is not None:
        agent._chat.update_tools(_build_tool_schemas(agent))
    agent._token_decomp_dirty = True


def _remove_tool(agent, name: str) -> None:
    """Unregister a dynamic tool, except for a statically reserved official name."""
    from ..tool_plugin import OFFICIAL_TOOL_PLUGIN_NAMES, OfficialToolNameCollisionError
    if name in OFFICIAL_TOOL_PLUGIN_NAMES:
        raise OfficialToolNameCollisionError(
            f"tool name {name!r} is reserved for an official plugin and cannot "
            "be removed by a generic route"
        )
    if agent._sealed:
        raise RuntimeError("Cannot modify tools after start()")
    agent._tool_handlers.pop(name, None)
    agent._tool_schemas = [s for s in agent._tool_schemas if s.name != name]
    # A later re-registration starts collapsed again.
    _disclosed_tools(agent).discard(name)
    if agent._chat is not None:
        agent._chat.update_tools(_build_tool_schemas(agent))
    agent._token_decomp_dirty = True


def _override_intrinsic(agent, name: str):
    """Remove an intrinsic and return its handler for delegation.

    Called by capabilities that upgrade an intrinsic.
    Must be called before start() (tool surface sealed).

    Returns the original handler so the capability can delegate to it.
    """
    if agent._sealed:
        raise RuntimeError("Cannot modify tools after start()")
    handler = agent._intrinsics.pop(name)  # raises KeyError if missing
    agent._token_decomp_dirty = True
    return handler


def _has_capability(agent, name: str) -> bool:
    """Check if a capability is registered. Subclasses override."""
    return False
