from lingtai.kernel.nudge.prompts import (
    NudgeFacts,
    NudgeSituation,
    SKILL_ROUTE,
    render_nudge_payload,
)


def test_installed_runtime_mismatch_routes_migrations_before_refresh():
    payload = render_nudge_payload(
        NudgeSituation.INSTALLED_RUNTIME_MISMATCH,
        NudgeFacts(running="0.16.3", installed="0.16.4", checked_at_date="2026-07-16"),
    )

    assert payload["skill"] == SKILL_ROUTE
    assert payload["running"] == "0.16.3"
    assert payload["installed"] == "0.16.4"
    assert payload["source"] == "installed-distribution"
    detail = payload["detail"]
    assert f"Read {SKILL_ROUTE} first" in detail
    assert "determine the applicable release migrations" in detail
    assert (
        "explicit human/config-owner authorization for EVERY migration/config write and for refresh"
        in detail
    )
    assert "Apply only authorized writes" in detail
    assert "this nudge and the route are not authorization" in detail
    assert detail.index(SKILL_ROUTE) < detail.index("determine the applicable release migrations")
    assert detail.index("determine the applicable release migrations") < detail.index(
        "explicit human/config-owner authorization"
    )
    assert detail.index("explicit human/config-owner authorization") < detail.index(
        "Apply only authorized writes"
    )
    assert detail.index("Apply only authorized writes") < detail.index("validate")
    assert detail.index("validate") < detail.index("refresh last")


def test_package_update_preserves_human_authorization_boundary():
    payload = render_nudge_payload(
        NudgeSituation.PACKAGE_UPDATE_AVAILABLE,
        NudgeFacts(running="0.16.4", installed="0.16.4", latest="0.16.5"),
    )

    assert payload["skill"] == SKILL_ROUTE
    assert payload["latest"] == "0.16.5"
    detail = payload["detail"]
    assert "ask whether they want to update" in detail
    assert (
        "explicit human/config-owner authorization for EVERY migration/config write and for refresh"
        in detail
    )
    assert "Apply only authorized writes" in detail
    assert "Do not download, update, or refresh without human confirmation." in detail
    assert detail.index(SKILL_ROUTE) < detail.index("determine the applicable release migrations")
    assert detail.index("determine the applicable release migrations") < detail.index(
        "explicit human/config-owner authorization"
    )
    assert detail.index("explicit human/config-owner authorization") < detail.index(
        "Apply only authorized writes"
    )
    assert detail.index("Apply only authorized writes") < detail.index("validate")
    assert detail.index("validate") < detail.index("refresh last")


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
