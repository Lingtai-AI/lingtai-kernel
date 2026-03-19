"""AgentState — lifecycle state enum for StoAI agents."""

from __future__ import annotations

import enum


class AgentState(enum.Enum):
    """Lifecycle state of an agent.

    SLEEPING --(inbox message)---> ACTIVE
    ACTIVE   --(idle/stuck/error)-> SLEEPING
    SLEEPING --(CPR timeout)-----> DEAD
    """

    ACTIVE = "active"
    SLEEPING = "sleeping"
    DEAD = "dead"
