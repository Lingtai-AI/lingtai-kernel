"""Static, normally closed channel_reply intrinsic."""
from __future__ import annotations

from typing import Any, Mapping

from lingtai.kernel.channel_reply import (
    ChannelReplySubmitRequest,
    ClosedChannelReplySubmitPort,
    make_fail_closed_receipt,
)
from lingtai.tools.tool_family import ChildTool, ToolFamily
from lingtai.tools.tool_family.manual import build_manual_child

from .schema import ACTION_ENUM_DESCRIPTION, ACTION_ORDER, INPUT_SCHEMAS, get_description

_MANUAL_SKILL_NAME = "channel_reply"


def _schema_only_family() -> ToolFamily:
    def _unused(_input: Mapping[str, Any]) -> dict[str, Any]:
        raise AssertionError("schema-only channel_reply family never dispatches")

    return ToolFamily(
        "channel_reply",
        [
            ChildTool(action, INPUT_SCHEMAS[action], _unused, title=f"{action} input")
            for action in ACTION_ORDER
        ],
    )


_FAMILY = _schema_only_family()


def get_schema(lang: str = "en") -> dict[str, Any]:
    schema = _FAMILY.build_schema()
    schema["properties"]["action"]["description"] = ACTION_ENUM_DESCRIPTION
    return schema


def _submit(agent, action_input: Mapping[str, Any]) -> dict[str, Any]:
    try:
        request = ChannelReplySubmitRequest.from_mapping(action_input)
    except ValueError as exc:
        return make_fail_closed_receipt(action_input, str(exc))
    port = getattr(agent, "_channel_reply_submit_port", None)
    if port is None:
        port = ClosedChannelReplySubmitPort()
    return port.submit_channel_reply(request).to_public_dict()


def _build_family(agent) -> ToolFamily:
    children = [
        ChildTool(
            "submit",
            INPUT_SCHEMAS["submit"],
            lambda action_input: _submit(agent, action_input),
            title="submit input",
        ),
        build_manual_child(agent, _MANUAL_SKILL_NAME),
    ]
    return ToolFamily("channel_reply", children)


def handle(agent, args: dict) -> dict:
    raw = dict(args or {})
    raw.pop("_tc_id", None)
    return _build_family(agent).handle(raw)
