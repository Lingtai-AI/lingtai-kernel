"""Capacity tests reach production send paths and capture real request bodies."""
import json
from types import SimpleNamespace

import pytest

from lingtai.kernel.llm.base import LLMReplayTerminalError, llm_replay_terminal_flags
from lingtai.kernel.llm.interface import ChatInterface, TextBlock, ToolCallBlock, ToolResultBlock
from tests.contracts.llm_conversation_input import regimes


@pytest.fixture(autouse=True)
def deterministic_estimator(monkeypatch):
    # No optional tokenizer downloads or provider calls in these controls.
    monkeypatch.setattr('lingtai.kernel.token_counter.count_tokens', lambda text: (len(text) + 3) // 4)


def seed_old(session):
    for n in range(12):
        session.interface.add_user_message(f'OLD-{n} ' + 'x' * 2500)
        session.interface.add_assistant_message(content=[TextBlock(text='answer')])


def bounded_codex():
    session, client = regimes._build_codex()
    session.configure_context_capacity(6000)
    return session, client


@pytest.mark.parametrize('kind', ['text', 'tools', 'wake'])
def test_codex_real_payload_trims_oldest_and_preserves_pending(kind, tmp_path):
    session, client = bounded_codex()
    seed_old(session)
    session.interface.add_user_message('NEWEST instruction')
    if kind != 'text':
        session.interface.add_assistant_message(content=[
            ToolCallBlock(id='a', name='bash', args={}),
            TextBlock(text='mixed assistant'),
            ToolCallBlock(id='b', name='bash', args={}),
        ])
        results = [ToolResultBlock(id=n, name='bash', content='REAL-' + n) for n in ['a', 'b']]
        if kind == 'wake':
            session.interface.add_tool_results(results)
            pending = None
        else:
            pending = results
    else:
        pending = 'CURRENT instruction'
    session.pre_request_hook = lambda interface: interface._append('user', [TextBlock(text='HOOK')])
    session._compacted_items = [{'role': 'user', 'content': 'STALE-OPAQUE'}]
    session._compacted_at_entry_count = 2
    session._ws_frozen_outputs['stale'] = 'REMOVED output'
    session.send(pending)
    body = client.responses.kwargs[-1]
    wire = json.dumps(body, default=str)
    assert 'OLD-0 ' not in wire
    assert 'NEWEST instruction' in wire
    assert 'HOOK' in wire
    assert 'STALE-OPAQUE' not in wire
    assert 'previous_response_id' not in body
    assert session._compacted_items is None
    assert 'stale' not in session._ws_frozen_outputs
    assert session._estimate_request_tokens() <= session._request_input_budget()
    if kind != 'text':
        assert 'REAL-a' in wire and 'REAL-b' in wire
        assert {item.get('call_id') for item in body['input'] if item.get('type') == 'function_call_output'} == {'a', 'b'}
    saved = tmp_path / 'state.json'
    saved.write_text(json.dumps(session.get_state()))
    restored, restored_client = bounded_codex()
    restored._interface = ChatInterface.from_dict(json.loads(saved.read_text())['messages'])
    restored.send(None)
    replay = json.dumps(restored_client.responses.kwargs[-1], default=str)
    assert 'OLD-0 ' not in replay and 'STALE-OPAQUE' not in replay


@pytest.mark.parametrize('source', ['pending', 'system', 'tools', 'hook', 'prebuilt'])
def test_irreducible_input_never_calls_provider(source):
    session, client = bounded_codex()
    huge = 'Z' * 40000
    pending = 'CURRENT'
    if source == 'pending':
        pending = huge
    elif source == 'system':
        session._instructions = huge
    elif source == 'tools':
        session._tools = [{'type': 'function', 'name': 'huge', 'description': huge}]
    elif source == 'hook':
        session.pre_request_hook = lambda interface: interface.add_user_message(huge)
    else:
        pending = {'role': 'user', 'content': huge}
    with pytest.raises(LLMReplayTerminalError) as caught:
        session.send(pending)
    assert llm_replay_terminal_flags(caught.value) == (False, True)
    assert client.responses.kwargs == []
    if source in ('pending', 'hook'):
        assert huge in json.dumps(session.get_state())


def test_block_keeps_executed_results_and_pending_call():
    session, client = bounded_codex()
    session.interface.add_user_message('CURRENT')
    session.interface.add_assistant_message(content=[ToolCallBlock(id='a', name='bash', args={})])
    result = ToolResultBlock(id='a', name='bash', content='REAL' * 10000)
    with pytest.raises(LLMReplayTerminalError):
        session.send([result])
    assert client.responses.kwargs == []
    assert any(result is b for e in session.interface.entries for b in e.content)
    assert any(isinstance(b, ToolCallBlock) for e in session.interface.entries for b in e.content)


def test_hook_cannot_move_protected_instruction():
    session, client = bounded_codex()
    session.pre_request_hook = lambda interface: interface.add_user_message('small hook')
    with pytest.raises(LLMReplayTerminalError):
        session.send('CURRENT' * 10000)
    assert 'CURRENT' in json.dumps(session.get_state())
    assert not client.responses.kwargs


@pytest.mark.parametrize('failure', ['zero', 'flat', 'rounds'])
def test_bounded_failure_calls_provider_zero(monkeypatch, failure):
    session, client = bounded_codex()
    seed_old(session)
    calls = []
    if failure == 'zero':
        monkeypatch.setattr(session, '_trim_context_one_round', lambda **kw: 0)
    elif failure == 'flat':
        monkeypatch.setattr(session, '_estimate_request_tokens', lambda *a: 99999)
    else:
        monkeypatch.setattr(session, '_estimate_request_tokens', lambda *a: 99999 - len(calls))
        def tiny(**kw):
            calls.append(1)
            return 1
        monkeypatch.setattr(session, '_trim_context_one_round', tiny)
    with pytest.raises(LLMReplayTerminalError):
        session.send('CURRENT')
    assert not client.responses.kwargs
    if failure == 'rounds':
        assert len(calls) == 64


def test_websocket_actual_frame_rebases_after_trim():
    session, client = bounded_codex()
    frames = []
    class Wire:
        def connect(self, **kw): return None
        def close(self): pass
        def send_response_processed(self, response_id): pass
        def stream(self, frame):
            frames.append(frame)
            return regimes._CodexRestResponses().create(**frame)
    session._transport = 'websocket'
    session._ws_enabled = True
    session._ws_transport_factory = lambda *a: Wire()
    session.send('initial')
    seed_old(session)
    session.send('CURRENT')
    assert len(frames) == 2
    assert 'previous_response_id' not in frames[-1]
    assert 'OLD-0 ' not in json.dumps(frames[-1])
    assert 'CURRENT' in json.dumps(frames[-1])
    assert not client.responses.kwargs


@pytest.mark.parametrize('regime', regimes.CONFORMING_BUILDABLE, ids=lambda r: r.name)
def test_production_regimes_block_after_hook(regime):
    session, transport = regime.build()
    session.configure_context_capacity(6000)
    session.pre_request_hook = lambda interface: interface.add_user_message('HUGE' * 10000)
    with pytest.raises(LLMReplayTerminalError):
        session.send('CURRENT')
    if regime.name == 'codex':
        assert transport.responses.kwargs == []


def test_multitool_mixed_rows_snap_as_one_pair():
    session, _ = bounded_codex()
    interface = session.interface
    interface._append('assistant', [ToolCallBlock(id='a', name='bash', args={}), ToolCallBlock(id='b', name='bash', args={})])
    interface._append('user', [TextBlock(text='mixed'), ToolResultBlock(id='a', name='bash', content='real a')])
    interface._append('user', [TextBlock(text='intervening')])
    interface._append('user', [TextBlock(text='mixed'), ToolResultBlock(id='b', name='bash', content='real b')])
    interface.add_user_message('CURRENT')
    assert session._trim_context_one_round(protected_entry_id=interface.entries[-1].id) == 4
    assert len(interface.entries) == 1


def test_local_capacity_stops_real_run_loop_before_aed(tmp_path, monkeypatch):
    from lingtai.kernel.base_agent import turn
    from tests.test_aed_recovery import _make_run_loop_agent
    session, client = bounded_codex()
    agent = _make_run_loop_agent(tmp_path)
    agent._stop_on_sleep = True
    calls = []
    def handle(_agent, _msg):
        calls.append(1)
        session.send('CURRENT' * 10000)
    monkeypatch.setattr(turn, '_handle_message', handle)
    turn._run_loop(agent)
    assert calls == [1]
    assert client.responses.kwargs == []
    assert getattr(agent, 'rebuilds', 0) == 0
    assert not any(name in ('aed_attempt', 'aed_transient_retry') for name, _ in agent._logs)
    assert any(name == 'llm_no_aed_retry_terminal' for name, _ in agent._logs)


@pytest.mark.parametrize('kind', ['text', 'tools', 'wake'])
def test_session_manager_propagates_capacity_to_actual_dispatch(kind):
    from tests.test_context_pressure_reminder import _make_session_manager
    session, client = regimes._build_codex()
    manager = _make_session_manager()
    manager._chat = session
    manager._config.context_limit = 6000
    manager._config.retry_timeout = 2
    if kind == 'text':
        pending = 'CURRENT' * 10000
    else:
        session.interface.add_user_message('CURRENT')
        session.interface.add_assistant_message(content=[ToolCallBlock(id='a', name='bash', args={})])
        results = [ToolResultBlock(id='a', name='bash', content='REAL' * 10000)]
        if kind == 'wake':
            session.interface.add_tool_results(results)
            pending = None
        else:
            pending = results
    try:
        with pytest.raises(LLMReplayTerminalError):
            manager.send(pending)
        assert client.responses.kwargs == []
        assert session._context_capacity_limit == 6000
    finally:
        manager._timeout_pool.shutdown(wait=True)


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('kind', ['text', 'tools', 'wake', 'extras'])
def test_responses_effective_wire_capacity(stream, kind):
    from lingtai.kernel.llm.base import LLMResponse
    from lingtai.kernel.token_counter import count_tokens
    session, client = regimes._build_openai_responses()
    session.configure_context_capacity(6000)
    session.update_system_prompt('system')
    if stream:
        raw = client.responses.create.return_value
        client.responses.create.return_value = [SimpleNamespace(type='response.completed', response=raw)]
    session._record_assistant_response(LLMResponse(text='ok'), [
        {'type': 'reasoning', 'id': 'rs_probe', 'summary': [], 'encrypted_content': 'R' * 40000},
    ])
    pending = 'CURRENT'
    if kind in ('tools', 'wake'):
        session.interface.add_user_message('CURRENT')
        session.interface.add_assistant_message([ToolCallBlock(id='executed', name='bash', args={})])
        results = [ToolResultBlock(id='executed', name='bash', content='REAL-EVIDENCE')]
        if kind == 'wake':
            session.interface.add_tool_results(results)
            pending = None
        else:
            pending = results
    if kind == 'extras':
        session._extra_kwargs['metadata'] = {'large': 'X' * 40000}
    try:
        (session.send_stream if stream else session.send)(pending)
    except LLMReplayTerminalError as exc:
        assert llm_replay_terminal_flags(exc) == (False, True)
        assert client.responses.create.call_count == 0
        if kind in ('tools', 'wake'):
            assert 'REAL-EVIDENCE' in json.dumps(session.get_state())
    else:
        wire = client.responses.create.call_args.kwargs
        assert (count_tokens(json.dumps(wire, default=str)) * 110 + 99) // 100 <= session._request_input_budget()
        assert 'CURRENT' in json.dumps(wire)
        if kind in ('tools', 'wake'):
            assert 'REAL-EVIDENCE' in json.dumps(wire)


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('extra,expected', [({}, 8192), ({'max_tokens': 256}, 256),
    ({'max_output_tokens': 384}, 384), ({'max_tokens': 256, 'max_output_tokens': 384}, 256),
    ({'max_tokens': 6001}, 6001), ({'max_output_tokens': 6001}, 6001),
    ({'max_tokens': 0}, 0), ({'max_tokens': 8192}, 8192)])
def test_anthropic_effective_output_budget(stream, extra, expected):
    session, client = regimes._build_anthropic()
    session._extra_kwargs.update(extra)
    session.configure_context_capacity(6000)
    if expected > 6000:
        with pytest.raises(LLMReplayTerminalError) as caught:
            (session.send_stream if stream else session.send)('hello')
        assert llm_replay_terminal_flags(caught.value) == (False, True)
        assert client.messages.create.call_count == 0
        assert client.messages.stream.call_count == 0
        assert 'hello' in json.dumps(session.get_state())
    else:
        # Builder and admission must resolve precisely the same effective cap.
        assert session._build_request_kwargs([])['max_tokens'] == expected
        assert session._request_input_budget() == 6000 - expected - 1024
        if stream:
            fake = client.messages.stream.return_value.__enter__.return_value
            fake.__iter__.return_value = [SimpleNamespace(type='content_block_delta',
                delta=SimpleNamespace(type='text_delta', text='ok'))]
            fake.get_final_message.return_value = client.messages.create.return_value
            session.send_stream('hello')
            kwargs = client.messages.stream.call_args.kwargs
        else:
            session.send('hello')
            kwargs = client.messages.create.call_args.kwargs
        assert kwargs['max_tokens'] == expected
        assert 'max_output_tokens' not in kwargs


def test_notification_sync_real_manager_codex_terminal_no_aed(tmp_path, monkeypatch):
    from lingtai.kernel.base_agent import turn
    from tests.test_tc_wake import _modern_agent
    from tests.test_aed_recovery import _make_run_loop_agent
    from tests.test_context_pressure_reminder import _make_session_manager
    from tests._notification_store_helpers import publish_test_payload
    source = _modern_agent(tmp_path, monkeypatch)
    source._chat.interface.add_user_message('CURRENT' + 'X' * 40000)
    publish_test_payload(tmp_path, 'email', {'header': 'REAL-EVENT'})
    source._sync_notifications()
    msg = source.inbox.get_nowait()
    import copy
    before_results = copy.deepcopy([b for e in source._chat.interface.entries for b in e.content if isinstance(b, ToolResultBlock)])
    assert before_results
    session, client = regimes._build_codex()
    session._interface = source._chat.interface
    manager = _make_session_manager()
    manager._chat = session
    manager._config.context_limit = 6000
    manager._config.retry_timeout = 2
    agent = _make_run_loop_agent(tmp_path)
    agent._stop_on_sleep = True
    agent._chat = session
    agent._session = manager
    agent.inbox.get_nowait()
    agent.inbox.put(msg)
    try:
        turn._run_loop(agent)
        assert not client.responses.kwargs
        assert not any(n in ('aed_attempt', 'aed_transient_retry') for n, _ in agent._logs)
        assert any(n == 'llm_no_aed_retry_terminal' for n, _ in agent._logs)
        assert before_results == [b for e in session.interface.entries for b in e.content if isinstance(b, ToolResultBlock)]
        assert not session.interface.has_pending_tool_calls()
        assert manager._chat is session
    finally:
        manager._timeout_pool.shutdown(wait=True)


def test_base_agent_save_and_real_manager_restore_trimmed_history(tmp_path):
    from lingtai.kernel.base_agent import BaseAgent
    from tests.test_context_pressure_reminder import _make_session_manager
    session, client = bounded_codex()
    seed_old(session)
    session.interface.add_user_message('CURRENT')
    session.interface.add_assistant_message([ToolCallBlock(id='executed', name='bash', args={})])
    session.send([ToolResultBlock(id='executed', name='bash', content='REAL-EVIDENCE')])
    manager = _make_session_manager()
    manager._chat = session
    agent = SimpleNamespace(_working_dir=tmp_path, agent_name='test', _last_usage=None,
        get_chat_state=manager.get_chat_state,
        _workdir=SimpleNamespace(write_manifest=lambda _: None), _build_manifest=lambda: {},
        _write_status_snapshot=lambda: None, _write_session_stats_record=lambda: None)
    BaseAgent._save_chat_history(agent)
    state = {'messages': [json.loads(line) for line in (tmp_path/'history/chat_history.jsonl').read_text().splitlines()]}
    restored, restored_client = bounded_codex()
    def factory(**kw):
        restored._interface = kw['interface']
        return restored
    manager._llm_service.create_session.side_effect = factory
    # A durable executed-tool event remains independently recoverable after trim.
    logs = tmp_path / 'logs'
    logs.mkdir(exist_ok=True)
    events = logs / 'events.jsonl'
    event_bytes = json.dumps({'type': 'tool_result', 'tool_call_id': 'executed',
        'tool_name': 'bash', 'result': 'REAL-EVIDENCE', 'ts': 1}) + '\n'
    events.write_text(event_bytes)
    recovery_agent = SimpleNamespace(_working_dir=tmp_path, _log=lambda *a, **kw: None)
    manager._tool_result_recovery_lookup_fn = lambda call: BaseAgent._recover_pending_tool_result(recovery_agent, call)
    manager.restore_chat(state)
    assert manager._chat is restored
    restored.send(None)
    replay = json.dumps(restored_client.responses.kwargs[-1], default=str)
    assert 'OLD-0 ' not in replay
    assert 'CURRENT' in replay and 'REAL-EVIDENCE' in replay
    # Reconstruct an interrupted tail through the real SessionManager heal path.
    pending = []
    for row in state['messages']:
        pending.append(row)
        if any(b.get('type') == 'tool_call' and b.get('id') == 'executed' for b in row.get('content', [])):
            break
    assert len(pending) < len(state['messages'])
    manager.restore_chat({'messages': pending})
    restored.send(None)
    healed = json.dumps(restored_client.responses.kwargs[-1], default=str)
    assert 'REAL-EVIDENCE' in healed and 'OLD-0 ' not in healed
    assert events.read_text() == event_bytes
    manager._timeout_pool.shutdown(wait=True)
