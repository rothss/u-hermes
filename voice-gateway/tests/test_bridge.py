from __future__ import annotations

import asyncio
import json

import pytest

from hermes_voice_gateway.bridge import HermesBridge
from hermes_voice_gateway.hermes import HermesRun


class FakeHermes:
    def __init__(self, final: HermesRun, delay: float = 0.0):
        self.final = final
        self.delay = delay
        self.started: list[str] = []
        self.steered: list[tuple[str, str]] = []
        self.stopped: list[str] = []

    async def start_run(self, text: str) -> HermesRun:
        self.started.append(text)
        return HermesRun(run_id=self.final.run_id, status="started")

    async def wait_for_terminal(self, run_id: str, timeout: float | None = None) -> HermesRun:
        if self.delay:
            if timeout is not None and self.delay > timeout:
                await asyncio.sleep(timeout)
                raise TimeoutError
            await asyncio.sleep(self.delay)
        return self.final

    async def steer(self, run_id: str, text: str):
        self.steered.append((run_id, text))
        return {"status": "accepted"}

    async def stop(self, run_id: str):
        self.stopped.append(run_id)
        return {"status": "stopping"}


@pytest.mark.asyncio
async def test_inline_completed_run():
    fake = FakeHermes(HermesRun("run-1", "completed", output="42"))
    bridge = HermesBridge(fake, inline_wait_seconds=0.1)
    result = json.loads(await bridge.start("question"))
    assert result["status"] == "completed"
    assert result["result"] == "42"
    assert bridge.active_run_id is None


@pytest.mark.asyncio
async def test_background_run_announces():
    fake = FakeHermes(HermesRun("run-2", "completed", output="done"), delay=0.04)
    bridge = HermesBridge(fake, inline_wait_seconds=0.01)
    announced: list[str] = []
    async def cb(text: str):
        announced.append(text)
    bridge.set_announcement_callback(cb)
    result = json.loads(await bridge.start("slow"))
    assert result["status"] == "running"
    await asyncio.sleep(0.08)
    assert announced and "done" in announced[0]
    await bridge.close()


@pytest.mark.asyncio
async def test_steer_and_stop_use_active_run():
    fake = FakeHermes(HermesRun("run-3", "completed", output="done"), delay=10)
    bridge = HermesBridge(fake, inline_wait_seconds=0.001)
    result = json.loads(await bridge.start("long task"))
    assert result["status"] == "running"
    steer = json.loads(await bridge.steer("switch machine"))
    assert steer["status"] == "steer_queued"
    stop = json.loads(await bridge.stop())
    assert stop["status"] == "stopping"
    assert fake.steered == [("run-3", "switch machine")]
    assert fake.stopped == ["run-3"]
    await bridge.close()
