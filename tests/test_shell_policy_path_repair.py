"""Focused tests for explicit deny-list token/path semantics."""
from __future__ import annotations

from types import SimpleNamespace

from lingtai.tools.bash import BashManager, BashPolicy
from tests._notification_store_helpers import notification_store_for


def _manager(tmp_path, policy):
    return BashManager(
        policy=policy,
        working_dir=str(tmp_path),
        agent=SimpleNamespace(_notification_store=notification_store_for(tmp_path)),
    )


def test_basename_deny_matches_bare_relative_and_absolute_tokens(tmp_path):
    policy = BashPolicy(deny=["rm"])
    assert policy.is_allowed("rm --version") is False
    assert policy.is_allowed("./rm --version") is False
    assert policy.is_allowed("/bin/rm --version") is False
    assert policy.is_allowed("echo --version") is True


def test_exact_path_deny_does_not_become_a_global_basename_rule():
    policy = BashPolicy(deny=["/bin/rm"])
    assert policy.is_allowed("/bin/rm --version") is False
    assert policy.is_allowed("rm --version") is True
    assert policy.is_allowed("./rm --version") is True
    assert BashPolicy(deny=[r"C:\\bin\\rm"]).is_allowed(r"C:\\bin\\rm --version") is False


def test_allowlist_and_yolo_controls_are_unchanged(tmp_path):
    assert BashPolicy(allow=["echo"]).is_allowed("echo ok") is True
    assert BashPolicy(allow=["echo"]).is_allowed("/bin/echo ok") is False
    assert BashPolicy.yolo().is_allowed("/bin/rm --version") is True

    manager = _manager(tmp_path, BashPolicy(deny=["rm"]))
    assert manager._validate_command("/bin/rm --version")["status"] == "error"
    assert manager._validate_command("echo ok") is None


def test_case_variants_remain_unknown_on_posix_policy_without_filesystem_resolution():
    # This narrow patch does not claim that a case variant resolves to the
    # same executable. A future resolver must use cwd/PATH and filesystem
    # identity rather than a host-OS label or universal casefold.
    assert BashPolicy(deny=["rm"]).is_allowed("RM --version") is True
