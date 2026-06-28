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


# --- init seed contract: `lingtai` replaces `prompt` with no legacy alias ---
#
# `lingtai` (灵台) is the agent's required initial character seed — distinct from
# `base_prompt` (third-party injection). It was renamed from `prompt` /
# `prompt_file`. Jason: no legacy alias, no backward compatibility — a stale
# `prompt` is an unknown-field warning and a missing `lingtai` is a hard error.


def test_lingtai_is_the_required_seed_field():
    from lingtai.init_schema import TOP_KNOWN

    assert "lingtai" in TOP_KNOWN
    assert "lingtai_file" in TOP_KNOWN

    data = _valid_init()
    assert data["lingtai"] == ""
    validate_init(data)  # present → valid


def test_missing_lingtai_is_a_hard_error():
    import pytest

    data = _valid_init()
    del data["lingtai"]
    with pytest.raises(ValueError, match="lingtai"):
        validate_init(data)


def test_lingtai_file_satisfies_the_required_seed():
    data = _valid_init()
    del data["lingtai"]
    data["lingtai_file"] = "system/lingtai.md"
    validate_init(data)  # _file form satisfies the requirement


def test_legacy_prompt_field_is_unknown_no_alias():
    """No legacy alias: a `prompt` field is neither known nor honored — it
    surfaces as an unknown-field warning, and on its own it does NOT satisfy
    the required `lingtai` seed."""
    import pytest
    from lingtai.init_schema import TOP_KNOWN, TOP_OPTIONAL

    assert "prompt" not in TOP_KNOWN
    assert "prompt_file" not in TOP_KNOWN
    assert "prompt" not in TOP_OPTIONAL

    # `prompt` present but `lingtai` absent → required-field failure.
    data = _valid_init()
    del data["lingtai"]
    data["prompt"] = "stale seed under the old name"
    with pytest.raises(ValueError, match="lingtai"):
        validate_init(data)

    # With `lingtai` present, a stray `prompt` is merely an unknown-field warning.
    data["lingtai"] = ""
    warnings = validate_init(data)
    assert any("prompt" in w for w in warnings)


# --- init prompt contract: base_prompt is the third-party injection point ---


def test_base_prompt_is_active_optional_text_field():
    """`base_prompt` is the contract's third-party (application / recipe /
    preset) system-prompt injection point — an active, type-checked optional
    text field with inline + _file forms."""
    from lingtai.init_schema import TOP_OPTIONAL, TOP_KNOWN

    assert "base_prompt" in TOP_OPTIONAL
    assert "base_prompt" in TOP_KNOWN
    assert "base_prompt_file" in TOP_KNOWN

    data = _valid_init()
    data["base_prompt"] = "Recipe-injected base prompt."
    warnings = validate_init(data)
    assert all("base_prompt" not in w for w in warnings)


def test_base_prompt_wrong_type_rejected():
    data = _valid_init()
    data["base_prompt"] = 123
    import pytest
    with pytest.raises(ValueError, match="base_prompt.*str"):
        validate_init(data)


def test_base_prompt_file_wrong_type_rejected():
    data = _valid_init()
    data["base_prompt_file"] = 123
    import pytest
    with pytest.raises(ValueError, match="base_prompt_file.*str"):
        validate_init(data)


def test_base_prompt_not_required():
    """base_prompt is optional; absence is valid."""
    data = _valid_init()
    assert "base_prompt" not in data
    validate_init(data)  # no raise, no required-field error


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
