from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import websockets

from .audio import PCMCapture, PCMPlayer
from .bridge import HermesBridge

log = logging.getLogger(__name__)

VOICE_SHELL_INSTRUCTIONS = """
You are the realtime voice I/O shell for Hermes Agent. Hermes is the sole reasoning and agent brain.

Rules:
1. For EVERY meaningful user utterance that asks for information, reasoning, advice, memory, planning,
   coding, search, files, email, servers, tools, or any substantive conversation, you MUST call
   hermes_start. Do not answer from your own knowledge.
2. If a Hermes task is already running and the user changes/corrects/narrows that same task, call
   hermes_steer instead of starting a replacement task.
3. If the user asks to cancel/stop the running Hermes task, call hermes_stop.
4. You may directly handle only pure voice-interface control such as: stop speaking, repeat the last
   spoken sentence, speak slower/faster, volume/style requests, greetings/backchannels with no factual content.
5. After a Hermes tool result arrives, speak it faithfully and naturally. Do not add independent facts.
6. If a Hermes tool reports status=running, only acknowledge briefly that Hermes is working. Never guess
   the result; the final result will arrive automatically.
7. Keep voice responses concise unless the Hermes result itself requires detail.
""".strip()


class QwenRealtimeClient:
    def __init__(self, *, api_key: str, base_url: str, model: str, voice: str,
                 turn_detection: str, max_history_turns: int, bridge: HermesBridge,
                 player: PCMPlayer, capture: PCMCapture, audio_mode: str = "headset") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("?")
        self.model = model
        self.voice = voice
        self.turn_detection = turn_detection
        self.max_history_turns = max_history_turns
        self.bridge = bridge
        self.player = player
        self.capture = capture
        self.audio_mode = audio_mode
        self.ws: Any = None
        self._send_lock = asyncio.Lock()
        self._response_active = False
        self._user_speaking = False
        self._closed = asyncio.Event()
        self._pending_announcements: asyncio.Queue[str] = asyncio.Queue()
        self.bridge.set_announcement_callback(self.announce)

    async def connect(self) -> None:
        url = f"{self.base_url}?model={self.model}"
        headers = {"Authorization": f"Bearer {self.api_key}", "x-dashscope-dataInspection": "disable"}
        self.ws = await websockets.connect(url, additional_headers=headers, max_size=None)
        await self.send({"type": "session.update", "session": {
            "modalities": ["text", "audio"],
            "voice": self.voice,
            "instructions": VOICE_SHELL_INSTRUCTIONS,
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "max_history_turns": self.max_history_turns,
            "enable_search": False,
            "tools": self.bridge.tool_schemas(),
            "turn_detection": {"type": self.turn_detection},
        }})

    async def send(self, event: dict[str, Any]) -> None:
        if not self.ws:
            raise RuntimeError("Qwen websocket is not connected")
        async with self._send_lock:
            await self.ws.send(json.dumps(event, ensure_ascii=False))

    async def send_audio(self, chunk: bytes) -> None:
        await self.send({"type": "input_audio_buffer.append",
                         "audio": base64.b64encode(chunk).decode("ascii")})

    async def _write_tool_output(self, call_id: str, output: str) -> None:
        await self.send({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": call_id, "output": output
        }})
        await self.send({"type": "response.create", "response": {"modalities": ["audio", "text"]}})

    async def _handle_tool_call(self, event: dict[str, Any]) -> None:
        call_id = event.get("call_id")
        name = event.get("name", "")
        raw_args = event.get("arguments", "{}")
        try:
            args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            args = {}
        try:
            output = await self.bridge.dispatch(name, args)
        except Exception as exc:
            log.exception("Tool call failed: %s", name)
            output = json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
        await self._write_tool_output(call_id, output)

    async def announce(self, text: str) -> None:
        await self._pending_announcements.put(text)
        await self._flush_announcements()

    async def _flush_announcements(self) -> None:
        if self._response_active or self._user_speaking or not self.ws:
            return
        while not self._pending_announcements.empty():
            text = await self._pending_announcements.get()
            await self.send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "system",
                "content": [{"type": "input_text", "text": text}]
            }})
            await self.send({"type": "response.create", "response": {"modalities": ["audio", "text"]}})
            return

    async def receive_loop(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                event = json.loads(raw)
                kind = event.get("type", "")
                if kind == "session.updated":
                    log.info("Qwen session ready: %s", event.get("session", {}).get("id", ""))
                elif kind == "response.created":
                    self._response_active = True
                elif kind == "response.done":
                    self._response_active = False
                    await self._flush_announcements()
                elif kind == "input_audio_buffer.speech_started":
                    self._user_speaking = True
                    self.player.clear()
                elif kind == "input_audio_buffer.speech_stopped":
                    self._user_speaking = False
                elif kind == "response.audio.delta":
                    self.player.add(base64.b64decode(event.get("delta", "")))
                elif kind == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "")
                    if transcript:
                        print(f"\n[You] {transcript}")
                elif kind == "response.audio_transcript.done":
                    transcript = event.get("transcript", "")
                    if transcript:
                        print(f"[Voice] {transcript}")
                elif kind == "response.function_call_arguments.done":
                    asyncio.create_task(self._handle_tool_call(event), name=f"tool-{event.get('call_id', '')}")
                elif kind == "error":
                    log.error("Qwen error: %s", event.get("error", event))
        finally:
            self._closed.set()

    async def capture_loop(self) -> None:
        while not self._closed.is_set():
            chunk = await asyncio.to_thread(self.capture.read)
            if self.audio_mode == "safe" and (self._response_active or self.player.is_playing):
                await asyncio.sleep(0.01)
                continue
            await self.send_audio(chunk)
            await asyncio.sleep(0)

    async def run(self) -> None:
        await self.connect()
        print("Hermes Voice Gateway connected. Speak naturally; Ctrl+C to exit.")
        if self.audio_mode == "headset":
            print("Desktop full-duplex mode: use headphones/headset to prevent acoustic echo.")
        await asyncio.gather(self.receive_loop(), self.capture_loop())

    async def close(self) -> None:
        self._closed.set()
        if self.ws:
            await self.ws.close()
