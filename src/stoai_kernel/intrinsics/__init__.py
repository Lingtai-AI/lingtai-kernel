"""Intrinsic tools available to all agents.

Each intrinsic has:
- SCHEMA: JSON Schema dict for tool parameters
- DESCRIPTION: human-readable description
- handle: handler function(agent, args) -> dict
"""
from . import mail, system, eigen, soul

ALL_INTRINSICS = {
    "mail": {"schema": mail.SCHEMA, "description": mail.DESCRIPTION, "handle": mail.handle},
    "system": {"schema": system.SCHEMA, "description": system.DESCRIPTION, "handle": system.handle},
    "eigen": {"schema": eigen.SCHEMA, "description": eigen.DESCRIPTION, "handle": eigen.handle},
    "soul": {"schema": soul.SCHEMA, "description": soul.DESCRIPTION, "handle": soul.handle},
}
