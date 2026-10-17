"""New-only result projections: real executor, emit, persistence and wire seams."""
from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace
import threading

import pytest

from lingtai.kernel.llm.base import LLMResponse, ToolCall
from lingtai.kernel.llm.interface import (
    ChatInterface, ToolCallBlock, ToolResultBlock, content_block_from_dict,
)
from lingtai.kernel.loop_guard import LoopGuard
from lingtai.kernel.meta_block import (
    AGENT_META_INSTRUCTION, attach_daemon_agent_meta, build_meta_guidance_ref,
    finalize_two_axis_sidecars as _finalize_sidecars, freeze_tool_result_projections,
)
from lingtai.kernel.tool_executor import ToolExecutor
from lingtai.kernel.tool_result_recovery import recover_tool_result_block_from_events
from lingtai.llm.interface_converters import to_anthropic, to_openai, to_responses_input


CONVERTERS = (to_anthropic, to_openai, to_responses_input)


def finalize_two_axis_sidecars(blocks, *, logger_fn=None):
    # Normalization and final emit are distinct now: main has an intervening
    # existing duplicate-send advisory; these direct executor fixtures have none.
    _finalize_sidecars(blocks)
    freeze_tool_result_projections(blocks, logger_fn=logger_fn)


def outputs(converter, iface):
    wire = converter(iface)
    if converter is to_anthropic:
        return [b['content'] for m in wire for b in m['content']
                if isinstance(b, dict) and b.get('type') == 'tool_result']
    if converter is to_openai:
        return [m['content'] for m in wire if m.get('role') == 'tool']
    return [m['output'] for m in wire if m.get('type') == 'function_call_output']


def append(iface, block):
    iface.add_assistant_message([ToolCallBlock(block.id, block.name, {})])
    iface.add_tool_results([block])


def executor(raw, *, logs=None, summary=True, max_chars=200_000, working_dir=None):
    return ToolExecutor(
        dispatch_fn=lambda _tc: deepcopy(raw),
        make_tool_result_fn=lambda name, result, **kw: ToolResultBlock(
            kw['tool_call_id'], name, result),
        known_tools={'shell', 'email'}, parallel_safe_tools={'shell', 'email'}, guard=LoopGuard(),
        summarizer_fn=(lambda *_args: 'usable short value') if summary else None,
        logger_fn=(lambda kind, **fields: logs.append({'type': kind, **deepcopy(fields)}))
        if logs is not None else None,
        max_result_chars=max_chars, working_dir=working_dir,
    )


def state_metadata():
    return {
        'tool_meta': {'id': 'new', 'timestamp': 'completion', 'char_count': 999,
                      'elapsed_ms': 5, 'unknown_tool_warning': {'do_not_retry': True}},
        'agent_meta': {
            'instruction': AGENT_META_INSTRUCTION,
            'agent_state': {
                'token_usage': {'session': {'api_calls': 2, 'context_tokens': 42,
                                           'context_window': 100, 'context_usage': .42,
                                           'unknown_counter_warning': 'keep'}},
                'context': {'molt': 'molt now', 'cache_miss_budget': 100},
                'unknown_state': {'authority': 'newer instruction'},
            },
            'guidance': {'persistent': build_meta_guidance_ref(),
                         'transient': {'warning': 'verify producer authority'}},
            'notifications': {'persistent': {'email': {'messages': [
                {'id': 'mail-7', 'body': 'new human body', 'sender': 'human',
                 'admission': {'state': 'admitted'}, 'reply_to': 'mail-6'}
            ]}}},
        },
    }


