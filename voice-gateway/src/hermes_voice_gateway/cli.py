from __future__ import annotations

import argparse
import asyncio
import logging

from .audio import PCMCapture, PCMPlayer, list_audio_devices
from .bridge import HermesBridge
from .config import Settings
from .hermes import HermesClient
from .qwen import QwenRealtimeClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Full-duplex Qwen voice shell for Hermes Agent")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--check", action="store_true")
    return parser


async def _check(settings: Settings) -> int:
    hermes = HermesClient(settings.hermes_base_url, settings.hermes_api_key,
                          settings.hermes_session_id, settings.hermes_session_key,
                          settings.hermes_request_timeout_seconds, settings.hermes_poll_interval_seconds,
                          settings.hermes_session_rollover_runs)
    try:
        print("health:", await hermes.health())
        caps = await hermes.capabilities()
        features = caps.get("features", {})
        required = ["run_submission", "run_status", "run_stop", "run_steer", "run_approval_response"]
        print("required features:")
        ok = True
        for name in required:
            value = features.get(name)
            print(f"  {name}: {value}")
            ok = ok and bool(value)
        return 0 if ok else 2
    finally:
        await hermes.close()


async def _run(settings: Settings) -> None:
    hermes = HermesClient(settings.hermes_base_url, settings.hermes_api_key,
                          settings.hermes_session_id, settings.hermes_session_key,
                          settings.hermes_request_timeout_seconds, settings.hermes_poll_interval_seconds,
                          settings.hermes_session_rollover_runs)
    await hermes.health()
    bridge = HermesBridge(hermes, settings.hermes_inline_wait_seconds)
    player = PCMPlayer(settings.audio_output_device)
    capture = PCMCapture(settings.audio_input_device, chunk_ms=settings.audio_chunk_ms)
    qwen = QwenRealtimeClient(
        api_key=settings.dashscope_api_key, base_url=settings.qwen_realtime_url,
        model=settings.qwen_realtime_model, voice=settings.qwen_voice,
        turn_detection=settings.qwen_turn_detection, max_history_turns=settings.qwen_max_history_turns,
        bridge=bridge, player=player, capture=capture, audio_mode=settings.audio_mode)
    try:
        await qwen.run()
    finally:
        await qwen.close()
        await bridge.close()
        capture.close()
        player.close()
        await hermes.close()


def main() -> None:
    args = build_parser().parse_args()
    if args.list_devices:
        list_audio_devices()
        return
    settings = Settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.check:
            raise SystemExit(asyncio.run(_check(settings)))
        asyncio.run(_run(settings))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
