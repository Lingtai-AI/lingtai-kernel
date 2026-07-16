"""Docs/architecture tests for the preset runtime model documentation slice.

Covers `init.json` as a distributed composition document, the detailed preset
runtime model routed through the existing substrate-manual reference, compact
resident routing cues, and the corrected stale wrapper Anatomy citations /
retired-comment drift. Asserts route/concept presence with stable text
anchors; it is not a full-prose snapshot.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SYSTEM_MANUAL = ROOT / "src/lingtai/intrinsic_skills/system-manual/SKILL.md"
SUBSTRATE_REFERENCE = (
    ROOT
    / "src/lingtai/intrinsic_skills/system-manual/reference/substrate-manual/SKILL.md"
)
RESIDENT_SUBSTRATE = ROOT / "src/lingtai/prompts/substrate/substrate.md"
RESIDENT_PROCEDURES = ROOT / "src/lingtai/prompts/procedures/procedures.md"
DAEMON_MANUAL = ROOT / "src/lingtai/tools/daemon/manual/SKILL.md"
WRAPPER_ANATOMY = ROOT / "src/lingtai/ANATOMY.md"
INIT_SCHEMA = ROOT / "src/lingtai/init_schema.py"
KERNEL_PROMPT = ROOT / "src/lingtai/kernel/prompt.py"

CANONICAL_SURFACE = (
    SYSTEM_MANUAL,
    SUBSTRATE_REFERENCE,
    RESIDENT_SUBSTRATE,
    RESIDENT_PROCEDURES,
    DAEMON_MANUAL,
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Router and nested catalog point to the existing substrate reference.
# ---------------------------------------------------------------------------


def test_system_manual_routes_preset_runtime_model_to_substrate_reference():
    text = _read(SYSTEM_MANUAL)
    assert "reference/substrate-manual/SKILL.md" in text
    assert "preset runtime model" in text.lower()
    assert "init.json composition" in text.lower()


def test_system_manual_does_not_add_a_competing_preset_manual():
    text = _read(SYSTEM_MANUAL)
    # No new top-level preset-manual entry; the substrate reference stays the
    # single canonical detailed owner.
    assert "name: preset-manual" not in text.lower()


# ---------------------------------------------------------------------------
# 2. The canonical substrate reference contains the required anchors.
# ---------------------------------------------------------------------------


# Case-sensitive anchors: literal identifiers/paths that must match exactly.
REQUIRED_SUBSTRATE_ANCHORS_EXACT = [
    "system(action=\"presets\")",
    "tasks[].preset",
    "exact path",
    "stem lookup",
    "system/manifest.resolved.json",
    "TUI/library",
    "discover_presets_in_dirs",
]

# Case-insensitive anchors: prose concepts that may appear with varying case.
REQUIRED_SUBSTRATE_ANCHORS_CI = [
    "active",
    "default",
    "allowed",
    "refresh",
    "revert",
    "external cli",
]


def test_substrate_reference_contains_required_preset_anchors():
    text = _read(SUBSTRATE_REFERENCE)
    for anchor in REQUIRED_SUBSTRATE_ANCHORS_EXACT:
        assert anchor in text, f"missing anchor: {anchor}"
    text_lower = text.lower()
    for anchor in REQUIRED_SUBSTRATE_ANCHORS_CI:
        assert anchor in text_lower, f"missing anchor: {anchor}"


def test_substrate_reference_daemon_explicit_preset_must_be_allowed():
    text = _read(SUBSTRATE_REFERENCE)
    assert "manifest.preset.allowed" in text
    assert "_preset_ref_in" in text


def test_substrate_reference_daemon_preset_failures_refuse_whole_batch():
    text = _read(SUBSTRATE_REFERENCE)
    assert text.count("refuses the whole batch") >= 2
    assert "before any emanation is scheduled" in text


def test_substrate_reference_omitted_daemon_preset_is_parent_derived():
    text = _read(SUBSTRATE_REFERENCE)
    assert "parent-derived" in text
    # Omitting preset is the one path that still skips the allowlist check.
    assert "never reads or consults" in text or "skips this check entirely" in text or "skip that check entirely" in text


# ---------------------------------------------------------------------------
# 3. No accidental "directory scan" / "all presets in the library" claims,
#    and no stale claim that the daemon path never checks `allowed` for an
#    explicit preset, anywhere in the canonical surface.
# ---------------------------------------------------------------------------

FORBIDDEN_CATALOG_PHRASES = [
    "all presets in the library",
]

FORBIDDEN_DAEMON_BYPASS_PHRASES = [
    "does not consult",
    "does not check the path against",
    "not check the path against",
]


def test_no_canonical_text_claims_unrestricted_directory_scan_catalog():
    for path in CANONICAL_SURFACE:
        text = _read(path).lower()
        for phrase in FORBIDDEN_CATALOG_PHRASES:
            assert phrase not in text, f"{path} claims: {phrase}"


def test_no_route_claims_daemon_explicit_preset_bypasses_allowed_gate():
    for path in CANONICAL_SURFACE:
        text = _read(path).lower()
        for phrase in FORBIDDEN_DAEMON_BYPASS_PHRASES:
            assert phrase not in text, f"{path} claims: {phrase}"


# ---------------------------------------------------------------------------
# 4. Resident sections stay compact: routes, not duplicated tables.
# ---------------------------------------------------------------------------


def test_resident_substrate_routes_without_duplicating_full_model():
    text = _read(RESIDENT_SUBSTRATE)
    assert "reference/substrate-manual/SKILL.md" in text
    assert "allowed" in text
    # No duplicated field matrix / manifest table markdown pipe-table for preset fields.
    assert "| Field group | Real owner |" not in text
    assert "Preset identity and the two catalogs" not in text


def test_resident_procedures_routes_without_duplicating_full_model():
    text = _read(RESIDENT_PROCEDURES)
    assert "reference/substrate-manual/SKILL.md" in text
    assert "tasks[].preset" in text
    assert "| Field group | Real owner |" not in text


def test_daemon_manual_routes_without_duplicating_the_model():
    text = _read(DAEMON_MANUAL)
    assert "reference/substrate-manual/SKILL.md" in text
    assert "does not" in text.lower()
    assert "allowed" in text
    # Does not duplicate the two-catalog / swap-sequence explanation.
    assert "TUI/library discovery" not in text
    assert "Main-agent swap, revert, and refresh sequence" not in text


# ---------------------------------------------------------------------------
# 5. Stale wrapper Anatomy citations are corrected (symbol identity, not
#    merely a range that still happens to validate), including the
#    _setup_from_init / core_defaults call-site citations.
# ---------------------------------------------------------------------------

STALE_WRAPPER_ANATOMY_ANCHORS = [
    "`_read_init` :906",
    "`_activate_preset` :988",
    "`_reload_prompt_sections` :1354",
    "`_setup_from_init` :1062",
    "`_setup_from_init` :989",
    "`_setup_from_init` :1137",
    "`_activate_preset` :915",
    "`agent._read_init` :1071",
    "`cli.load_init` :50",
]

CURRENT_WRAPPER_ANATOMY_ANCHORS = [
    "`_read_init` :1169",
    "`_activate_preset` :1261",
    "`_reload_prompt_sections` :1602",
    "`_setup_from_init` :1338",
    "`agent._read_init` :1224",
    "`cli.load_init` :62",
]


def test_wrapper_anatomy_rejects_stale_symbol_citations():
    text = _read(WRAPPER_ANATOMY)
    for stale in STALE_WRAPPER_ANATOMY_ANCHORS:
        assert stale not in text, f"stale citation present: {stale}"


def test_wrapper_anatomy_cites_current_symbol_lines():
    text = _read(WRAPPER_ANATOMY)
    for current in CURRENT_WRAPPER_ANATOMY_ANCHORS:
        assert current in text, f"missing current citation: {current}"


# ---------------------------------------------------------------------------
# 6. The wrapper Anatomy carries the reciprocal edge to the canonical manual,
#    and both directions state the cross-check obligation.
# ---------------------------------------------------------------------------


def test_wrapper_anatomy_links_the_canonical_manual_route():
    text = _read(WRAPPER_ANATOMY)
    assert "src/lingtai/intrinsic_skills/system-manual/SKILL.md" in text
    assert (
        "src/lingtai/intrinsic_skills/system-manual/reference/substrate-manual/SKILL.md"
        in text
    )
    assert "cross-check" in text.lower()


def test_substrate_reference_links_back_to_wrapper_anatomy():
    text = _read(SUBSTRATE_REFERENCE)
    assert "src/lingtai/ANATOMY.md" in text
    assert "cross-check" in text.lower() or "re-check" in text.lower()


# ---------------------------------------------------------------------------
# 7. Corrected comments: retired large-result notification wording, the
#    false "freely editable" substrate mirror claim, and the accurate
#    threshold-vs-ranking distinction for summarize_notification_threshold.
# ---------------------------------------------------------------------------


def test_init_schema_no_longer_describes_retired_notification_gate():
    text = _read(INIT_SCHEMA)
    assert "combined length of all" not in text
    assert "total-length gate" not in text


def test_init_schema_threshold_comment_does_not_claim_zero_disables_ranking():
    text = _read(INIT_SCHEMA)
    assert "0 disables ranking" not in text
    assert "top_results" in text
    assert "fixed" in text.lower()


def test_kernel_prompt_does_not_claim_substrate_mirror_is_freely_editable():
    text = _read(KERNEL_PROMPT)
    assert "edit it freely" not in text
    assert "overwrit" in text.lower()


# ---------------------------------------------------------------------------
# 8. The raw-init writer list is scoped, not falsely exhaustive: boot/refresh
#    reader paths are read-only, explicit preset activation remains a writer,
#    and the list explicitly disclaims being a repository-wide inventory,
#    anchoring the soul(action="config")/soul(action="voice") counter-example
#    to the real persist functions.
# ---------------------------------------------------------------------------


def test_substrate_reference_pins_read_only_boot_venv_resolution():
    text = _read(SUBSTRATE_REFERENCE)
    assert "CLI venv write-back" in text
    assert "raw input is unchanged" in text


def test_cli_keeps_resolved_venv_in_memory_without_init_writeback():
    text = _read(ROOT / "src/lingtai/cli.py")
    assert 'data["venv_path"] = str(venv_dir)' in text
    assert "init_path.write_text(json.dumps(data" not in text


def test_substrate_reference_writer_list_disclaims_repo_wide_exhaustivity():
    text = _read(SUBSTRATE_REFERENCE)
    assert "not a repository-wide inventory of every raw-`init.json`" in text
    assert "boot/refresh/preset-composition lifecycle" in text


def test_substrate_reference_soul_example_anchors_to_actual_persist_functions():
    text = _read(SUBSTRATE_REFERENCE)
    assert 'soul(action="config")' in text
    assert 'soul(action="voice")' in text
    assert "_persist_soul_config" in text
    assert "_persist_soul_voice" in text

    soul_config_src = _read(ROOT / "src/lingtai/tools/soul/config.py")
    assert "def _persist_soul_config(agent, new_values: dict) -> str | None:" in soul_config_src
    assert "def _persist_soul_voice(agent, *, voice: str, voice_prompt: str) -> str | None:" in soul_config_src
    assert 'soul_block["delay"] = new_values["delay_seconds"]' in soul_config_src
    assert 'soul_block["voice"] = voice' in soul_config_src