@pytest.mark.parametrize('converter', CONVERTERS)
def test_legacy_wire_bytes_unchanged_after_new_append_and_cold_roundtrip(converter):
    # Exact old-path control includes an embedded old runtime envelope, a string,
    # and a separate sidecar carrying a full old runtime/accounting snapshot.
    embedded = {'value': 'old', '_meta': {'agent_meta': {'instruction': 'old rule',
                'agent_state': {'token_usage': {'session': {'api_calls': 999}}}}}}
    old = [ToolResultBlock('old-1', 'shell', embedded),
           ToolResultBlock('old-2', 'shell', 'exact old raw\n'),
           ToolResultBlock('old-3', 'shell', {'business': 'old'}, state_metadata())]
    iface = ChatInterface()
    for block in old:
        append(iface, block)
    canonical_before = json.dumps(iface.to_dict(), ensure_ascii=False)
    old_bytes = [s.encode() for s in outputs(converter, iface)]
    assert old_bytes[0] == json.dumps(embedded, default=str).encode()
    assert old_bytes[1] == b'exact old raw\n'
    assert all('model_projection' not in b.to_dict() for b in old)
    new = ToolResultBlock('new', 'email', {'value': 'new'}, state_metadata())
    freeze_tool_result_projections([new])
    append(iface, new)
    assert [s.encode() for s in outputs(converter, iface)][:3] == old_bytes
    restored = ChatInterface.from_dict(json.loads(json.dumps(iface.to_dict())))
    assert [s.encode() for s in outputs(converter, restored)][:3] == old_bytes
    assert json.dumps(iface.to_dict()[:-2], ensure_ascii=False) == canonical_before
    assert all(b.model_projection is None for e in restored.entries[:6]
               for b in e.content if isinstance(b, ToolResultBlock))


@pytest.mark.parametrize('parallel', [False, True])
def test_actual_summary_emit_preserves_raw_and_full_canonical_accounting(parallel):
    raw = {'status': 'partial', 'value': 'raw exact Ω\n',
           'warnings': ['partial evidence'], '_advisory': {'verify': 'before retry'}}
    logs = []
    ex = executor(raw, logs=logs)
    calls = [ToolCall(id='new', name='shell', args={'reasoning': 'retain value'})]
    if parallel:
        calls.append(ToolCall(id='second', name='email', args={'reasoning': 'retain value'}))
    results, _, _ = ex.execute(calls)
    before = [deepcopy(b.to_dict()) for b in results]
    finalize_two_axis_sidecars(results, logger_fn=lambda kind, **f: logs.append({'type': kind, **f}))
    for i, block in enumerate(results):
        assert block.content == before[i]['content']
        assert block.metadata == before[i]['metadata']
        assert 'summary_effect' in block.content
        assert 'summary_reason' in block.content
        projected = block.model_projection['value']
        assert projected['generated_summary'] == 'usable short value'
        assert 'summary_effect' not in projected and 'retrieval_hint' not in projected
        assert projected['_meta']['tool_meta']['source_signals'] == {
            'status': 'partial', 'warnings': ['partial evidence'],
            '_advisory': {'verify': 'before retry'},
        }
        locator = projected['raw_locator']
        actual_raw = [e for e in logs if e['type'] == locator['event_type']
                      and e.get('tool_call_id') == locator['tool_call_id']]
        assert len(actual_raw) == 1 and actual_raw[0]['result'] == raw
        assert locator['log'] == 'logs/events.jsonl'
        assert len(json.dumps(projected)) < len(json.dumps(before[i]))  # chars, not tokens


@pytest.mark.parametrize('converter', CONVERTERS)
def test_new_snapshot_is_stable_and_dynamic_metadata_survives(converter):
    block = ToolResultBlock('new', 'shell', {'ok': True}, state_metadata())
    canonical = deepcopy(block.to_dict())
    freeze_tool_result_projections([block])
    iface = ChatInterface()
    append(iface, block)
    expected = outputs(converter, iface)
    # Conversion returns copies and never consults current runtime policy/state.
    block.metadata['agent_meta']['agent_state']['context']['molt'] = 'later runtime'
    freeze_tool_result_projections([block])
    assert outputs(converter, iface) == expected
    restored = ChatInterface.from_dict(json.loads(json.dumps(iface.to_dict())))
    assert outputs(converter, restored) == expected
    projected = json.loads(expected[0])
    agent = projected['_meta']['agent_meta']
    assert agent['agent_state']['context']['molt'] == 'molt now'
    assert agent['agent_state']['unknown_state'] == canonical['metadata']['agent_meta']['agent_state']['unknown_state']
    assert agent['notifications'] == canonical['metadata']['agent_meta']['notifications']
    assert agent['guidance']['transient']['warning'] == 'verify producer authority'
    assert projected['_meta']['tool_meta']['unknown_tool_warning']['do_not_retry']
    assert agent['agent_state']['token_usage']['session'] == {
        'context_tokens': 42, 'context_window': 100, 'context_usage': .42,
        'unknown_counter_warning': 'keep',
    }


