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
        "prompt": "",
    }


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
