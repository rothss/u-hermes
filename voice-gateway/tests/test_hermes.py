import asyncio
from unittest.mock import AsyncMock

import httpx

from hermes_voice_gateway.hermes import HermesClient


def test_voice_session_rolls_over_after_configured_run_count():
    client = HermesClient(
        "http://127.0.0.1:8642",
        "test-key",
        "voice-main",
        "voice-key",
        session_rollover_runs=2,
    )
    try:
        assert client._session_id_for_next_run() == "voice-main"
        assert client._session_id_for_next_run() == "voice-main"
        assert client._session_id_for_next_run() == "voice-main-1"
        assert client._session_id_for_next_run() == "voice-main-1"
        assert client._session_id_for_next_run() == "voice-main-2"
    finally:
        asyncio.run(client.close())


def test_start_run_sends_matching_session_and_idempotency_headers():
    client = HermesClient(
        "http://127.0.0.1:8642",
        "test-key",
        "voice-main",
        "voice-key",
        session_rollover_runs=2,
    )
    response = httpx.Response(
        202,
        json={"run_id": "run-1", "status": "started"},
        request=httpx.Request("POST", "http://127.0.0.1:8642/v1/runs"),
    )
    client._http.post = AsyncMock(return_value=response)
    try:
        run = asyncio.run(client.start_run("hello", idempotency_key="qwen-call-1"))
        assert run.run_id == "run-1"
        kwargs = client._http.post.await_args.kwargs
        assert kwargs["json"]["session_id"] == "voice-main"
        assert kwargs["headers"]["X-Hermes-Session-Id"] == "voice-main"
        assert kwargs["headers"]["Idempotency-Key"] == "qwen-call-1"
        assert client._http.headers["X-Hermes-Session-Key"] == "voice-key"
        assert "X-Hermes-Session-Id" not in client._http.headers
    finally:
        asyncio.run(client.close())