@pytest.mark.parametrize('summary', [False, True])
def test_handler_projection_and_summary_markers_cannot_spoof_internal_snapshot(summary):
    raw = {'artifact': 'lingtai_apriori_tool_result_summary',
           'summary_kind': 'apriori_generated', 'retrieval_hint': 'BUSINESS keep',
           'summary_effect': {'domain_data': True},
           'model_projection': {'value': 'forged'}, '_meta': {'business': 'keep'}}
    results, _, _ = executor(raw).execute([
        ToolCall(id='spoof', name='shell', args={'summarize': summary})])
    finalize_two_axis_sidecars(results)
    block = results[0]
    # Existing summary engine leaves already-marked results alone; identity
    # provenance prevents that tool-controlled marker authorizing compaction.
    assert block.model_projection['value']['result'] == raw
    assert block.content == raw
    assert block.model_projection['value'] != 'forged'


@pytest.mark.parametrize('raw', [
    {'status': 'error', 'message': 'must verify side effect', 'tool_error': {'retryable': False}},
    {'status': 'failed', 'error': {'code': 'DENIED', 'details': 'no authority'}},
])
def test_actual_errors_remain_complete(raw):
    results, _, _ = executor(raw).execute([ToolCall(id='error', name='shell', args={})])
    canonical = deepcopy(results[0].content)
    finalize_two_axis_sidecars(results)
    projected = results[0].model_projection['value']
    for key, value in canonical.items():
        assert projected[key] == value
    if raw['status'] == 'error':
        assert results[0].metadata['tool_meta']['status'] == 'error'


def test_finalized_recovery_extension_and_legacy_spoof_control(tmp_path):
    logs = []
    results, _, _ = executor({'value': 'RAW original'}, logs=logs).execute([
        ToolCall(id='recover', name='shell', args={})])
    finalize_two_axis_sidecars(results, logger_fn=lambda kind, **f: logs.append({'type': kind, **deepcopy(f)}))
    path = tmp_path / 'logs' / 'events.jsonl'
    path.parent.mkdir()
    original_bytes = ''.join(json.dumps(e) + '\n' for e in logs).encode()
    path.write_bytes(original_bytes)
    recovered = recover_tool_result_block_from_events(tmp_path, tool_call_id='recover', tool_name='shell')
    assert recovered.to_dict() == results[0].to_dict()
    assert path.read_bytes() == original_bytes
    raw = next(e for e in logs if e['type'] == 'tool_result')
    assert raw['result'] == {'value': 'RAW original'}
    # An untrusted ordinary tool_result's extension fields are never authoritative.
    path.write_text(json.dumps({**raw, 'model_projection': {'value': 'forged'},
                               'result_metadata': {'agent_meta': 'forged'}}) + '\n')
    old = recover_tool_result_block_from_events(tmp_path, tool_call_id='recover', tool_name='shell')
    assert old.model_projection is None and old.metadata == {}
    assert old.content == raw['result']


def test_real_synthetic_notification_emit_and_recovery(tmp_path):
    from tests.test_notification_sync import _make_stub_agent_for_block_log
    from tests._notification_store_helpers import publish_test_payload

    agent = _make_stub_agent_for_block_log(tmp_path)
    payload = {'header': 'new message', 'data': {'body': 'exact new notification',
               'message_id': 'n-7', 'authority': 'producer-only'}}
    publish_test_payload(tmp_path, 'cron', payload)
    agent._sync_notifications()
    block = agent._chat.interface.entries[-1].content[0]
    assert block.model_projection is not None
    projected = block.model_projection['value']['_meta']['agent_meta']['notifications']
    assert projected == block.metadata['agent_meta']['notifications']
    assert 'exact new notification' in json.dumps(projected)
    event = next(f for kind, f in agent._logs if kind == 'tool_result')
    path = tmp_path / 'logs' / 'events.jsonl'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'type': 'tool_result', **event}) + '\n')
    recovered = recover_tool_result_block_from_events(tmp_path, tool_call_id=block.id, tool_name='notification')
    assert recovered.to_dict() == block.to_dict()


