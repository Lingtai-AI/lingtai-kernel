"""Email intrinsic — filesystem-based mailbox with search and contacts.

Re-exports the full public surface of the former monolithic email.py so all
existing import sites continue to work unchanged.

The tool is an LTP v2 family (``../CONTRACT.md``): the model-facing root is
the closed ``action`` + ``input`` + ``reasoning`` + ``summarize`` envelope,
and each action's arguments live in its own strict ``input`` object composed
by the generic ``ToolFamily`` infra (``lingtai.tools.tool_family``) from the
per-action schemas in :mod:`._family_schema`. The public tool name and thirteen
operational action values stay unchanged; the generic opt-in inserts only
``settings`` before ``manual``. ``EmailManager.handle``'s historical flat
argument shape is retained
unchanged as a purely *internal* interface — the same seam ``shell`` kept for
its ``ShellManager`` (``tools/CONTRACT.md`` "Relationship to current
runtime") — and :func:`handle` translates the envelope into it.

Sub-modules:
    primitives.py     — Mailbox I/O, ID generation, read tracking, delivery, display.
    _family_schema.py — Canonical per-action ``input`` schemas + action order.
    settings.py       — Read-only Email settings rows and policy constants.
    schema.py         — Legacy flat schema/description (internal manager shape).
    manager.py        — EmailManager class (the core filesystem manager).

Storage layout:
    working_dir/mailbox/inbox/{uuid}/message.json     — received
    working_dir/mailbox/sent/{uuid}/message.json      — sent
    working_dir/mailbox/archive/{uuid}/message.json   — archived from inbox
    working_dir/mailbox/read.json                     — read tracking
    working_dir/mailbox/contacts.json                 — contact book

Internal:
    boot(agent) — instantiates EmailManager on agent._email_manager.
        Called from base_agent during agent construction.
    handle(agent, args) — module-level dispatcher; delegates to the manager.

Note: recurring/scheduled sends were removed in favor of cron. The email
tool is now request/response only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from lingtai.kernel.notifications import register_generic_dismiss_guard
from lingtai.kernel.tool_plugin import BoundToolPlugin, ToolPluginDeclaration

from .._manual import load_installed_manual  # noqa: F401  (public re-export)
from ..tool_family import ChildTool, ToolFamily
from ..tool_family.manual import MANUAL_INPUT_SCHEMA, build_manual_child

if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent
    from lingtai.kernel.tool_plugin import ToolPluginHost

register_generic_dismiss_guard(
    "email",
    (
        "email(action='dismiss', input={'email_id': [...]}, reasoning='handled') "
        "or email(action='read', input={'email_id': [...]}, reasoning='refresh')"
    ),
)


# ---------------------------------------------------------------------------
# Email-owned runtime boundary
# ---------------------------------------------------------------------------

EmailInput = Mapping[str, object]
EmailResult = dict[str, object]


@dataclass(frozen=True, slots=True)
class EmailRuntimeRequest:
    """One validated Email action request crossing the manager boundary."""

    action: str
    input: EmailInput


class EmailRuntimePort(Protocol):
    """Capability-native port for the already-wired Email manager runtime."""

    def handle_email(self, request: EmailRuntimeRequest) -> EmailResult:
        """Execute one Email action without exposing an Agent or generic lookup."""

    def read_pseudo_agent_subscriptions(self) -> tuple[str, ...]:
        """Read the effective mail-adapter construction snapshot."""


# --- Re-exports from sub-modules for backward compatibility ---

# Primitives (mailbox I/O, helpers)
from .primitives import (  # noqa: F401
    _coerce_address_list,
    _email_time,
    _inbox_dir,
    _is_self_send,
    _list_inbox,
    _load_message,
    _mailbox_dir,
    _mailman,
    _mark_read,
    _message_summary,
    _move_to_sent,
    _new_mailbox_id,
    _outbox_dir,
    _persist_to_inbox,
    _persist_to_outbox,
    _preview,
    _read_ids,
    _read_ids_path,
    _render_unread_digest,
    _rerender_unread_digest,
    _save_read_ids,
    _sent_dir,
    _summary_to_list,
    _unread_notification_context,
)

# Schema — the legacy flat description/schema. ``get_description`` remains the
# registered intrinsic description; ``get_schema`` is re-exported under its
# historical name ``schema.get_schema`` but is NO LONGER the model-facing
# schema (see ``get_schema`` below, which shadows it with the composed one).
from .schema import get_description  # noqa: F401
from .schema import get_schema as get_flat_schema  # noqa: F401

# Canonical per-action data driving the composed model-facing schema.
from ._family_schema import ACTION_ENUM_DESCRIPTION, ACTION_ORDER, INPUT_SCHEMAS

# Manager
from .manager import EmailManager  # noqa: F401
from .settings import email_settings_rows


# ---------------------------------------------------------------------------
# LTP v2 family composition — one model-facing root, one child per action
# ---------------------------------------------------------------------------

# The exact pre-migration reserved-action rejection for ``unread``. It is a
# kernel-synthesized digest action, NOT a public child: it is absent from
# ``ACTION_ORDER`` and therefore from the ``action`` enum, so the generic
# dispatcher would answer it with its own generic ``ACTION_REQUIRED``
# envelope. This exact message is a pinned public promise
# (``CONTRACT.md`` "Tool surface"), so :func:`handle` renders it itself,
# before delegating — the same Host-boundary seam ``mcp`` uses for its own
# legacy envelope, and never a widening of the generic dispatcher.
_UNREAD_RESERVED_RESULT: dict[str, Any] = {
    "status": "error",
    "message": (
        "email(action='unread', ...) is reserved for kernel-"
        "synthesized unread-mail digests and cannot be invoked "
        "directly. Use email(action='check') to view your inbox."
    ),
}


def _schema_only_family() -> ToolFamily:
    """Build the module-level ``ToolFamily`` used only to compose the schema.

    Email is an *intrinsic*: the kernel imports this module once and calls
    ``get_schema()``/``handle(agent, args)`` on the module itself, so unlike
    ``web`` there is no per-Agent manager instance to hang a family off at
    import time. The real handlers need an ``agent``, which only arrives per
    call, so :func:`handle` builds a per-call family with bound handlers and
    this module-level one never dispatches. Constructing it at import time is
    still load-bearing: it proves the fixed fifteen-child public registry has
    no duplicate or reserved-name collision
    (``ToolFamilyError`` raises here, at import, rather than shipping
    silently).
    """

    def _unused(_input: Mapping[str, Any]) -> dict[str, Any]:
        raise AssertionError("the module-level schema-only ToolFamily never dispatches")

    schemas = DECLARATION.public_input_schemas()
    return ToolFamily(
        DECLARATION.name,
        [
            ChildTool(action, schemas[action], _unused, title=f"{action} input")
            for action in (*DECLARATION.actions, "manual")
        ],
        settings_provider=email_settings_rows,
    )


def get_schema(lang: str = "en") -> dict[str, Any]:
    """Return the composed model-facing ``email`` family schema.

    Composed by the generic ``ToolFamily`` infra from each action's own
    canonical ``input_schema`` in :mod:`._family_schema`, rather than
    hand-assembled. ``lang`` is accepted for source compatibility and ignored:
    schema prose is canonical English and language-independent, exactly as
    ``CONTRACT.md`` "Schema and glossary ownership" already promised.

    This shadows the legacy flat ``schema.get_schema`` (re-exported above as
    ``get_flat_schema``), which is now only the internal ``EmailManager``
    argument shape.
    """
    schema = _FAMILY.build_schema()
    # The generic composer writes a neutral "Required operation within the
    # email family." description. Email's own per-action prose (the 50,000-char
    # cap rationale, the read-vs-dismiss preference, the reply discipline) is
    # the model's only in-schema guide to *which* action to pick, so it
    # replaces that placeholder here rather than being lost.
    schema["properties"]["action"]["description"] = ACTION_ENUM_DESCRIPTION
    return schema


def _strip_nulls(action_input: Mapping[str, Any]) -> dict:
    """Drop explicit ``null``s so absent-vs-null defaulting is preserved.

    Strict provider schemas express an optional field as a required nullable
    property, so the model sends ``{"folder": null}`` for "no folder". The
    pre-existing ``EmailManager`` handlers distinguish absent from present via
    ``args.get("folder", "inbox")``, ``args.get("folder")`` (``_read``:
    ``if folder:`` selects a direct path lookup), ``_search``'s
    "folder or both", and ``_edit_contact``'s ``if "name" in args``. Null must
    therefore become *absent* before they run, or ``check`` would list a
    folder named ``None`` and ``edit_contact`` would blank a stored name the
    caller never mentioned.

    Strict-provider nested objects need the same one-level normalization.
    ``check.filter`` is mostly read through truthiness, but ``truncate`` uses
    ``f.get("truncate", 500)`` and therefore treats an explicit ``null`` as
    ``None`` rather than as the historical 500-character default. Drop nested
    nulls at the family boundary while leaving the manager's flat interface
    and defaulting rules unchanged.
    """
    cleaned: dict[str, Any] = {}
    for key, value in action_input.items():
        if value is None:
            continue
        if isinstance(value, Mapping):
            value = {
                nested_key: nested_value
                for nested_key, nested_value in value.items()
                if nested_value is not None
            }
        cleaned[key] = value
    return cleaned


def _adapt_manual_result(mcp_result: dict) -> dict:
    """Flatten the ManualTool child's canonical result to Email's exact shape.

    ``ToolFamily.handle()`` has already dispatched to the registered ``manual``
    child (``tool_family.manual.build_manual_child``) and returned its
    canonical result *verbatim* (no double wrap) — full body at
    ``content[0].text``, host-local path at
    ``structuredContent.manual_path``. Email's pre-migration public result was
    ``load_installed_manual(agent, "email")``'s own flat dict — exactly
    ``status`` / ``manual`` / ``manual_path`` (+ ``error`` when degraded) — so
    this Host-owned adapter runs strictly *after* dispatch, in :func:`handle`,
    never inside a registered child, per the no-double-wrap rule
    (``tool_family/CONTRACT.md`` "Dispatch and actions").

    The generic result carries no field Email did not expose pre-migration:
    ``content``/``structuredContent`` are dropped here rather than added to
    Email's public shape, and the loader's ``error`` sentence is forwarded
    verbatim because this family's installed skill directory really is named
    ``email`` — so the loader's own ``f"{skill_name} manual missing — ..."``
    text is byte-identical to the pre-migration one, with nothing to restate.
    """
    flat: dict[str, Any] = {
        "status": mcp_result.get("status", "ok"),
        "manual": mcp_result["content"][0]["text"],
        "manual_path": mcp_result["structuredContent"]["manual_path"],
    }
    if "error" in mcp_result:
        flat["error"] = mcp_result["error"]
    return flat


# The operational action inventory is derived from the same canonical order that
# builds the legacy intrinsic family. `manual` remains the declaration-owned
# reserved action and is appended exactly once by ToolPluginDeclaration.
_EMAIL_DECLARED_ACTIONS = ACTION_ORDER[:-1]


def _build_bound_family(host: "ToolPluginHost") -> ToolFamily:
    """Compose Email's official surface against only its granted host ports.

    Email's filesystem manager remains Agent-bound because mailbox arrival
    hooks and delivery/identity semantics share that live runtime. The declared
    family receives no Agent; each operational child consumes the Email-owned
    ``EmailRuntimePort`` granted by the host. Its production adapter reads the
    current manager at call time, while the package-owned `manual` child reads
    only the separate read-only workdir port.
    """

    runtime: EmailRuntimePort = host.email_runtime

    def _dispatch(action: str):
        def call(action_input: Mapping[str, Any]) -> dict:
            return runtime.handle_email(
                EmailRuntimeRequest(action=action, input=_strip_nulls(action_input))
            )

        return call

    return ToolFamily(
        DECLARATION.name,
        [
            *[
                ChildTool(
                    action,
                    DECLARATION.input_schemas[action],
                    _dispatch(action),
                    title=f"{action} input",
                )
                for action in DECLARATION.actions
            ],
            build_manual_child(host.workdir, DECLARATION.manual),
        ],
        settings_provider=lambda: email_settings_rows(
            runtime.read_pseudo_agent_subscriptions
        ),
    )


def _bound_handler(family: ToolFamily, args: dict) -> dict:
    """Preserve Email's host-owned reserved/legacy result adaptations."""
    raw = dict(args or {})
    raw.pop("_tc_id", None)
    action = raw.get("action")
    if action == "unread":
        return dict(_UNREAD_RESERVED_RESULT)
    result = family.handle(raw)
    if action == "manual" and "content" in result:
        return _adapt_manual_result(result)
    if result.get("error_code") == "ACTION_REQUIRED":
        if not action:
            return {"error": "action is required"}
        return {"error": f"Unknown email action: {action}"}
    return result


