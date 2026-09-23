from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


@dataclass(slots=True)
class HermesRun:
    run_id: str
    status: str
    output: str | None = None
    error: str | None = None
    raw: dict[str, Any] | None = None


class HermesClient:
    def __init__(self, base_url: str, api_key: str, session_id: str, session_key: str,
                 request_timeout: float = 15.0, poll_interval: float = 0.25) -> None:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if session_id:
            headers["X-Hermes-Session-Id"] = session_id
        if session_key:
            headers["X-Hermes-Session-Key"] = session_key
        self.session_id = session_id
        self.poll_interval = poll_interval
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

    async def start_run(self, text: str) -> HermesRun:
        payload: dict[str, Any] = {"input": text}
        if self.session_id:
            payload["session_id"] = self.session_id
        r = await self._http.post("/v1/runs", json=payload)
        r.raise_for_status()
        data = r.json()
        return HermesRun(run_id=data["run_id"], status=data.get("status", "started"), raw=data)

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
            while True:
                run = await self.get_run(run_id)
                if run.status in TERMINAL_STATUSES:
                    return run
                await asyncio.sleep(self.poll_interval)
        if timeout is None:
            return await _wait()
        return await asyncio.wait_for(_wait(), timeout=timeout)

    async def steer(self, run_id: str, text: str) -> dict[str, Any]:
        r = await self._http.post(f"/v1/runs/{run_id}/steer", json={"input": text})
        r.raise_for_status()
        return r.json()

    async def stop(self, run_id: str) -> dict[str, Any]:
        r = await self._http.post(f"/v1/runs/{run_id}/stop", json={})
        r.raise_for_status()
        return r.json()
