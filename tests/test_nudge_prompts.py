import pytest

from lingtai.kernel.nudge.prompts import (
    INSTALL_ROUTE,
    NudgeFacts,
    NudgeSituation,
    SKILL_ROUTE,
    render_nudge_payload,
)


def test_installed_runtime_mismatch_routes_authorized_refresh():
    payload = render_nudge_payload(
        NudgeSituation.INSTALLED_RUNTIME_MISMATCH,
        NudgeFacts(running="0.16.3", installed="0.16.4", checked_at_date="2026-07-16"),
    )

    assert "skill" not in payload
    assert "install_url" not in payload
    assert payload["running"] == "0.16.3"
    assert payload["installed"] == "0.16.4"
    assert payload["source"] == "installed-distribution"
    detail = payload["detail"]
    assert "already on disk" in detail
    assert "not a release download or migration" in detail
    assert "explicit human/config-owner authorization for refresh" in detail
    assert "this nudge is not authorization" in detail
    assert "system(action='refresh')" in detail


@pytest.mark.parametrize(
    ("running", "installed"),
    [("0.17.0", "0.16.5"), ("not-a-version", "0.16.5"), ("0.17.0", "unknown")],
)
def test_non_installed_newer_mismatch_is_diagnostic_only(running, installed):
    payload = render_nudge_payload(
        NudgeSituation.RUNTIME_MISMATCH_DIAGNOSTIC,
        NudgeFacts(running=running, installed=installed),
    )
    assert payload["suggested_action"] == "inspect-runtime-interpreter-and-import-paths"
    assert "Do not refresh, downgrade, install" in payload["detail"]
    assert "already on disk" not in payload["detail"]
    assert "system(action='refresh')" not in payload["detail"]


def test_package_update_points_to_installer_and_requires_authorization():
    payload = render_nudge_payload(
        NudgeSituation.PACKAGE_UPDATE_AVAILABLE,
        NudgeFacts(
            running="0.16.4",
            installed="0.16.4",
            latest="0.16.5",
            source="github-release-manifest",
        ),
    )

    assert payload["latest"] == "0.16.5"
    assert payload["source"] == "github-release-manifest"
    assert payload["skill"] == INSTALL_ROUTE
    assert payload["install_url"] == INSTALL_ROUTE
    detail = payload["detail"]
    assert INSTALL_ROUTE in detail
    assert "Use Shell to execute" in detail
    assert "with --help" in detail
    assert "do not read or paste the script source into context" in detail
    assert "update --help" in detail
    assert "must obtain explicit human/config-owner authorization" in detail
    assert "this nudge and help output are not authorization" in detail
    assert "skill.md" not in detail
    assert "pip install" not in detail
    assert "system(action=" not in detail


def test_mirror_mismatch_reports_without_selecting_higher_version():
    payload = render_nudge_payload(
        NudgeSituation.MIRROR_MISMATCH,
        NudgeFacts(
            running="0.16.3",
            installed="0.16.3",
            checked_at_date="2026-07-17",
            mirror_mismatch={
                "github-release-manifest": {
                    "version": "0.16.5",
                    "manifest_sha256": "a" * 64,
                    "artifact_hashes_sha256": "b" * 64,
                },
                "gitee-release-manifest": {
                    "version": "0.16.4",
                    "manifest_sha256": "c" * 64,
                    "artifact_hashes_sha256": "d" * 64,
                },
            },
        ),
    )

    assert payload["latest"] is None
    assert payload["source"] == "release-manifest-mirror-mismatch"
    assert payload["mirror_mismatch"]["github-release-manifest"]["version"] == "0.16.5"
    assert "disagree" in payload["detail"]
    assert "do not choose the higher version" in payload["detail"]


def test_source_drift_payload_keeps_facts_without_release_migration_route():
    startup = {"git_rev": "old", "source_digest": "a"}
    disk = {"git_rev": "new", "source_digest": "b"}
    payload = render_nudge_payload(
        NudgeSituation.SOURCE_DRIFT,
        NudgeFacts(
            startup_fingerprint=startup,
            disk_fingerprint=disk,
            drift_signals=("git_rev: old -> new",),
        ),
    )

    assert "skill" not in payload
    assert payload["startup_fingerprint"] == startup
    assert payload["disk_fingerprint"] == disk
    assert "git_rev: old -> new" in payload["detail"]
    assert "does not by itself imply a release migration" in payload["detail"]
    assert "grant refresh authority" in payload["detail"]
    detail = payload["detail"]
    assert "explicit human/config-owner authorization for refresh" in detail
    assert detail.index("explicit human/config-owner authorization") < detail.index(
        "system(action='refresh')"
    )