def _bind(host: "ToolPluginHost") -> BoundToolPlugin:
    """Bind Email without mounting it or exposing the Agent."""
    family = _build_bound_family(host)
    return BoundToolPlugin(
        name=DECLARATION.name,
        schema=get_schema(),
        handler=lambda args: _bound_handler(family, args),
        description=get_description(),
        glossary_package=__package__,
    )


#: Static declaration of the official Email family.  It is created at import,
#: before an Agent exists; the kernel validates the action/manual/schema shape
#: then and validates the composed action inventory again at every bind.
DECLARATION = ToolPluginDeclaration(
    name="email",
    actions=_EMAIL_DECLARED_ACTIONS,
    input_schemas={action: INPUT_SCHEMAS[action] for action in _EMAIL_DECLARED_ACTIONS},
    manual_input_schema=MANUAL_INPUT_SCHEMA,
    manual="email",
    description=get_description(),
    binder=_bind,
    # The Email-owned runtime port carries operational calls; workdir is used
    # only by the package-owned manual child.
    requires=("workdir", "email_runtime"),
    glossary_package=__package__,
    settings=True,
)


# Built only after the static declaration exists, so the advertised public
# inventory has no independently restated name, action list, or manual schema.
_FAMILY = _schema_only_family()


def _build_family(agent) -> ToolFamily:
    """Build the per-call dispatching family with handlers bound to *agent*.

    Every non-``manual`` child re-enters the unchanged ``EmailManager.handle``
    with its historical flat argument shape (``{"action": ..., **input}``),
    which keeps the whole engine — delivery threads, the duplicate-send guard,
    read tracking, the digest rerender, reply routing, contacts — untouched by
    this migration. The child's own strict ``input_schema`` is what makes that
    safe: ``ToolFamily.handle()`` has already rejected any key outside the
    selected action's own branch before the handler runs, so no cross-action
    field can reach the flat dispatcher.

    The reserved ``manual`` child is registered directly and unwrapped, per
    ``tool_family/CONTRACT.md``: ``ToolFamily.handle()`` returns its canonical
    ``content``/``structuredContent`` result verbatim, and
    :func:`_adapt_manual_result` reshapes it afterwards in :func:`handle`.
    """

    def _bind(action: str):
        def _dispatch(action_input: Mapping[str, Any]) -> dict[str, Any]:
            mgr = getattr(agent, "_email_manager", None)
            if mgr is None:
                return {
                    "error": (
                        "Internal: email manager not initialized. "
                        "boot() was not called."
                    )
                }
            return mgr.handle({"action": action, **_strip_nulls(action_input)})

        return _dispatch

    children = [
        ChildTool(
            action,
            DECLARATION.input_schemas[action],
            _bind(action),
            title=f"{action} input",
        )
        for action in DECLARATION.actions
    ]
    children.append(build_manual_child(agent, DECLARATION.manual))

    def _read_pseudo_agent_subscriptions() -> tuple[str, ...]:
        service = getattr(agent, "_mail_service", None)
        if service is None:
            raise RuntimeError("Email mail service is unavailable")
        return service.pseudo_agent_subscriptions

    return ToolFamily(
        DECLARATION.name,
        children,
        settings_provider=lambda: email_settings_rows(
            _read_pseudo_agent_subscriptions
        ),
    )


