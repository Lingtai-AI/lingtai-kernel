"""Avatar-owned immutable policy for read-only settings discovery."""
from __future__ import annotations

from ..tool_family import SettingRow

SPAWN_TYPES = ("shallow", "deep")
SPAWN_TYPE_DEFAULT = "shallow"
SPAWN_DRY_RUN_DEFAULT = False
SPAWN_CONFIRM_DEFAULT = False

AVATAR_NAME_MIN_CHARACTERS = 1
AVATAR_NAME_MAX_CHARACTERS = 64
MISSION_MIN_CHARACTERS = 20
MISSION_PLACEHOLDER_PREFIXES = frozenset(
    {"test", "debug", "check", "tmp", "temp", "foo", "bar"}
)

BOOT_WAIT_SECONDS = 5.0
BOOT_POLL_INTERVAL_SECONDS = 0.1
BOOT_STDERR_TAIL_BYTES = 2_000

_CALL_DEFAULTS = "avatar-manual#spawn-call-defaults"
_VALIDATION_POLICY = "avatar-manual#spawn-validation-policy"
_LIFECYCLE_POLICY = "avatar-manual#spawn-lifecycle-policy"


class AvatarSettingsProvider:
    """Return fresh effective Avatar defaults and immutable owner policy."""

    def __call__(self) -> tuple[SettingRow, ...]:
        return (
            SettingRow(
                "spawn.type.default",
                SPAWN_TYPE_DEFAULT,
                SPAWN_TYPE_DEFAULT,
                False,
                _CALL_DEFAULTS,
            ),
            SettingRow(
                "spawn.type.allowed",
                list(SPAWN_TYPES),
                list(SPAWN_TYPES),
                False,
                _VALIDATION_POLICY,
            ),
            SettingRow(
                "spawn.dry_run.default",
                SPAWN_DRY_RUN_DEFAULT,
                SPAWN_DRY_RUN_DEFAULT,
                False,
                _CALL_DEFAULTS,
            ),
            SettingRow(
                "spawn.confirm.default",
                SPAWN_CONFIRM_DEFAULT,
                SPAWN_CONFIRM_DEFAULT,
                False,
                _CALL_DEFAULTS,
            ),
            SettingRow(
                "spawn.name.minimum_characters",
                AVATAR_NAME_MIN_CHARACTERS,
                AVATAR_NAME_MIN_CHARACTERS,
                False,
                _VALIDATION_POLICY,
            ),
            SettingRow(
                "spawn.name.maximum_characters",
                AVATAR_NAME_MAX_CHARACTERS,
                AVATAR_NAME_MAX_CHARACTERS,
                False,
                _VALIDATION_POLICY,
            ),
            SettingRow(
                "spawn.mission.minimum_characters",
                MISSION_MIN_CHARACTERS,
                MISSION_MIN_CHARACTERS,
                False,
                _VALIDATION_POLICY,
            ),
            SettingRow(
                "spawn.mission.placeholder_prefixes",
                sorted(MISSION_PLACEHOLDER_PREFIXES),
                sorted(MISSION_PLACEHOLDER_PREFIXES),
                False,
                _VALIDATION_POLICY,
            ),
            SettingRow(
                "spawn.boot.wait_seconds",
                BOOT_WAIT_SECONDS,
                BOOT_WAIT_SECONDS,
                False,
                _LIFECYCLE_POLICY,
            ),
            SettingRow(
                "spawn.boot.poll_interval_seconds",
                BOOT_POLL_INTERVAL_SECONDS,
                BOOT_POLL_INTERVAL_SECONDS,
                False,
                _LIFECYCLE_POLICY,
            ),
            SettingRow(
                "spawn.boot.stderr_tail_bytes",
                BOOT_STDERR_TAIL_BYTES,
                BOOT_STDERR_TAIL_BYTES,
                False,
                _LIFECYCLE_POLICY,
            ),
            SettingRow(
                "spawn.preset_policy",
                "parent-default",
                "parent-default",
                False,
                _LIFECYCLE_POLICY,
            ),
            SettingRow(
                "spawn.environment_policy",
                "inherit-launcher-process",
                "inherit-launcher-process",
                False,
                _LIFECYCLE_POLICY,
            ),
            SettingRow(
                "spawn.lifecycle_policy",
                "detached-independent",
                "detached-independent",
                False,
                _LIFECYCLE_POLICY,
            ),
            SettingRow(
                "spawn.admin_inheritance",
                "none",
                "none",
                False,
                _LIFECYCLE_POLICY,
            ),
        )


__all__ = [
    "AvatarSettingsProvider",
    "AVATAR_NAME_MAX_CHARACTERS",
    "AVATAR_NAME_MIN_CHARACTERS",
    "BOOT_POLL_INTERVAL_SECONDS",
    "BOOT_STDERR_TAIL_BYTES",
    "BOOT_WAIT_SECONDS",
    "MISSION_MIN_CHARACTERS",
    "MISSION_PLACEHOLDER_PREFIXES",
    "SPAWN_CONFIRM_DEFAULT",
    "SPAWN_DRY_RUN_DEFAULT",
    "SPAWN_TYPE_DEFAULT",
    "SPAWN_TYPES",
]
