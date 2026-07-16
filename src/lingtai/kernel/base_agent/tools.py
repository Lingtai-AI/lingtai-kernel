"""Tool surface — schemas, dispatch, inventory refresh, and tool registry.

The 2-layer tool dispatch: intrinsics (built-in) + capabilities/MCP.
"""
from __future__ import annotations

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
        return agent._tool_handlers[tc.name](tc.args or {})
    elif tc.name == "bash" and "shell" in agent._tool_handlers:
        # One-way rolling compatibility for historical/pending calls.  Do not
        # register a second schema or expose ``bash`` in provider tools.
        return agent._tool_handlers["shell"](tc.args or {})
    else:
        raise UnknownToolError(tc.name)


def _refresh_tool_inventory_section(agent) -> None:
    """Rebuild the 'tools' section from current intrinsic + schema descriptions.

    Each tool's full canonical English description (``get_description()`` for
    intrinsics, ``FunctionSchema.description`` for dynamic/MCP tools) is
    appended with the selected-language glossary body from its owning package.
    Provider wire descriptions/schemas are a separate surface
    (``_build_tool_schemas`` + ``WIRE_TOOL_DESCRIPTION``); this section never
    affects them.
    """
    lang = agent._config.language
    lines = []
    for name in agent._intrinsics:
        module = agent._intrinsic_modules.get(name)
        if module:
            base = module.get_description()
            pkg = getattr(module, "__package__", None)
            rendered = append_tool_glossary(base, tool_package=pkg, language=lang)
            lines.append(f"### {name}\n{rendered}")
    for s in agent._tool_schemas:
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
    for s in agent._tool_schemas:
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
) -> None:
    """Register a dynamic tool."""
    if agent._sealed:
        raise RuntimeError("Cannot modify tools after start()")
    if handler is not None:
        agent._tool_handlers[name] = handler
    if schema is not None:
        # Remove any existing schema with same name
        agent._tool_schemas = [s for s in agent._tool_schemas if s.name != name]
        agent._tool_schemas.append(
            FunctionSchema(
                name=name,
                description=description,
                parameters=schema,
                system_prompt=system_prompt,
                glossary_package=glossary_package,
            )
        )
    # Update the live session's tools if one exists
    if agent._chat is not None:
        agent._chat.update_tools(_build_tool_schemas(agent))
    agent._token_decomp_dirty = True


def _remove_tool(agent, name: str) -> None:
    """Unregister a dynamic tool."""
    if agent._sealed:
        raise RuntimeError("Cannot modify tools after start()")
    agent._tool_handlers.pop(name, None)
    agent._tool_schemas = [s for s in agent._tool_schemas if s.name != name]
    if agent._chat is not None:
        agent._chat.update_tools(_build_tool_schemas(agent))
    agent._token_decomp_dirty = True


def _override_intrinsic(agent, name: str):
    """Remove an intrinsic and return its handler for delegation.

    Called by capabilities that upgrade an intrinsic (e.g. email → mail).
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
