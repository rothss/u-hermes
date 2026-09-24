from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .bridge import HermesBridge
from .config import Settings
from .hermes import HermesClient
from .qwen import VOICE_SHELL_INSTRUCTIONS

log = logging.getLogger(__name__)

_SAFE_CLIENT_ID = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_CACHED_TOOL_RESULTS = 256


def _safe_client_id(value: str) -> str:
    cleaned = _SAFE_CLIENT_ID.sub("-", (value or "mobile").strip()).strip("-")
    return (cleaned or "mobile")[:48]


def derive_aoq_token_url(settings: Settings) -> str:
    if settings.qwen_aoq_token_url.strip():
        return settings.qwen_aoq_token_url.strip()
    parsed = urlsplit(settings.qwen_realtime_url)
    if not parsed.netloc:
        raise RuntimeError("QWEN_AOQ_TOKEN_URL is required when QWEN_REALTIME_URL has no host")
    query = urlencode({"model": settings.qwen_realtime_model})
    return urlunsplit(("https", parsed.netloc, "/api/v1/webrtc/realtime", query, ""))


def extract_aoq_credentials(payload: dict[str, Any]) -> dict[str, Any]:
    candidates = [payload]
    for key in ("output", "data", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)

    source: dict[str, Any] | None = None
    for candidate in candidates:
        if candidate.get("aoqTokenForClient"):
            source = candidate
            break
    if source is None:
        raise ValueError("Bailian response did not include aoqTokenForClient")

    extra = source.get("extraInfo") if isinstance(source.get("extraInfo"), dict) else {}
    result = {
        "aoqTokenForClient": source.get("aoqTokenForClient"),
        "sid": source.get("sid"),
        "clientRelayCertFingerprint": source.get("clientRelayCertFingerprint"),
        "clientRelayEndpoints": source.get("clientRelayEndpoints"),
        "workspaceIdHash": extra.get("workspaceIdHash") or source.get("workspaceIdHash"),
    }
    missing = [key for key, value in result.items() if value in (None, "", [])]
    if missing:
        raise ValueError("Bailian response missing AOQ fields: " + ", ".join(missing))
    return result


async def fetch_aoq_credentials(settings: Settings) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
        "x-dashscope-rtc-transport": "moq",
    }
    # clientIp is intentionally omitted. Bailian documents it as optional, and
    # the AppServer may not know the phone's true public IP behind NAT/tunnels.
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        response = await client.post(derive_aoq_token_url(settings), headers=headers, json={})
        response.raise_for_status()
        data = response.json()
    return extract_aoq_credentials(data)


@dataclass
class MobileAgentSession:
    client_id: str
    hermes: HermesClient
    bridge: HermesBridge
    socket: WebSocket | None = None
    pending_events: deque[dict[str, Any]] = field(default_factory=deque)
    tool_results: dict[str, str] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def push(self, event: dict[str, Any]) -> None:
        async with self.send_lock:
            if self.socket is None:
                self.pending_events.append(event)
                return
            try:
                await self.socket.send_json(event)
            except Exception:
                self.pending_events.append(event)
                self.socket = None
                raise

    async def attach(self, socket: WebSocket) -> None:
        async with self.send_lock:
            self.socket = socket
            while self.pending_events:
                event = self.pending_events[0]
                await socket.send_json(event)
                self.pending_events.popleft()

    async def detach(self, socket: WebSocket) -> None:
        async with self.send_lock:
            if self.socket is socket:
                self.socket = None

    def cache_tool_result(self, call_id: str, output: str) -> None:
        if not call_id:
            return
        self.tool_results[call_id] = output
        while len(self.tool_results) > _MAX_CACHED_TOOL_RESULTS:
            oldest = next(iter(self.tool_results))
            self.tool_results.pop(oldest, None)

    async def close(self) -> None:
        await self.bridge.close()
        await self.hermes.close()


