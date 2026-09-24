from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
RETURNABLE_STATUSES = TERMINAL_STATUSES | {"waiting_for_approval"}


@dataclass(slots=True)
class HermesRun:
    run_id: str
    status: str
    output: str | None = None
    error: str | None = None
    raw: dict[str, Any] | None = None


class HermesClient:
    def __init__(self, base_url: str, api_key: str, session_id: str, session_key: str,
                 request_timeout: float = 15.0, poll_interval: float = 0.25,
                 session_rollover_runs: int = 4) -> None:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if session_key:
            headers["X-Hermes-Session-Key"] = session_key
        self.session_id = session_id
        self.poll_interval = poll_interval
        self.session_rollover_runs = max(0, session_rollover_runs)
        self._session_generation = 0
        self._runs_in_session = 0
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=httpx.Timeout(request_timeout)
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def health(self) -> dict[str, Any]:
        r = await self._http.get("/health")
        r.raise_for_status()
        return r.json()

    async def capabilities(self) -> dict[str, Any]:
        r = await self._http.get("/v1/capabilities")
        r.raise_for_status()
        return r.json()

    async def start_run(self, text: str, idempotency_key: str | None = None) -> HermesRun:
        started = time.perf_counter()
        run_session_id = self._session_id_for_next_run()
        payload: dict[str, Any] = {"input": text}
        request_headers: dict[str, str] = {
            "Idempotency-Key": idempotency_key or f"voice-{uuid.uuid4().hex}"
        }
        if run_session_id:
            payload["session_id"] = run_session_id
            # Keep Hermes transcript continuity explicit and consistent with the
            # dynamic rollover session in the JSON body.
            request_headers["X-Hermes-Session-Id"] = run_session_id

        # A POST can fail after Hermes accepted it but before the client receives
        # the 202. Retrying once with the SAME Idempotency-Key is safe on Hermes
        # v0.21.3+ and prevents duplicate email/SSH/file side effects.
        for attempt in range(2):
            try:
                r = await self._http.post("/v1/runs", json=payload, headers=request_headers)
                break
            except httpx.TransportError:
                if attempt:
                    raise
                log.warning("HERMES run submit transport error; retrying idempotently")
                await asyncio.sleep(0.2)
        r.raise_for_status()
        data = r.json()
        log.info("HERMES run submitted run_id=%s session=%s replayed=%s post_ms=%.1f",
                 data.get("run_id", ""), run_session_id or "<run>",
                 r.headers.get("Idempotency-Replayed", "false"),
                 (time.perf_counter() - started) * 1000)
        return HermesRun(run_id=data["run_id"], status=data.get("status", "started"), raw=data)

    def _session_id_for_next_run(self) -> str:
        """Return a bounded-history voice session without changing the auth session key."""
        if not self.session_id:
            return ""
        if self.session_rollover_runs and self._runs_in_session >= self.session_rollover_runs:
            self._session_generation += 1
            self._runs_in_session = 0
        self._runs_in_session += 1
        suffix = f"-{self._session_generation}" if self._session_generation else ""
        return f"{self.session_id}{suffix}"

    async def get_run(self, run_id: str) -> HermesRun:
        r = await self._http.get(f"/v1/runs/{run_id}")
        r.raise_for_status()
        data = r.json()
        output = data.get("output")
        if output is not None and not isinstance(output, str):
            output = str(output)
        error = data.get("error")
        if error is not None and not isinstance(error, str):
            error = str(error)
        return HermesRun(run_id=run_id, status=data.get("status", "unknown"),
                         output=output, error=error, raw=data)

    async def wait_for_terminal(self, run_id: str, timeout: float | None = None) -> HermesRun:
        async def _wait() -> HermesRun:
            # A short inline wait is intentionally polling-only.  Cancelling an SSE
            # request here can make Hermes drop the run's single transport queue before
            # the background monitor subscribes, which loses the terminal event.
            if timeout is not None:
                return await self._wait_by_polling(run_id)
            try:
                return await self._wait_for_events(run_id)
            except Exception as exc:
                log.warning("HERMES SSE unavailable run_id=%s error=%s; using backoff polling",
                            run_id, type(exc).__name__)
                return await self._wait_by_polling(run_id)
        if timeout is None:
            return await _wait()
        return await asyncio.wait_for(_wait(), timeout=timeout)

    async def _wait_for_events(self, run_id: str) -> HermesRun:
        """Wait for a terminal run event; the final GET retrieves the full output/usage."""
        terminal_events = {"run.completed", "run.failed", "run.cancelled", "run.interrupted"}
        async with self._http.stream("GET", f"/v1/runs/{run_id}/events", timeout=None) as response:
            response.raise_for_status()
            log.debug("HERMES SSE connected run_id=%s", run_id)
            lines = response.aiter_lines()
            event_name = ""
            data_lines: list[str] = []
            while True:
                try:
                    # Keep the stream open, but use a bounded status check as a safety
                    # net if a proxy or server stops emitting frames.
                    line = await asyncio.wait_for(anext(lines), timeout=5.0)
                except asyncio.TimeoutError:
                    current = await self.get_run(run_id)
                    if current.status in RETURNABLE_STATUSES:
                        log.info("HERMES SSE fallback state run_id=%s status=%s", run_id, current.status)
                        return current
                    continue
                except StopAsyncIteration:
                    break

                if line == "":
                    if data_lines:
                        event = json.loads("\n".join(data_lines))
                        name = str(event.get("event", "") or event_name)
                        log.debug("HERMES SSE event run_id=%s name=%s", run_id, name)
                        if name == "approval.request":
                            log.info("HERMES approval requested run_id=%s", run_id)
                            return HermesRun(
                                run_id=run_id,
                                status="waiting_for_approval",
                                raw={"approval": event},
                            )
                        if name in terminal_events:
                            log.info("HERMES SSE terminal run_id=%s name=%s", run_id, name)
                            return await self.get_run(run_id)
                    event_name = ""
                    data_lines = []
                elif line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    raw = line[5:].lstrip()
                    if raw:
                        data_lines.append(raw)
        raise RuntimeError("Hermes SSE stream closed before terminal event")

    async def _wait_by_polling(self, run_id: str) -> HermesRun:
        delay = max(0.1, self.poll_interval)
        while True:
            run = await self.get_run(run_id)
            if run.status in RETURNABLE_STATUSES:
                return run
            await asyncio.sleep(delay)
            delay = min(delay * 2, 2.0)

    async def approve(self, run_id: str, choice: str,
                      request_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"choice": choice}
        if request_id:
            payload["request_id"] = request_id
        r = await self._http.post(f"/v1/runs/{run_id}/approval", json=payload)
        r.raise_for_status()
        return r.json()

    async def steer(self, run_id: str, text: str) -> dict[str, Any]:
        r = await self._http.post(f"/v1/runs/{run_id}/steer", json={"input": text})
        r.raise_for_status()
        return r.json()

    async def stop(self, run_id: str) -> dict[str, Any]:
        r = await self._http.post(f"/v1/runs/{run_id}/stop", json={})
        r.raise_for_status()
        return r.json()