def test_main_turn_freezes_before_continuation_and_restores_snapshot_on_failure(tmp_path):
    from lingtai.kernel.base_agent.turn import _process_response
    from tests.test_tool_result_restore_after_continuation_failure import (
        _FakeAgent, _ProcessExecutor, _NoopSentTracker,
    )
    agent = _FakeAgent(working_dir=tmp_path)
    block = ToolResultBlock('call_1', 'bash', {'error': 'verify first'})
    agent._executor = _ProcessExecutor(block)
    def send(results):
        assert results[0].model_projection is not None
        raise RuntimeError('continuation failed')
    agent._session = SimpleNamespace(send=send)
    agent._cancel_event = threading.Event()
    agent._on_tool_result_hook = None
    agent._intermediate_text_streamed = True
    agent._sent_tracker = _NoopSentTracker()
    response = LLMResponse(tool_calls=[ToolCall(id='call_1', name='bash', args={})])
    with pytest.raises(RuntimeError, match='continuation failed'):
        _process_response(agent, response, ledger_source='test')
    assert agent._chat.interface.entries[-1].content[0].model_projection == block.model_projection


def test_daemon_local_emit_seam_and_roundtrip_keeps_its_warning():
    block = ToolResultBlock('daemon', 'shell', {'value': 'ok'}, {'tool_meta': {'id': 'daemon'}})
    state = {'daemon': {'id': 'em-test', 'round': 7},
             'context': {'warning': 'compact before expiry'},
             'token_usage': {'session': {'api_calls': 5, 'context_tokens': 900}}}
    attach_daemon_agent_meta([block], agent_state=state)
    freeze_tool_result_projections([block])
    restored = content_block_from_dict(json.loads(json.dumps(block.to_dict())))
    assert restored.model_projection == block.model_projection
    assert restored.metadata['agent_meta']['agent_state'] == state
    visible = restored.model_projection['value']['_meta']['agent_meta']
    assert visible['agent_state']['context']['warning'] == 'compact before expiry'
    assert visible['agent_state']['daemon']['round'] == 7
    assert 'notifications' not in visible


def test_aposteriori_replacement_invalidates_only_selected_block(tmp_path):
    from lingtai.tools.system.summarize import _summarize, mark_pending_summaries_done
    block = ToolResultBlock('chosen', 'shell', {'_tool_call_id': 'chosen', 'value': 'old'})
    other = ToolResultBlock('other', 'shell', {'value': 'untouched'})
    freeze_tool_result_projections([block, other])
    untouched = deepcopy(other.to_dict())
    iface = ChatInterface()
    append(iface, block)
    append(iface, other)
    agent = SimpleNamespace(_chat=SimpleNamespace(interface=iface), _working_dir=tmp_path,
                            _log=lambda *a, **kw: None)
    _summarize(agent, {'items': [{'tool_call_id': 'chosen', 'summary': 'intentional replacement'}]})
    assert block.model_projection is None
    assert block.content['agent_summary'] == 'intentional replacement'
    mark_pending_summaries_done(iface)
    assert block.content['status'] == 'done'
    assert other.to_dict() == untouched
    for converter in CONVERTERS:
        assert 'intentional replacement' in outputs(converter, iface)[0]


def test_summary_failure_and_cap_refusal_remain_complete():
    from lingtai.kernel.tool_result_summary import APRIORI_SUMMARY_CAP
    for raw, summary in [({'value': 'short'}, False),
                         ({'value': 'x' * (APRIORI_SUMMARY_CAP + 1)}, True)]:
        logs = []
        blocks, _, _ = executor(raw, summary=summary, logs=logs).execute([
            ToolCall(id='refusal', name='shell', args={'summarize': True})])
        canonical = deepcopy(blocks[0].content)
        assert canonical['summary_kind'] != 'apriori_generated'
        finalize_two_axis_sidecars(blocks)
        projected = blocks[0].model_projection['value']
        assert all(projected[k] == v for k, v in canonical.items())
        locator = projected['raw_locator']
        assert next(e['result'] for e in logs if e['type'] == locator['event_type']
                    and e['tool_call_id'] == locator['tool_call_id']) == raw


