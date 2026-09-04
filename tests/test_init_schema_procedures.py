from lingtai.init_schema import validate_init


def _valid_init() -> dict:
    return {
        "manifest": {
            "agent_name": "alice",
            "language": "en",
            "llm": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-20250514",
            },
            "capabilities": {},
        },
        "principle": "",
        "covenant": "",
        "pad": "",
        "lingtai": "",
    }



def test_principle_no_longer_active_or_required_prompt_field():
    """principle is LingTai-owned like procedures: legacy-known and ignored."""
    from lingtai.init_schema import LEGACY_MIGRATED_TOP_FIELDS, TOP_KNOWN, TOP_OPTIONAL

    assert "principle" in LEGACY_MIGRATED_TOP_FIELDS
    assert "principle" in TOP_KNOWN
    assert "principle" not in TOP_OPTIONAL

    data = _valid_init()
    del data["principle"]
    validate_init(data)  # no longer required

    data["principle"] = {"ignored": "not type-checked"}
    warnings = validate_init(data)
    assert all("principle" not in w for w in warnings)


def test_principle_file_no_longer_active_prompt_field():
    """principle_file is legacy-known, not active or deprecated schema."""
    from lingtai.init_schema import (
        DEPRECATED_TOP_FIELDS,
        LEGACY_MIGRATED_TOP_FIELDS,
        TOP_KNOWN,
        TOP_OPTIONAL,
        strip_deprecated,
    )

    assert "principle_file" not in DEPRECATED_TOP_FIELDS
    assert "principle_file" in LEGACY_MIGRATED_TOP_FIELDS
    assert "principle_file" in TOP_KNOWN
    assert "principle_file" not in TOP_OPTIONAL

    data = _valid_init()
    data["principle_file"] = 123
    stripped = strip_deprecated(data)
    warnings = validate_init(data)

    assert stripped == []
    assert "principle_file" in data
    assert all("principle_file" not in w for w in warnings)


def test_procedures_no_longer_active_optional_prompt_field():
    """Inline procedures is migrated-legacy-known, not active schema."""
    from lingtai.init_schema import LEGACY_MIGRATED_TOP_FIELDS, TOP_KNOWN, TOP_OPTIONAL

    assert "procedures" in LEGACY_MIGRATED_TOP_FIELDS
    assert "procedures" in TOP_KNOWN
    assert "procedures" not in TOP_OPTIONAL

    data = _valid_init()
    data["procedures"] = {"not": "a string"}
    warnings = validate_init(data)

    assert all("procedures" not in w for w in warnings)


def test_procedures_file_no_longer_active_optional_prompt_field():
    """procedures_file is migrated-legacy-known, not strip_deprecated schema."""
    from lingtai.init_schema import (
        DEPRECATED_TOP_FIELDS,
        LEGACY_MIGRATED_TOP_FIELDS,
        TOP_KNOWN,
        TOP_OPTIONAL,
        strip_deprecated,
    )

    assert "procedures_file" not in DEPRECATED_TOP_FIELDS
    assert "procedures_file" in LEGACY_MIGRATED_TOP_FIELDS
    assert "procedures_file" in TOP_KNOWN
    assert "procedures_file" not in TOP_OPTIONAL

    data = _valid_init()
    data["procedures_file"] = 123
    stripped = strip_deprecated(data)
    warnings = validate_init(data)

    assert stripped == []
    assert "procedures_file" in data
    assert all("procedures_file" not in w for w in warnings)


# --- init seed contract: lingtai is optional; prompt has no alias ---
#
# The lingtai field is an optional initial character seed distinct from
# base_prompt (third-party injection). If omitted, the seed is empty and the
# long-lived character continues to be managed by system/lingtai.md / psyche.
# Jason: no legacy alias, no backward compatibility — a stale prompt remains
# an unknown-field warning and is not honored as a character seed.


def test_lingtai_is_an_optional_seed_field():
    from lingtai.init_schema import TOP_KNOWN

    assert "lingtai" in TOP_KNOWN
    assert "lingtai_file" in TOP_KNOWN

    data = _valid_init()
    assert data["lingtai"] == ""
    validate_init(data)  # present -> valid

    del data["lingtai"]
    warnings = validate_init(data)  # omitted -> empty seed, still valid
    assert all("lingtai" not in w for w in warnings)


def test_lingtai_file_remains_an_optional_seed_field():
    data = _valid_init()
    del data["lingtai"]
    data["lingtai_file"] = "system/lingtai.md"
    validate_init(data)  # _file form remains valid when present


