from lingtai_kernel.prompt import build_system_prompt
from lingtai_kernel.prompt import SystemPromptManager


def test_build_system_prompt_minimal():
    mgr = SystemPromptManager()
    prompt = build_system_prompt(mgr)
    assert isinstance(prompt, str)


def test_build_system_prompt_with_sections():
    mgr = SystemPromptManager()
    mgr.write_section("role", "You are a test agent")
    mgr.write_section("memory", "Remember: user likes concise")
    prompt = build_system_prompt(mgr)
    assert "You are a test agent" in prompt
    assert "Remember: user likes concise" in prompt


def test_rules_renders_after_covenant_before_tools():
    mgr = SystemPromptManager()
    mgr.write_section("covenant", "Be good.", protected=True)
    mgr.write_section("rules", "No deleting files.", protected=True)
    mgr.write_section("tools", "### bash\nRun commands.", protected=True)
    prompt = mgr.render()
    cov_pos = prompt.index("Be good.")
    rules_pos = prompt.index("No deleting files.")
    tools_pos = prompt.index("Run commands.")
    assert cov_pos < rules_pos < tools_pos


def test_rules_section_absent_when_empty():
    mgr = SystemPromptManager()
    mgr.write_section("covenant", "Be good.", protected=True)
    mgr.write_section("tools", "### bash\nRun commands.", protected=True)
    prompt = mgr.render()
    assert "## rules" not in prompt
