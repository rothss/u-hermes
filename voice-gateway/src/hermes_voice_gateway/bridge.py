from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .hermes import HermesClient, HermesRun

log = logging.getLogger(__name__)
AnnouncementCallback = Callable[[str], Awaitable[None]]


class HermesBridge:
    """Maps Qwen tool calls to Hermes runs and preserves long-running task control."""

    def __init__(self, hermes: HermesClient, inline_wait_seconds: float = 2.0) -> None:
        self.hermes = hermes
        self.inline_wait_seconds = inline_wait_seconds
        self._runs: dict[str, HermesRun] = {}
        self._active_run_id: str | None = None
        self._monitor_tasks: dict[str, asyncio.Task[None]] = {}
        self._announce: AnnouncementCallback | None = None
        self._lock = asyncio.Lock()

    def set_announcement_callback(self, callback: AnnouncementCallback) -> None:
        self._announce = callback

    @property
    def active_run_id(self) -> str | None:
        return self._active_run_id

    @staticmethod
    def tool_schemas() -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {
                "name": "hermes_start",
                "description": "MANDATORY brain tool. Use for every meaningful user request: questions, analysis, memory, planning, coding, search, file/email/server actions, or any substantive chat. Hermes is the sole reasoning and agent brain; never answer those requests yourself.",
                "parameters": {"type": "object", "properties": {
                    "request": {"type": "string", "description": "The user's complete request, preserving important details and references."}
                }, "required": ["request"]}
            }},
            {"type": "function", "function": {
                "name": "hermes_steer",
                "description": "Use only when a Hermes task is already running and the user changes, corrects, narrows, or redirects that in-progress task.",
                "parameters": {"type": "object", "properties": {
                    "instruction": {"type": "string", "description": "New guidance to inject into the currently active Hermes run."}
                }, "required": ["instruction"]}
            }},
            {"type": "function", "function": {
                "name": "hermes_stop",
                "description": "Stop/cancel the currently active Hermes task when the user asks to stop it.",
                "parameters": {"type": "object", "properties": {}}
            }},
        ]

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "hermes_start":
            return await self.start(str(arguments.get("request", "")).strip())
        if name == "hermes_steer":
            return await self.steer(str(arguments.get("instruction", "")).strip())
        if name == "hermes_stop":
            return await self.stop()
        return json.dumps({"status": "error", "error": f"unknown tool: {name}"}, ensure_ascii=False)

    async def start(self, request: str) -> str:
        if not request:
            return json.dumps({"status": "error", "error": "empty request"}, ensure_ascii=False)
        run = await self.hermes.start_run(request)
        async with self._lock:
            self._runs[run.run_id] = run
            self._active_run_id = run.run_id
        try:
            terminal = await self.hermes.wait_for_terminal(run.run_id, timeout=self.inline_wait_seconds)
        except TimeoutError:
            task = asyncio.create_task(self._monitor(run.run_id), name=f"hermes-monitor-{run.run_id}")
            self._monitor_tasks[run.run_id] = task
            return json.dumps({
                "status": "running",
                "run_id": run.run_id,
                "instruction": "Acknowledge briefly that Hermes is working. Do not invent or summarize a result yet. The completed result will be delivered automatically."
            }, ensure_ascii=False)
        await self._record_terminal(terminal)
        return self._terminal_tool_output(terminal)

    async def steer(self, instruction: str) -> str:
        run_id = self._active_run_id
        if not run_id:
            return json.dumps({"status": "no_active_run"}, ensure_ascii=False)
        try:
            result = await self.hermes.steer(run_id, instruction)
        except Exception as exc:
            return json.dumps({"status": "error", "run_id": run_id, "error": str(exc)}, ensure_ascii=False)
        return json.dumps({"status": "steer_queued", "run_id": run_id, "hermes": result}, ensure_ascii=False)

    async def stop(self) -> str:
        run_id = self._active_run_id
        if not run_id:
            return json.dumps({"status": "no_active_run"}, ensure_ascii=False)
        try:
            result = await self.hermes.stop(run_id)
        except Exception as exc:
            return json.dumps({"status": "error", "run_id": run_id, "error": str(exc)}, ensure_ascii=False)
        return json.dumps({"status": "stopping", "run_id": run_id, "hermes": result}, ensure_ascii=False)

    async def _monitor(self, run_id: str) -> None:
        try:
            terminal = await self.hermes.wait_for_terminal(run_id)
            await self._record_terminal(terminal)
            if self._announce:
                await self._announce(self._announcement_text(terminal))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Hermes run monitor failed: %s", run_id)
        finally:
            self._monitor_tasks.pop(run_id, None)

    async def _record_terminal(self, run: HermesRun) -> None:
        async with self._lock:
            self._runs[run.run_id] = run
            if self._active_run_id == run.run_id:
                self._active_run_id = None

    @staticmethod
    def _terminal_tool_output(run: HermesRun) -> str:
        return json.dumps({
            "status": run.status, "run_id": run.run_id, "result": run.output, "error": run.error,
            "instruction": "Speak Hermes's result faithfully. Do not add facts from your own knowledge."
        }, ensure_ascii=False)

    @staticmethod
    def _announcement_text(run: HermesRun) -> str:
        if run.status == "completed":
            return "A Hermes background run has completed. Deliver this result faithfully to the user. Do not add independent facts or analysis.\n\nHermes result:\n" + (run.output or "(no text output)")
        return f"A Hermes background run ended without a normal completion. Tell the user concisely.\n\nstatus={run.status}\nerror={run.error or ''}\noutput={run.output or ''}"

    async def close(self) -> None:
        for task in list(self._monitor_tasks.values()):
            task.cancel()
        if self._monitor_tasks:
            await asyncio.gather(*self._monitor_tasks.values(), return_exceptions=True)