def test_legacy_prompt_field_is_unknown_no_alias():
    """No legacy alias: a prompt field is neither known nor honored.
    It surfaces as an unknown-field warning and does not act as the character
    seed; missing lingtai is still valid because the seed is optional."""
    from lingtai.init_schema import TOP_KNOWN, TOP_OPTIONAL

    assert "prompt" not in TOP_KNOWN
    assert "prompt_file" not in TOP_KNOWN
    assert "prompt" not in TOP_OPTIONAL

    data = _valid_init()
    del data["lingtai"]
    data["prompt"] = "stale seed under the old name"
    warnings = validate_init(data)

    assert any("prompt" in w for w in warnings)
    assert all("lingtai" not in w for w in warnings)


# --- Psyche prompt-owner compatibility boundary ---


def test_psyche_prompt_pairs_are_known_but_inert_init_compatibility_fields():
    from lingtai.init_schema import LEGACY_MIGRATED_TOP_FIELDS, TOP_KNOWN, TOP_OPTIONAL

    for key in (
        "base_prompt", "base_prompt_file", "covenant", "covenant_file",
        "comment", "comment_file",
    ):
        assert key in LEGACY_MIGRATED_TOP_FIELDS
        assert key in TOP_KNOWN
        assert key not in TOP_OPTIONAL

    data = _valid_init()
    data.update({
        "base_prompt": {"intentionally": "untyped"},
        "base_prompt_file": 1,
        "covenant": ["inert"],
        "covenant_file": False,
        "comment": {"legacy": True},
        "comment_file": 2,
    })
    warnings = validate_init(data)
    assert not [warning for warning in warnings if "prompt" in warning or "covenant" in warning or "comment" in warning]


def test_covenant_is_not_required_but_pad_still_is():
    data = _valid_init()
    data.pop("covenant")
    validate_init(data)

    data.pop("pad")
    import pytest
    with pytest.raises(ValueError, match="missing required field: pad"):
        validate_init(data)


# --- init prompt contract: brief and substrate retired as external overrides ---
#
# The externally changeable system-prompt surface is exactly base_prompt,
# covenant, and comment. `brief` (secretary-written life context) and
# `substrate` (kernel-owned architecture model) are no longer external prompt
# overrides: their inline/_file init.json fields are migrated-legacy-known
# (tolerated on old init.json, never honored) and the kernel owns the rendered
# sections (substrate from the packaged default; brief from disk only).


def test_brief_no_longer_active_optional_prompt_field():
    from lingtai.init_schema import LEGACY_MIGRATED_TOP_FIELDS, TOP_KNOWN, TOP_OPTIONAL

    assert "brief" in LEGACY_MIGRATED_TOP_FIELDS
    assert "brief" in TOP_KNOWN
    assert "brief" not in TOP_OPTIONAL

    data = _valid_init()
    data["brief"] = {"not": "a string"}  # untyped now
    warnings = validate_init(data)
    assert all("brief" not in w for w in warnings)


def test_brief_file_no_longer_active_prompt_field():
    from lingtai.init_schema import (
        DEPRECATED_TOP_FIELDS,
        LEGACY_MIGRATED_TOP_FIELDS,
        TOP_KNOWN,
        TOP_OPTIONAL,
        strip_deprecated,
    )

    assert "brief_file" not in DEPRECATED_TOP_FIELDS
    assert "brief_file" in LEGACY_MIGRATED_TOP_FIELDS
    assert "brief_file" in TOP_KNOWN
    assert "brief_file" not in TOP_OPTIONAL

    data = _valid_init()
    data["brief_file"] = 123
    stripped = strip_deprecated(data)
    warnings = validate_init(data)

    assert stripped == []
    assert "brief_file" in data
    assert all("brief_file" not in w for w in warnings)


def test_substrate_no_longer_active_optional_prompt_field():
    from lingtai.init_schema import LEGACY_MIGRATED_TOP_FIELDS, TOP_KNOWN, TOP_OPTIONAL

    assert "substrate" in LEGACY_MIGRATED_TOP_FIELDS
    assert "substrate" in TOP_KNOWN
    assert "substrate" not in TOP_OPTIONAL

    data = _valid_init()
    data["substrate"] = {"not": "a string"}  # untyped now
    warnings = validate_init(data)
    assert all("substrate" not in w for w in warnings)


def test_substrate_file_no_longer_active_prompt_field():
    from lingtai.init_schema import (
        DEPRECATED_TOP_FIELDS,
        LEGACY_MIGRATED_TOP_FIELDS,
        TOP_KNOWN,
        TOP_OPTIONAL,
        strip_deprecated,
    )

    assert "substrate_file" not in DEPRECATED_TOP_FIELDS
    assert "substrate_file" in LEGACY_MIGRATED_TOP_FIELDS
    assert "substrate_file" in TOP_KNOWN
    assert "substrate_file" not in TOP_OPTIONAL

    data = _valid_init()
    data["substrate_file"] = 123
    stripped = strip_deprecated(data)
    warnings = validate_init(data)

    assert stripped == []
    assert "substrate_file" in data
    assert all("substrate_file" not in w for w in warnings)
