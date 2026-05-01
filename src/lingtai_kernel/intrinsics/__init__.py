"""Intrinsic tools available to all agents.

Each intrinsic module exposes:
- get_schema(lang) -> dict: JSON Schema for tool parameters
- get_description(lang) -> str: human-readable description
- handle(agent, args) -> dict: handler function
"""
from . import mail, system, psyche, soul

ALL_INTRINSICS = {
    "mail": {"module": mail},
    "system": {"module": system},
    "psyche": {"module": psyche},
    "soul": {"module": soul},
}
