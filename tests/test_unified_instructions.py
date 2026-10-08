"""Production-wired unified prefix, loaded disclosure, and literal retirement."""
from pathlib import Path
import json
import pytest

from lingtai.kernel.prompt import INSTRUCTIONS_ENTRY, build_system_prompt_batches
from lingtai.tools.psyche.settings import PsycheSettingsError
from lingtai.tools.avatar import AvatarManager, get_schema as avatar_schema
from tests.test_psyche_prompt_settings import _agent, _write_init, _write_owner


def call(agent, action):
    return agent._intrinsics['psyche']({'action': action, 'input': {}, 'reasoning': 'orientation'})


def test_real_startup_reconstruction_and_current_read(tmp_path, monkeypatch):
    _write_init(tmp_path)
    _write_owner(tmp_path, base_prompt='USER BASE', covenant='FULL CUSTOM COVENANT')
    (tmp_path / 'system').mkdir()
    (tmp_path / 'system/rules.md').write_text('RETIRED RULES')
    (tmp_path / '.rules').write_text('RETIRED SIGNAL')
    (tmp_path / 'system/pad.md').write_text('AUTHORIZED PAD INSTRUCTIONS')
    agent = _agent(tmp_path)
    try:
        agent._reconstruct_context()
        prompt = agent._build_system_prompt()
        assert prompt.startswith(INSTRUCTIONS_ENTRY)
        assert prompt.count(INSTRUCTIONS_ENTRY) == 1
        assert prompt.index('USER BASE') < prompt.index('## identity')
        assert 'AUTHORIZED PAD INSTRUCTIONS' in prompt
        assert 'RETIRED' not in prompt
        assert agent._prompt_manager.read_section('rules') is None
        assert agent._prompt_manager.read_section('comment') is None
        assert (tmp_path / '.rules').read_text() == 'RETIRED SIGNAL'
        assert (tmp_path / 'system/rules.md').read_text() == 'RETIRED RULES'
        assert call(agent, 'covenant')['covenant'] == 'FULL CUSTOM COVENANT'
        assert 'FULL CUSTOM COVENANT' not in prompt
        full = call(agent, 'instructions')['instructions']
        for name in ('principle', 'substrate', 'procedures', 'meta_guidance'):
            assert agent._prompt_manager.read_section(name) in full
            assert '## '+name+'\n' not in prompt
        expected = agent._prompt_manager.read_instructions()
        with monkeypatch.context() as m:
            def forbidden(*args, **kwargs):
                raise AssertionError('disclosure attempted source I/O')
            m.setattr(Path, 'read_text', forbidden)
            m.setattr(Path, 'write_text', forbidden)
            assert call(agent, 'instructions') == {'status': 'ok', 'instructions': expected}
        # Live loaded owner, not ambient reread or installed manual.
        agent._prompt_manager.write_section('procedures', 'CURRENT LOADED DETAIL', protected=True)
        assert 'CURRENT LOADED DETAIL' in call(agent, 'instructions')['instructions']
        assert 'CURRENT LOADED DETAIL' not in agent._build_system_prompt()
        assert len(build_system_prompt_batches(agent._prompt_manager)) == 2
        assert [r['key'] for r in call(agent, 'settings')['settings']] == [
            'pad','pad_file','base_prompt','base_prompt_file','covenant','covenant_file']
    finally:
        agent.stop(timeout=1)


@pytest.mark.parametrize('field', ['comment', 'comment_file'])
def test_retired_owner_fields_fail_before_publication(tmp_path, field):
    _write_init(tmp_path)
    agent = _agent(tmp_path)
    try:
        agent._reconstruct_context()
        before = agent._build_system_prompt()
        _write_owner(tmp_path, **{field:'old value'})
        with pytest.raises(PsycheSettingsError, match='unknown field.*retired.*pad.md'):
            agent._reconstruct_context()
        assert agent._build_system_prompt() == before
    finally:
        agent.stop(timeout=1)


def test_spawn_retirement_and_parent_source_inheritance():
    schema = avatar_schema()
    spawn = schema['properties']['input']['anyOf'][0]
    assert 'comment' not in spawn['properties']
    body = AvatarManager._make_avatar_psyche_settings({
        'base_prompt': 'BASE', 'covenant_file': '/parent/covenant.md'})
    assert json.loads(body) == {'schema_version':1,'base_prompt':'BASE','covenant_file':'/parent/covenant.md'}
    from lingtai.kernel.base_agent import lifecycle
    assert not hasattr(lifecycle, '_check_rules_file')


def test_failed_flush_restores_loaded_instruction_generation(tmp_path, monkeypatch):
    _write_init(tmp_path)
    agent = _agent(tmp_path)
    try:
        agent._reconstruct_context()
        before = call(agent, 'instructions')
        original = agent._reload_prompt_sections
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            agent._prompt_manager.write_section('procedures', 'REJECTED GENERATION', protected=True)
            return result
        monkeypatch.setattr(agent, '_reload_prompt_sections', changed)
        monkeypatch.setattr(agent, '_flush_system_prompt', lambda: (_ for _ in ()).throw(RuntimeError('flush failed')))
        with pytest.raises(RuntimeError, match='flush failed'):
            agent._reconstruct_context()
        assert call(agent, 'instructions') == before
    finally:
        agent.stop(timeout=1)


def test_actual_wire_and_adapter_owned_rules_are_reachable(tmp_path, monkeypatch):
    from lingtai.llm.openai.adapter import _build_tools
    from lingtai.kernel import meta_block
    _write_init(tmp_path)
    agent = _agent(tmp_path)
    try:
        monkeypatch.setattr(meta_block, 'static_adapter_comment', lambda a: {
            'adapter': 'test-provider', 'first_action': 'PROVIDER OWNED FIRST ACTION'})
        prompt = agent._build_system_prompt()
        assert 'PROVIDER OWNED FIRST ACTION' not in prompt
        assert 'PROVIDER OWNED FIRST ACTION' in call(agent, 'instructions')['instructions']
        wire = {t['function']['name']: t['function'] for t in _build_tools(agent._build_tool_schemas())}
        assert 'instructions' in json.dumps(wire['psyche'])
        assert 'refresh pre-check first' in json.dumps(wire['system'])
        assert 'BEFORE molt' in json.dumps(wire['context'])
        assert 'Persistent child-prompt note' not in json.dumps(wire)
    finally:
        agent.stop(timeout=1)
