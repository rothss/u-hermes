# Hermes Voice Gateway

Full-duplex voice shell for **Hermes Agent**, using **Qwen-Audio-3.1-Realtime-Plus** for real-time listening/speaking while keeping **Hermes as the sole reasoning + tool-use brain**.

## What v0.1 does

- streams 16 kHz microphone PCM to Qwen Realtime over WebSocket;
- plays Qwen's 24 kHz streaming speech immediately;
- clears queued speech on user barge-in;
- forces substantive turns through the Hermes tool contract;
- calls Hermes `/v1/runs` with stable session IDs/keys;
- waits inline for quick Hermes answers;
- leaves long Hermes runs running while the microphone stays live;
- maps spoken corrections to Hermes `/steer` and spoken cancellation to `/stop`;
- announces a background Hermes result automatically when it finishes.

## Deployment choice

**API-first audio**: Qwen-Audio-3.1-Realtime-Plus runs on QwenCloud. Hermes + this gateway run on the Windows GPU machine. Local speech-model deployment can be added behind the same interface later.

## Prerequisites

- Windows 10/11
- Python 3.11+
- a working Hermes Agent API server, normally `http://127.0.0.1:8642`
- a QwenCloud Singapore-region API key with access to `qwen-audio-3.1-realtime-plus`
- headset/headphones for true desktop full-duplex testing

## Quick start

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\bootstrap.ps1
notepad .env
.\scripts\windows\check.ps1
.\scripts\windows\run.ps1
```

Minimum `.env` values:

```dotenv
DASHSCOPE_API_KEY=your-qwen-key
HERMES_API_KEY=your-hermes-api-key
HERMES_BASE_URL=http://127.0.0.1:8642
AUDIO_MODE=headset
```

## Hermes setup

Enable the Hermes API server in `~/.hermes/.env`:

```dotenv
API_SERVER_ENABLED=true
API_SERVER_KEY=replace-with-a-long-random-secret
```

Then make sure `GET http://127.0.0.1:8642/health` returns `{"status":"ok"}`.

## Voice behavior

The Qwen system instruction treats Hermes as the only brain. Normal questions/tasks must call `hermes_start`. If Hermes is still working:

- “改成检查双卡那台机器” -> `hermes_steer`
- “算了，停掉” -> `hermes_stop`

Pure voice UI commands such as “说慢一点” can be handled by the speech shell without involving Hermes.

## Audio devices

```powershell
.\.venv\Scripts\hermes-voice.exe --list-devices
```

Set `AUDIO_INPUT_DEVICE` and `AUDIO_OUTPUT_DEVICE` in `.env` if needed.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

See `docs/ARCHITECTURE.md` and `docs/GPU_MACHINE.md`.
