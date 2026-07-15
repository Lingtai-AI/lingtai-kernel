"""Focused regression coverage for packaged runtime-guidance globals."""

from lingtai.kernel import meta_block


def test_runtime_guidance_globals_cache_valid_catalog(monkeypatch):
    """The loader's cache and validation-key declarations must ship together."""
    assert meta_block._GUIDANCE_REQUIRED_TOP_KEYS == (
        "schema_version",
        "guidance_version",
        "priority",
        "render_mode",
        "sections",
    )

    monkeypatch.setattr(meta_block, "_GUIDANCE_CACHE", None)
    guidance = meta_block.build_runtime_guidance()

    assert guidance is meta_block._GUIDANCE_CACHE
    assert meta_block.build_runtime_guidance() is guidance
    assert all(key in guidance for key in meta_block._GUIDANCE_REQUIRED_TOP_KEYS)
