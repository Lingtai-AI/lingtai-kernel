"""Read-only five-field settings provider for the Psyche family."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..tool_family import SettingRow, SettingsProvider

if TYPE_CHECKING:
    from lingtai.kernel.tool_plugin import PsycheSettingsPort

__all__ = ["build_settings_provider"]


def build_settings_provider(settings: "PsycheSettingsPort") -> SettingsProvider:
    """Bind Psyche SHOW to the host's last applied Pad configuration."""

    def provide() -> list[SettingRow]:
        snapshot = settings.read_snapshot()
        if (
            not isinstance(snapshot, tuple)
            or len(snapshot) != 2
            or not isinstance(snapshot[0], str)
            or not (snapshot[1] is None or isinstance(snapshot[1], str))
        ):
            raise RuntimeError("Psyche configuration snapshot is unavailable")
        pad, pad_file = snapshot
        return [
            SettingRow(
                "pad",
                pad,
                "",
                True,
                "psyche-manual#setting-pad",
                _sensitive=True,
            ),
            SettingRow(
                "pad_file",
                pad_file,
                None,
                True,
                "psyche-manual#setting-pad-file",
                _sensitive=True,
            ),
        ]

    return provide
