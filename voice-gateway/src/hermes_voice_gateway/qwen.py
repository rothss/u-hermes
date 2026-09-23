from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from contextlib import suppress
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
        self._announcement_lock = asyncio.Lock()
        self._session_ready = False
        self._session_ready_event = asyncio.Event()
        self._connection_ever_ready = False
        self._seen_tool_call_ids: set[str] = set()
        self._response_active = False
        self._user_speaking = False
        self._closed = asyncio.Event()
        self._connection_lost = asyncio.Event()
        self._pending_announcements: asyncio.Queue[str] = asyncio.Queue()
        self._response_started_at: float | None = None
        self._first_audio_logged = False
        self._connected_at: float | None = None
        self.bridge.set_announcement_callback(self.announce)

    async def connect(self) -> None:
        self._session_ready = False
        self._session_ready_event.clear()
        self._connection_ever_ready = False
        self._seen_tool_call_ids.clear()
        started = time.perf_counter()
        separator = "&" if "?" in self.base_url else "?"
        url = self.base_url if "model=" in self.base_url else f"{self.base_url}{separator}model={self.model}"
        headers = {"Authorization": f"Bearer {self.api_key}", "x-dashscope-dataInspection": "disable"}
        self.ws = await websockets.connect(url, additional_headers=headers, max_size=None)
        self._response_active = False
        self._user_speaking = False
        self._connection_lost.clear()
        self._connected_at = time.perf_counter()
        log.info("VOICE Qwen websocket connected handshake_ms=%.1f", (self._connected_at - started) * 1000)
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
        call_id = str(event.get("call_id") or "")
        name = event.get("name", "")
        raw_args = event.get("arguments", "{}")
        try:
            args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            args = {}
        try:
            output = await self.bridge.dispatch(name, args, request_id=call_id or None)
        except Exception as exc:
            log.exception("Tool call failed: %s", name)
            output = json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
        try:
            await self._write_tool_output(call_id, output)
        except Exception:
            # Never re-run an already-started Hermes action just because the
            # speech websocket disappeared. Preserve terminal results and let a
            # fresh Qwen session announce them after reconnect.
            log.exception("VOICE failed to return tool output call_id=%s name=%s", call_id, name)
            self._connection_lost.set()
            if name == "hermes_start":
                try:
                    data = json.loads(output)
                except json.JSONDecodeError:
                    data = {}
                if data.get("status") != "running":
                    result = data.get("result") or data.get("error") or output
                    await self._pending_announcements.put(
                        "Recovered Hermes result after the voice connection dropped:\n" + str(result)
                    )

    async def announce(self, text: str) -> None:
        await self._pending_announcements.put(text)
        await self._flush_announcements()

    async def _flush_announcements(self) -> None:
        async with self._announcement_lock:
            if (self._response_active or self._user_speaking or not self.ws
                    or not self._session_ready or self._connection_lost.is_set()
                    or self._pending_announcements.empty()):
                return
            text = await self._pending_announcements.get()
            # A system message alone can leave Qwen answering the old running
            # tool output. Inject a NEW matched call/result pair as documented
            # history, so the next inference consumes the completed result.
            call_id = "background_" + uuid.uuid4().hex
            self._response_active = True
            try:
                await self.send({"type": "conversation.item.create", "item": {
                    "type": "function_call", "call_id": call_id,
                    "name": "hermes_start",
                    "arguments": json.dumps({"request": "Deliver the completed background task result."})
                }})
                await self._write_tool_output(call_id, json.dumps({
                    "status": "completed", "result": text,
                    "instruction": "The task has finished. Speak the supplied result now. Do not say you are still working or call any tool."
                }, ensure_ascii=False))
                log.info("VOICE background result submitted call_id=%s chars=%d", call_id, len(text))
            except BaseException:
                self._response_active = False
                await self._pending_announcements.put(text)
                raise

    async def receive_loop(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                event = json.loads(raw)
                kind = event.get("type", "")
                if kind == "session.updated":
                    self._session_ready = True
                    self._session_ready_event.set()
                    self._connection_ever_ready = True
                    elapsed = ((time.perf_counter() - self._connected_at) * 1000
                               if self._connected_at else 0.0)
                    log.info("VOICE Qwen session ready session_id=%s session_update_ms=%.1f",
                             event.get("session", {}).get("id", ""), elapsed)
                    await self._flush_announcements()
                elif kind == "response.created":
                    self._response_active = True
                    self._response_started_at = time.perf_counter()
                    self._first_audio_logged = False
                elif kind == "response.done":
                    self._response_active = False
                    elapsed = ((time.perf_counter() - self._response_started_at) * 1000
                               if self._response_started_at else 0.0)
                    log.info("VOICE Qwen response done response_ms=%.1f", elapsed)
                    await self._flush_announcements()
                elif kind == "input_audio_buffer.speech_started":
                    self._user_speaking = True
                    self.player.clear()
                    log.info("VOICE speech_started")
                elif kind == "input_audio_buffer.speech_stopped":
                    self._user_speaking = False
                    log.info("VOICE speech_stopped")
                elif kind == "response.audio.delta":
                    if not self._first_audio_logged:
                        elapsed = ((time.perf_counter() - self._response_started_at) * 1000
                                   if self._response_started_at else 0.0)
                        log.info("VOICE first_audio_delta response_ms=%.1f", elapsed)
                        self._first_audio_logged = True
                    self.player.add(base64.b64decode(event.get("delta", "")))
                elif kind == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "")
                    if transcript:
                        log.info("VOICE user transcript chars=%d", len(transcript))
                        print(f"\n[You] {transcript}")
                elif kind == "response.audio_transcript.done":
                    transcript = event.get("transcript", "")
                    if transcript:
                        log.info("VOICE response transcript chars=%d", len(transcript))
                        print(f"[Voice] {transcript}")
                elif kind == "response.function_call_arguments.done":
                    call_id = str(event.get("call_id") or "")
                    log.info("QWEN tool call name=%s call_id=%s arguments_chars=%d",
                             event.get("name", ""), call_id,
                             len(event.get("arguments", "") or ""))
                    if call_id and call_id in self._seen_tool_call_ids:
                        log.warning("QWEN duplicate tool call ignored call_id=%s", call_id)
                    else:
                        if call_id:
                            self._seen_tool_call_ids.add(call_id)
                        asyncio.create_task(self._handle_tool_call(event), name=f"tool-{call_id}")
                elif kind == "error":
                    log.error("Qwen error: %s", event.get("error", event))
        finally:
            self._session_ready = False
            self._session_ready_event.clear()
            self._connection_lost.set()

    async def capture_loop(self) -> None:
        # Qwen only allows turn-detection/audio-format changes before the first
        # audio frame. Wait for session.updated so smart_turn is definitely live
        # before the microphone starts feeding the socket.
        await self._session_ready_event.wait()
        while not self._closed.is_set() and not self._connection_lost.is_set():
            chunk = await asyncio.to_thread(self.capture.read)
            if self._connection_lost.is_set() or self._closed.is_set():
                return
            if self.audio_mode == "safe" and (self._response_active or self.player.is_playing):
                await asyncio.sleep(0.01)
                continue
            try:
                await self.send_audio(chunk)
            except Exception:
                self._connection_lost.set()
                return
            await asyncio.sleep(0)

    async def run(self) -> None:
        reconnect_delay = 1.0
        announced = False
        while not self._closed.is_set():
            receiver: asyncio.Task[None] | None = None
            capture: asyncio.Task[None] | None = None
            try:
                await self.connect()
                if not announced:
                    print("Hermes Voice Gateway connected. Speak naturally; Ctrl+C to exit.")
                    if self.audio_mode == "headset":
                        print("Desktop full-duplex mode: use headphones/headset to prevent acoustic echo.")
                    announced = True
                receiver = asyncio.create_task(self.receive_loop(), name="qwen-receive")
                capture = asyncio.create_task(self.capture_loop(), name="audio-capture")
                done, pending = await asyncio.wait(
                    {receiver, capture}, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    error = task.exception()
                    if error is not None:
                        raise error
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._closed.is_set():
                    log.warning("VOICE Qwen connection ended (%s); reconnecting in %.1fs",
                                type(exc).__name__, reconnect_delay)
            finally:
                for task in (receiver, capture):
                    if task and not task.done():
                        task.cancel()
                if receiver or capture:
                    await asyncio.gather(*(task for task in (receiver, capture) if task),
                                         return_exceptions=True)
                if self.ws:
                    with suppress(Exception):
                        await self.ws.close()
                    self.ws = None
            if not self._closed.is_set():
                delay = 1.0 if self._connection_ever_ready else reconnect_delay
                await asyncio.sleep(delay)
                reconnect_delay = (1.0 if self._connection_ever_ready
                                   else min(reconnect_delay * 2, 10.0))

    async def close(self) -> None:
        self._closed.set()
        self._connection_lost.set()
        if self.ws:
            with suppress(Exception):
                await self.ws.close()