# ---------------------------------------------------------------------------
# Module-level intrinsic protocol — handle() + boot()
# ---------------------------------------------------------------------------


def handle(agent, args: dict) -> dict:
    """Handle the ``email`` family root — validate the envelope, dispatch one action.

    The generic ``ToolFamily`` dispatcher validates ``action``, type-checks and
    strips root ``summarize``, rejects unknown root fields, and — crucially for
    a mutating family — rejects ``input`` keys outside the selected action's
    own declared schema *before* any handler runs. That is what makes a
    cross-action smuggle such as ``action='check', input={'email_id': [...]}``
    fail with no mailbox I/O at all.

    ``_tc_id`` is stripped first. ``base_agent.tools._dispatch_tool`` injects
    it into **every** intrinsic's args (only ``psyche`` molt consumes it), so
    it is kernel plumbing that predates and is invisible to the LTP v2
    envelope — the same boundary ``notification`` owns.

    Two Email-specific results are rendered here, before/after the generic
    dispatcher, rather than by changing its canonical shapes:

    * the reserved ``unread`` action's exact rejection, which must still fire
      *before* dispatch because ``unread`` is deliberately not a public child;
    * ``manual``'s pre-migration flat public shape, adapted strictly after
      dispatch (:func:`_adapt_manual_result`).

    An unknown or absent action keeps the pre-migration public result exactly
    (``{"error": "Unknown email action: <x>"}`` / ``{"error": "action is
    required"}``), which ``CONTRACT.md`` "Tool surface" pins.
    """
    raw = dict(args or {})
    raw.pop("_tc_id", None)

    action = raw.get("action")
    if action == "unread":
        return dict(_UNREAD_RESERVED_RESULT)

    result = _build_family(agent).handle(raw)

    if action == "manual" and "content" in result:
        return _adapt_manual_result(result)
    if result.get("error_code") == "ACTION_REQUIRED":
        # Preserve Email's pre-migration unknown/absent-action results
        # verbatim. ``EmailManager.handle`` returned "action is required" for
        # a falsy action and "Unknown email action: <x>" otherwise; the
        # generic dispatcher collapses both into one envelope failure, so the
        # distinction is restored here at Email's own Host boundary.
        if not action:
            return {"error": "action is required"}
        return {"error": f"Unknown email action: {action}"}
    return result