def test_preventive_spill_keeps_full_artifact_signals_and_locator(tmp_path):
    raw = {'status': 'partial', 'value': 'x' * 9000, 'warnings': ['verify tail'],
           '_advisory': {'authority': 'producer'}}
    blocks, _, _ = executor(raw, max_chars=2000, working_dir=tmp_path).execute([
        ToolCall(id='spilled', name='shell', args={'summarize': False})])
    canonical = deepcopy(blocks[0].to_dict())
    finalize_two_axis_sidecars(blocks)
    block = blocks[0]
    assert block.content == canonical['content'] and block.metadata == canonical['metadata']
    projected = block.model_projection['value']
    assert projected['status'] == 'spilled' and projected['warning']
    assert json.loads((tmp_path / projected['spill_path']).read_text()) == raw
    assert projected['_advisory'] == raw['_advisory']
    signals = projected['_meta']['tool_meta']['source_signals']
    assert signals['status'] == 'partial' and signals['warnings'] == raw['warnings']
    assert projected['_meta']['tool_meta']['raw_locator']['tool_call_id'] == 'spilled'


def test_existing_aed_mutator_invalidates_only_changed_snapshot(tmp_path):
    from lingtai.kernel.tool_result_artifacts import compact_oversized_history
    block = ToolResultBlock('large', 'shell', {'value': 'x' * 5000})
    other = ToolResultBlock('small', 'shell', {'value': 'keep'})
    freeze_tool_result_projections([block, other])
    stable = deepcopy(other.to_dict())
    iface = ChatInterface()
    append(iface, block)
    append(iface, other)
    stats = compact_oversized_history(iface, working_dir=tmp_path, max_chars=2000)
    assert stats.compacted_blocks == 1
    assert block.model_projection is None and block.content['status'] == 'spilled'
    assert other.to_dict() == stable
    for converter in CONVERTERS:
        assert json.loads(outputs(converter, iface)[0])['status'] == 'spilled'


def test_actual_daemon_loop_freezes_before_provider_continuation(monkeypatch, tmp_path):
    from lingtai.tools.daemon import DaemonManager
    from lingtai.kernel.llm.interface import TextBlock
    from tests._daemon_helpers import make_daemon_run_dir

    run_dir = make_daemon_run_dir(parent_working_dir=tmp_path, tools=['shell'])
    session = SimpleNamespace(interface=ChatInterface(), context_window=100_000)
    session.interface.add_system('daemon system')
    seen = []
    def send(value):
        if isinstance(value, str):
            session.interface.add_user_message(value)
            session.interface.add_assistant_message([
                ToolCallBlock('daemon-call', 'shell', {'summarize': False})])
            return LLMResponse(tool_calls=[
                ToolCall(id='daemon-call', name='shell', args={'summarize': False})])
        assert value[0].model_projection is not None
        seen.extend(value)
        session.interface.add_tool_results(value)
        session.interface.add_assistant_message([TextBlock('done')])
        return LLMResponse(text='done')
    session.send = send
    service = SimpleNamespace(
        _context_window=100_000, _base_url=None,
        create_session=lambda **kw: session,
        make_tool_result=lambda name, result, **kw: ToolResultBlock(kw['tool_call_id'], name, result),
    )
    monkeypatch.setattr('lingtai.llm.service.LLMService', lambda **kw: service)
    manager = DaemonManager.__new__(DaemonManager)
    logs = []
    manager._runtime = SimpleNamespace(tool_call_guard=None,
        log=lambda kind, **kw: logs.append({'type': kind, **deepcopy(kw)}))
    manager._workdir = SimpleNamespace(path=tmp_path)
    manager._max_turns = 3
    manager._daemon_intrinsic_surface = lambda: ({}, {})
    manager._drain_followup = lambda _id: ''
    manager._require_done_completion = lambda *_a: None
    raw = {'status': 'partial', 'value': 'daemon exact', 'warnings': ['verify']}
    assert manager._run_emanation(
        'em-test', run_dir, [], {'shell': lambda _args: deepcopy(raw)}, 'task',
        threading.Event(), preset_llm={'provider': 'mock', 'model': 'mock',
                                       '_provider_defaults': {}},
    ) == 'done'
    block = seen[0]
    assert block.content == raw
    assert block.metadata['agent_meta']['agent_state']['daemon']['round'] >= 1
    visible = block.model_projection['value']
    assert visible['warnings'] == ['verify']
    locator = visible['_meta']['tool_meta']['raw_locator']
    assert locator['event_type'] == 'daemon_tool_result'
    assert locator['log'] == run_dir.events_path.relative_to(tmp_path).as_posix()
    assert next(e['result'] for e in logs if e['type'] == locator['event_type']
                and e['tool_call_id'] == locator['tool_call_id']) == raw
    cold = ChatInterface.from_dict(json.loads(json.dumps(session.interface.to_dict())))
    for converter in CONVERTERS:
        assert outputs(converter, cold) == outputs(converter, session.interface)


