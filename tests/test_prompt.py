from lingtai_kernel.prompt import build_system_prompt
from lingtai_kernel.prompt import SystemPromptManager


def test_build_system_prompt_minimal():
    mgr = SystemPromptManager()
    prompt = build_system_prompt(mgr)
    assert isinstance(prompt, str)


def test_build_system_prompt_with_sections():
    mgr = SystemPromptManager()
    mgr.write_section("role", "You are a test agent")
    mgr.write_section("pad", "Remember: user likes concise")
    prompt = build_system_prompt(mgr)
    assert "You are a test agent" in prompt
    assert "Remember: user likes concise" in prompt


def test_rules_renders_after_covenant_and_tools():
    """Section order is grouped by mutation frequency for cache stability:
    Batch 1 (immovable, prefix-cacheable) — principle, covenant, tools, substrate, ...
    Batch 2 (rarely mutated)              — rules, brief, library, codex, ...

    So both ``covenant`` and ``tools`` precede ``rules`` in the rendered
    prompt, since adjusting rules at runtime should invalidate as little
    of the cached prefix as possible."""
    mgr = SystemPromptManager()
    mgr.write_section("covenant", "Be good.", protected=True)
    mgr.write_section("rules", "No deleting files.", protected=True)
    mgr.write_section("tools", "### bash\nRun commands.", protected=True)
    prompt = mgr.render()
    cov_pos = prompt.index("Be good.")
    rules_pos = prompt.index("No deleting files.")
    tools_pos = prompt.index("Run commands.")
    assert cov_pos < tools_pos < rules_pos


def test_rules_section_absent_when_empty():
    mgr = SystemPromptManager()
    mgr.write_section("covenant", "Be good.", protected=True)
    mgr.write_section("tools", "### bash\nRun commands.", protected=True)
    prompt = mgr.render()
    assert "## rules" not in prompt