def boot(agent) -> None:
    """Create Email's live manager, then mount its declared official surface.

    Re-boot during refresh deliberately replaces the manager before the host
    binds the declaration.  The granted production adapter reads
    ``agent._email_manager`` at invocation time, so a later replacement is
    observed by already-bound handlers as well.
    """
    agent._email_manager = EmailManager(agent)
    agent._mailbox_name = "email box"
    agent._mailbox_tool = "email"

    # The daemon_email MCP server uses a deliberately minimal mailbox shim,
    # not a live BaseAgent. It needs the established manager/hook runtime but
    # has no official-tool surface to mount; only a real official Agent follows
    # the registrar path below.
    if not hasattr(agent, "official_tool_plugins"):
        return

    from lingtai.adapters.tool_plugin_host import (
        AgentEmailRuntimeAdapter,
        register_agent_tool_plugins,
    )

    register_agent_tool_plugins(
        agent,
        [DECLARATION],
        extra_ports_for=lambda declaration: (
            {
                "email_runtime": AgentEmailRuntimeAdapter(
                    lambda: getattr(agent, "_email_manager", None),
                    lambda: getattr(
                        getattr(agent, "_mail_service", None),
                        "pseudo_agent_subscriptions",
                    ),
                )
            }
            if declaration is DECLARATION
            else {}
        ),
    )
