"""Opt-in live Qwen regression; no microphone, speaker, or Hermes task execution."""
import asyncio
import json
import time

from hermes_voice_gateway.config import Settings
from hermes_voice_gateway.qwen import QwenRealtimeClient
from hermes_voice_gateway.bridge import HermesBridge


class Sink:
    is_playing = False
    def add(self, chunk):
        pass
    def clear(self):
        pass


async def main():
    settings = Settings()
    bridge = HermesBridge(None)
    client = QwenRealtimeClient(
        api_key=settings.dashscope_api_key, base_url=settings.qwen_realtime_url,
        model=settings.qwen_realtime_model, voice=settings.qwen_voice,
        turn_detection=settings.qwen_turn_detection, max_history_turns=10,
        bridge=bridge, player=Sink(), capture=None)
    async def response():
        audio_bytes = 0
        transcript = ''
        async with asyncio.timeout(35):
            while True:
                event = json.loads(await client.ws.recv())
                kind = event.get('type')
                if kind == 'error':
                    raise RuntimeError(event.get('error'))
                if kind == 'response.audio.delta':
                    audio_bytes += len(event.get('delta', ''))
                if kind == 'response.audio_transcript.done':
                    transcript += event.get('transcript', '')
                if kind == 'response.function_call_arguments.done':
                    raise AssertionError('Unexpected repeated tool call')
                if kind == 'response.done':
                    client._response_active = False
                    print(json.dumps({'transcript': transcript, 'audio_base64_chars': audio_bytes}, ensure_ascii=False))
                    return transcript, audio_bytes
    try:
        await client.connect()
        async with asyncio.timeout(15):
            while json.loads(await client.ws.recv()).get('type') != 'session.updated':
                pass
        client._session_ready = True
        await client.send({'type': 'conversation.item.create', 'item': {
            'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': '帮我查询海口的天气。'}]}})
        await client.send({'type': 'conversation.item.create', 'item': {
            'type': 'function_call', 'name': 'hermes_start', 'call_id': 'probe_running',
            'arguments': json.dumps({'request': '查询海口天气'}, ensure_ascii=False)}})
        await client._write_tool_output('probe_running', json.dumps({'status': 'running',
            'instruction': '仅告知正在查询，最终结果稍后自动到达。'}, ensure_ascii=False))
        await response()
        started = time.perf_counter()
        await client.announce('Hermes result: 海口测试天气为多云，最高31摄氏度，出门请带伞。')
        transcript, audio = await response()
        assert '多云' in transcript and '伞' in transcript and audio > 0, transcript
        print(f'PASS final result audio generated in {time.perf_counter()-started:.2f}s')
    finally:
        await client.close()


if __name__ == '__main__':
    asyncio.run(main())