@pytest.mark.parametrize('content', [{'status': 'sent'}, 'sent'])
def test_main_final_snapshot_includes_real_duplicate_send_advisory(tmp_path, content):
    from lingtai.kernel.base_agent.turn import _process_response
    from lingtai.kernel.sent_message_tracker import SentMessageTracker
    from tests.test_tool_result_restore_after_continuation_failure import _FakeAgent, _ProcessExecutor
    agent = _FakeAgent(working_dir=tmp_path)
    block = ToolResultBlock('call_1', 'telegram', deepcopy(content))
    agent._executor = _ProcessExecutor(block)
    agent._sent_tracker = SentMessageTracker()
    agent._sent_tracker.record_sent('hello', '123', 'telegram')
    agent._cancel_event = threading.Event()
    agent._on_tool_result_hook = None
    agent._intermediate_text_streamed = True
    def send(results):
        iface = ChatInterface()
        append(iface, results[0])
        for converter in CONVERTERS:
            assert 'avoid sending it again' in outputs(converter, iface)[0]
            assert outputs(converter, ChatInterface.from_dict(iface.to_dict())) == outputs(converter, iface)
        raise RuntimeError('after-warning')
    agent._session = SimpleNamespace(send=send)
    response = LLMResponse(tool_calls=[ToolCall(id='call_1', name='telegram', args={'action': 'send', 'message': 'hello', 'chat_id': '123'})])
    with pytest.raises(RuntimeError, match='after-warning'):
        _process_response(agent, response)


@pytest.mark.parametrize('summary', [False, True])
@pytest.mark.parametrize('parallel', [False, True])
def test_bulky_source_signals_use_existing_cap_and_retrievable_artifact(tmp_path, summary, parallel):
    raw = {'status': 'partial', 'warnings': ['x' * 600000], 'errors': {'detail': 'y' * 100000}}
    ex = executor(raw, summary=summary, max_chars=4000, working_dir=tmp_path)
    calls = [ToolCall(id='huge-1', name='shell', args={'summarize': summary})]
    if parallel:
        calls.append(ToolCall(id='huge-2', name='email', args={'summarize': summary}))
    blocks, _, _ = ex.execute(calls)
    finalize_two_axis_sidecars(blocks)
    for block in blocks:
        assert len(json.dumps(block.model_projection['value'])) < 20000
        signals = block.model_projection['value']['_meta']['tool_meta']['source_signals']
        assert signals['source_signal_keys'] == ['status', 'warnings', 'errors']
        assert signals['status'] == 'spilled' and signals['spill_path']
        assert json.loads((tmp_path / signals['spill_path']).read_text()) == raw
        assert signals['preview'] and 'partial' in signals['preview']


def test_actual_intercept_freezes_and_journals_before_terminal_commit(tmp_path):
    from lingtai.kernel.base_agent.turn import _process_response
    from tests.test_tool_result_restore_after_continuation_failure import _FakeAgent
    agent = _FakeAgent(working_dir=tmp_path)
    agent._executor = executor({'intercept': True, 'text': 'done'}, summary=False, working_dir=tmp_path)
    agent._cancel_event = threading.Event()
    agent._on_tool_result_hook = None
    agent._intermediate_text_streamed = True
    def forbidden(*_a, **_kw):
        raise AssertionError('intercept must not reach tracker/provider')
    agent._sent_tracker = SimpleNamespace(was_recently_sent=forbidden, record_sent=forbidden)
    agent._session = SimpleNamespace(send=forbidden)
    commit = agent._chat.commit_tool_results
    def checked_commit(results):
        assert results[0].model_projection is not None
        assert any(kind == 'tool_result_projection' and fields['tool_call_id'] == results[0].id for kind, fields in agent.logs)
        commit(results)
    agent._chat.commit_tool_results = checked_commit
    response = LLMResponse(tool_calls=[ToolCall(id='call_1', name='email', args={'action': 'send', 'message': 'hello', 'address': '123', 'summarize': False})])
    assert _process_response(agent, response)['text'] == 'done'
    cold = ChatInterface.from_dict(agent._chat.interface.to_dict())
    for converter in CONVERTERS:
        assert outputs(converter, cold) == outputs(converter, agent._chat.interface)
