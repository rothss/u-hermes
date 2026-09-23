import asyncio
import json
from unittest.mock import Mock

import pytest

from hermes_voice_gateway.qwen import QwenRealtimeClient


class Socket:
    def __init__(self):
        self.events = []

    async def send(self, raw):
        await asyncio.sleep(0)
        self.events.append(json.loads(raw))


def client():
    q = QwenRealtimeClient(api_key='', base_url='', model='', voice='',
                           turn_detection='smart_turn', max_history_turns=10,
                           bridge=Mock(), player=Mock(), capture=Mock())
    q.ws = Socket()
    q._session_ready = True
    return q


@pytest.mark.asyncio
async def test_background_result_is_latest_matched_tool_output():
    q = client()
    await asyncio.gather(q.announce('result one'), q.announce('result two'))
    assert [e['type'] for e in q.ws.events] == [
        'conversation.item.create', 'conversation.item.create', 'response.create']
    call, output = [e['item'] for e in q.ws.events[:2]]
    assert call['type'] == 'function_call'
    assert output['type'] == 'function_call_output'
    assert call['call_id'] == output['call_id']
    assert json.loads(output['output'])['result'] == 'result one'
    assert q._pending_announcements.qsize() == 1
    q._response_active = False
    await q._flush_announcements()
    assert json.loads(q.ws.events[4]['item']['output'])['result'] == 'result two'
    assert q.ws.events[3]['item']['call_id'] != call['call_id']


@pytest.mark.asyncio
async def test_result_waits_for_session_and_survives_send_failure():
    q = client()
    q._session_ready = False
    await q.announce('preserve me')
    assert not q.ws.events
    q._session_ready = True
    async def fail(raw):
        raise ConnectionError('disconnected')
    q.ws.send = fail
    with pytest.raises(ConnectionError):
        await q._flush_announcements()
    assert not q._response_active
    assert q._pending_announcements.qsize() == 1
    q.ws = Socket()
    await q._flush_announcements()
    assert json.loads(q.ws.events[1]['item']['output'])['result'] == 'preserve me'
