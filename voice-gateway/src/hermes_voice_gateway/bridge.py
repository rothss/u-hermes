from __future__ import annotations

import asyncio
import json
import logging
import time
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
        self._pending_approvals: dict[str, dict[str, Any]] = {}
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
            {"type": "function", "function": {
                "name": "hermes_approval",
                "description": "Resolve a Hermes safety approval ONLY after the user explicitly approves or denies the pending action. Never infer approval from silence, vague agreement, or unrelated speech.",
                "parameters": {"type": "object", "properties": {
                    "choice": {
                        "type": "string",
                        "enum": ["once", "deny"],
                        "description": "Use once only for explicit one-time approval; use deny for explicit rejection."
                    }
                }, "required": ["choice"]}
            }},
        ]

    async def dispatch(self, name: str, arguments: dict[str, Any],
                       request_id: str | None = None) -> str:
        if name == "hermes_start":
            return await self.start(str(arguments.get("request", "")).strip(), request_id=request_id)
        if name == "hermes_steer":
            return await self.steer(str(arguments.get("instruction", "")).strip())
        if name == "hermes_stop":
            return await self.stop()
        if name == "hermes_approval":
            return await self.approval(str(arguments.get("choice", "")).strip())
        return json.dumps({"status": "error", "error": f"unknown tool: {name}"}, ensure_ascii=False)

    async def start(self, request: str, request_id: str | None = None) -> str:
        if not request:
            return json.dumps({"status": "error", "error": "empty request"}, ensure_ascii=False)
        started = time.perf_counter()
        idempotency_key = f"qwen-{request_id}" if request_id else None
        run = await self.hermes.start_run(request, idempotency_key=idempotency_key)
        async with self._lock:
            self._runs[run.run_id] = run
            self._active_run_id = run.run_id
        try:
            terminal = await self.hermes.wait_for_terminal(run.run_id, timeout=self.inline_wait_seconds)
        except TimeoutError:
            task = asyncio.create_task(self._monitor(run.run_id), name=f"hermes-monitor-{run.run_id}")
            self._monitor_tasks[run.run_id] = task
            log.info("HERMES run running run_id=%s elapsed_ms=%.1f", run.run_id,
                     (time.perf_counter() - started) * 1000)
            return json.dumps({
                "status": "running",
                "run_id": run.run_id,
                "instruction": "Acknowledge briefly that Hermes is working. Do not invent or summarize a result yet. The completed result will be delivered automatically."
            }, ensure_ascii=False)
        if terminal.status == "waiting_for_approval":
            self._remember_approval(terminal)
            log.info("HERMES run waiting approval run_id=%s elapsed_ms=%.1f",
                     run.run_id, (time.perf_counter() - started) * 1000)
            return self._approval_tool_output(terminal)
        await self._record_terminal(terminal)
        log.info("HERMES run terminal run_id=%s status=%s elapsed_ms=%.1f",
                 run.run_id, terminal.status, (time.perf_counter() - started) * 1000)
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

    async def approval(self, choice: str) -> str:
        if choice not in {"once", "deny"}:
            return json.dumps({"status": "error", "error": "approval choice must be once or deny"},
                              ensure_ascii=False)
        run_id = self._active_run_id
        if not run_id or run_id not in self._pending_approvals:
            return json.dumps({"status": "no_pending_approval"}, ensure_ascii=False)
        approval = self._pending_approvals[run_id]
        request_id = approval.get("request_id")
        try:
            result = await self.hermes.approve(run_id, choice, request_id=request_id)
        except Exception as exc:
            return json.dumps({"status": "error", "run_id": run_id, "error": str(exc)},
                              ensure_ascii=False)
        self._pending_approvals.pop(run_id, None)
        task = asyncio.create_task(self._monitor(run_id), name=f"hermes-monitor-{run_id}")
        self._monitor_tasks[run_id] = task
        return json.dumps({
            "status": "approval_submitted",
            "run_id": run_id,
            "choice": choice,
            "hermes": result,
            "instruction": "Acknowledge the user's approval decision briefly. Hermes will continue and the final result will be delivered automatically."
        }, ensure_ascii=False)

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
        started = time.perf_counter()
        try:
            terminal = await self.hermes.wait_for_terminal(run_id)
            if terminal.status == "waiting_for_approval":
                self._remember_approval(terminal)
                log.info("HERMES background waiting approval run_id=%s elapsed_ms=%.1f",
                         run_id, (time.perf_counter() - started) * 1000)
                if self._announce:
                    await self._announce(self._approval_announcement(terminal))
                return
            await self._record_terminal(terminal)
            log.info("HERMES background completed run_id=%s status=%s elapsed_ms=%.1f",
                     run_id, terminal.status, (time.perf_counter() - started) * 1000)
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
            self._pending_approvals.pop(run.run_id, None)
            if self._active_run_id == run.run_id:
                self._active_run_id = None

    def _remember_approval(self, run: HermesRun) -> None:
        approval = {}
        if run.raw:
            approval = dict(run.raw.get("approval") or {})
        self._pending_approvals[run.run_id] = approval

    @staticmethod
    def _approval_tool_output(run: HermesRun) -> str:
        approval = dict((run.raw or {}).get("approval") or {})
        description = approval.get("description") or approval.get("prompt") or approval.get("command") or "a protected action"
        return json.dumps({
            "status": "waiting_for_approval",
            "run_id": run.run_id,
            "approval": {
                "description": description,
                "choices": ["once", "deny"],
            },
            "instruction": "Hermes is waiting at a safety gate. Ask the user to explicitly say approve once or deny. Do not approve automatically."
        }, ensure_ascii=False)

    @staticmethod
    def _approval_announcement(run: HermesRun) -> str:
        approval = dict((run.raw or {}).get("approval") or {})
        description = approval.get("description") or approval.get("prompt") or approval.get("command") or "a protected action"
        return (
            "Hermes is paused at a safety approval gate. Explain this protected action to the user: "
            + str(description)
            + ". Ask the user to explicitly approve this action once or deny it. Never infer approval."
        )

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
