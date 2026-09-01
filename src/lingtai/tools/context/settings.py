"""Context-owned provider for read-only five-field settings discovery."""
from __future__ import annotations

from collections.abc import Callable

from lingtai.adapters.tool_plugin_host import AgentContextRuntimeAdapter
from lingtai.kernel.base_agent.messaging import (
    DEFAULT_SUMMARIZE_NOTIFICATION_THRESHOLD,
)
from lingtai.kernel.config import (
    CONTEXT_PRESSURE_FORCED_REBUILD_RATIO,
    CONTEXT_PRESSURE_HIGH_RATIO,
    CONTEXT_PRESSURE_RECOVERY_TARGET,
    CONTEXT_PRESSURE_WARN_AFTER_ROUNDS,
    DEFAULT_SYSTEM_PROMPT_PRESSURE_RATIO,
    system_prompt_pressure_ratio,
)
from lingtai.llm.service import CONSERVATIVE_CONTEXT_WINDOW
from lingtai.tools.tool_family import SettingRow, SettingsProvider


_ValueReader = Callable[[], object]


def _positive_integer(value: object, key: str) -> int:
    if type(value) is not int or value <= 0:
        raise RuntimeError(f"current {key} is unavailable")
    return value


def _nonnegative_integer(value: object, key: str) -> int:
    if type(value) is not int or value < 0:
        raise RuntimeError(f"current {key} is unavailable")
    return value


def build_context_settings_provider(
    read_context_limit: _ValueReader,
    read_summarize_notification_threshold: _ValueReader,
) -> SettingsProvider:
    """Build the fresh provider bound to this Context runtime's live readers."""
    if not callable(read_context_limit) or not callable(
        read_summarize_notification_threshold
    ):
        raise TypeError("Context settings readers must be callable")

    def provide() -> tuple[SettingRow, ...]:
        context_limit = _positive_integer(read_context_limit(), "context_limit")
        summarize_threshold = _nonnegative_integer(
            read_summarize_notification_threshold(),
            "summarize_notification_threshold",
        )
        return (
            SettingRow(
                "context_limit",
                context_limit,
                CONSERVATIVE_CONTEXT_WINDOW,
                True,
                "context-manual#context-limit",
            ),
            SettingRow(
                "summarize_notification_threshold",
                summarize_threshold,
                DEFAULT_SUMMARIZE_NOTIFICATION_THRESHOLD,
                True,
                "context-manual#summarize-notification-threshold",
            ),
            SettingRow(
                "system_prompt_pressure_ratio",
                system_prompt_pressure_ratio(),
                DEFAULT_SYSTEM_PROMPT_PRESSURE_RATIO,
                True,
                "context-manual#system-prompt-pressure-ratio",
            ),
            SettingRow(
                "pressure_high_ratio",
                CONTEXT_PRESSURE_HIGH_RATIO,
                CONTEXT_PRESSURE_HIGH_RATIO,
                False,
                "context-manual#pressure-high-ratio",
            ),
            SettingRow(
                "forced_rebuild_ratio",
                CONTEXT_PRESSURE_FORCED_REBUILD_RATIO,
                CONTEXT_PRESSURE_FORCED_REBUILD_RATIO,
                False,
                "context-manual#forced-rebuild-ratio",
            ),
            SettingRow(
                "pressure_warn_after_rounds",
                CONTEXT_PRESSURE_WARN_AFTER_ROUNDS,
                CONTEXT_PRESSURE_WARN_AFTER_ROUNDS,
                False,
                "context-manual#pressure-warn-after-rounds",
            ),
            SettingRow(
                "recovery_target_ratio",
                CONTEXT_PRESSURE_RECOVERY_TARGET,
                CONTEXT_PRESSURE_RECOVERY_TARGET,
                False,
                "context-manual#recovery-target-ratio",
            ),
        )

    return provide


class ContextRuntimeSettingsAdapter(AgentContextRuntimeAdapter):
    """Existing Context lifecycle adapter plus its read-only row provider."""

    __slots__ = ("_settings",)

    def __init__(
        self,
        *,
        molt: Callable[[dict], dict],
        summarize: Callable[[dict], dict],
        rebuild: Callable[[dict], dict],
        settings: SettingsProvider,
    ) -> None:
        super().__init__(molt=molt, summarize=summarize, rebuild=rebuild)
        self._settings = settings

    def settings(self):
        return self._settings()


__all__ = [
    "ContextRuntimeSettingsAdapter",
    "build_context_settings_provider",
]