class MobileHub:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sessions: dict[str, MobileAgentSession] = {}
        self.lock = asyncio.Lock()

    async def get(self, client_id: str) -> MobileAgentSession:
        key = _safe_client_id(client_id)
        async with self.lock:
            existing = self.sessions.get(key)
            if existing is not None:
                return existing

            transcript_session = f"{self.settings.hermes_session_id}-mobile-{key}"
            hermes = HermesClient(
                self.settings.hermes_base_url,
                self.settings.hermes_api_key,
                transcript_session,
                self.settings.hermes_session_key,
                self.settings.hermes_request_timeout_seconds,
                self.settings.hermes_poll_interval_seconds,
                self.settings.hermes_session_rollover_runs,
            )
            bridge = HermesBridge(hermes, self.settings.hermes_inline_wait_seconds)
            session = MobileAgentSession(key, hermes, bridge)

            async def announce(text: str) -> None:
                await session.push({"type": "background_result", "text": text})

            bridge.set_announcement_callback(announce)
            self.sessions[key] = session
            return session

    async def close(self) -> None:
        async with self.lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        await asyncio.gather(*(s.close() for s in sessions), return_exceptions=True)


def _authorized(expected: str, provided: str | None) -> bool:
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    if not settings.mobile_gateway_token:
        raise RuntimeError(
            "MOBILE_GATEWAY_TOKEN must be set before starting the mobile gateway"
        )

    app = FastAPI(title="Hermes Voice Mobile Gateway", version="0.1.0")
    hub = MobileHub(settings)
    app.state.settings = settings
    app.state.hub = hub

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "hermes-voice-mobile"}

    @app.post("/v1/mobile/aoq-token")
    async def aoq_token(authorization: str | None = Header(default=None)):
        expected = f"Bearer {settings.mobile_gateway_token}"
        if not _authorized(expected, authorization):
            raise HTTPException(status_code=401, detail="unauthorized")
        try:
            credentials = await fetch_aoq_credentials(settings)
        except httpx.HTTPStatusError as exc:
            log.warning("MOBILE AOQ token request failed status=%s", exc.response.status_code)
            raise HTTPException(status_code=502, detail="Bailian token request failed") from exc
        except Exception as exc:
            log.exception("MOBILE AOQ token request failed")
            raise HTTPException(status_code=502, detail="Bailian token response invalid") from exc
        return JSONResponse(credentials)

    @app.websocket("/v1/mobile/control")
    async def control(socket: WebSocket) -> None:
        await socket.accept()
        session: MobileAgentSession | None = None
        try:
            first = await asyncio.wait_for(socket.receive_json(), timeout=10.0)
            token = str(first.get("token") or "")
            client_id = str(first.get("client_id") or "mobile")
            if first.get("type") != "auth" or not _authorized(settings.mobile_gateway_token, token):
                await socket.close(code=4401)
                return

            session = await hub.get(client_id)
            await session.attach(socket)
            await session.push({
                "type": "ready",
                "client_id": session.client_id,
                "instructions": VOICE_SHELL_INSTRUCTIONS,
                "tools": HermesBridge.tool_schemas(),
                "model": settings.qwen_realtime_model,
                "voice": settings.qwen_voice,
                "turn_detection": settings.qwen_turn_detection,
            })
            log.info("MOBILE control connected client_id=%s", session.client_id)

            while True:
                message = await socket.receive_json()
                kind = str(message.get("type") or "")

                if kind == "ping":
                    await session.push({"type": "pong"})
                    continue

                if kind != "tool_call":
                    await session.push({
                        "type": "error",
                        "code": "unsupported_message",
                        "message": "expected tool_call or ping",
                    })
                    continue

                call_id = str(message.get("call_id") or "")
                name = str(message.get("name") or "")
                arguments = message.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = {}

                cached = session.tool_results.get(call_id) if call_id else None
                if cached is not None:
                    await session.push({
                        "type": "tool_result",
                        "call_id": call_id,
                        "name": name,
                        "output": cached,
                        "replayed": True,
                    })
                    continue

                try:
                    output = await session.bridge.dispatch(
                        name, arguments, request_id=call_id or None
                    )
                except Exception as exc:
                    log.exception("MOBILE tool dispatch failed name=%s call_id=%s", name, call_id)
                    output = json.dumps(
                        {"status": "error", "error": str(exc)}, ensure_ascii=False
                    )

                session.cache_tool_result(call_id, output)
                await session.push({
                    "type": "tool_result",
                    "call_id": call_id,
                    "name": name,
                    "output": output,
                    "replayed": False,
                })

        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        except Exception:
            log.exception("MOBILE control connection failed")
        finally:
            if session is not None:
                await session.detach(socket)
                log.info("MOBILE control disconnected client_id=%s", session.client_id)

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await hub.close()

    return app


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.mobile_gateway_host,
        port=settings.mobile_gateway_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
