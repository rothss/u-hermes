# Hermes Voice Gateway

Full-duplex voice shell for **Hermes Agent**, using **Qwen-Audio-3.1-Realtime-Plus** for real-time listening/speaking while keeping **Hermes as the sole reasoning + tool-use brain**.

## What v0.1 does

- streams 16 kHz / 16-bit / mono PCM to Qwen Realtime in 40 ms frames;
- plays Qwen's 24 kHz streaming speech immediately;
- waits for `session.updated` before sending the first microphone frame;
- clears queued speech on user barge-in;
- forces substantive turns through the Hermes tool contract;
- calls Hermes `/v1/runs` with bounded transcript sessions and a stable long-term memory key;
- uses `Idempotency-Key` so a transport retry cannot duplicate a Hermes run;
- waits inline for quick Hermes answers, then monitors long runs by SSE with polling fallback;
- maps spoken corrections to Hermes `/steer` and spoken cancellation to `/stop`;
- asks for explicit **approve once / deny** when Hermes pauses at a safety approval gate;
- announces background results automatically and retains them across Qwen reconnect/race failures.

## Deployment choice

**API-first audio**: Qwen-Audio-3.1-Realtime-Plus runs on **Alibaba Cloud Model Studio / Bailian, China (cn-beijing)**. The Voice Gateway runs on the Windows PC that owns the microphone/speaker (for this deployment: ZhuanZ). Hermes may run locally or on another machine reachable over LAN/Tailscale.

A local speech-model backend can be added later without changing the Hermes tool contract.

## Prerequisites

- Windows 10/11
- Python 3.11+
- a working Hermes Agent API server, normally port `8642`
- an Alibaba Cloud Bailian Beijing-region API key with access to `qwen-audio-3.1-realtime-plus`
- preferably a headset/headphones for true desktop full-duplex testing

## Quick start

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\bootstrap.ps1
notepad .env
.\scripts\windows\check.ps1
.\scripts\windows\run.ps1
```

Minimum `.env` values:

```dotenv
DASHSCOPE_API_KEY=your-bailian-key
QWEN_REALTIME_MODEL=qwen-audio-3.1-realtime-plus
QWEN_REALTIME_URL=wss://YOUR_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime

HERMES_API_KEY=your-hermes-api-key
HERMES_BASE_URL=http://127.0.0.1:8642

AUDIO_CHUNK_MS=40
AUDIO_MODE=headset
```

If Hermes is on another machine, set `HERMES_BASE_URL` to its LAN/Tailscale address.

## Hermes setup

Enable the Hermes API server in `~/.hermes/.env`:

```dotenv
API_SERVER_ENABLED=true
API_SERVER_KEY=replace-with-a-long-random-secret
```

The gateway self-check requires run submission/status, steer, stop, and run approval support.

## Voice behavior

The Qwen system instruction treats Hermes as the only brain. Normal questions/tasks must call `hermes_start`. During a running task:

- “改成检查双卡那台机器” -> `hermes_steer`
- “算了，停掉” -> `hermes_stop`
- when Hermes requests approval, the gateway asks the user to explicitly “批准这一次” or “拒绝”; voice never grants session/permanent approval

Pure voice UI commands such as “说慢一点” can be handled by the speech shell without involving Hermes.

## Audio modes

`AUDIO_MODE=headset` keeps the microphone live while Qwen speaks and supports barge-in. Use headphones/headset to avoid acoustic feedback.

`AUDIO_MODE=safe` suppresses microphone upload while output is playing. It is useful with desktop speakers but **is not true full duplex**.

List devices:

```powershell
.\.venv\Scripts\hermes-voice.exe --list-devices
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

See `docs/ARCHITECTURE.md`, `docs/GPU_MACHINE.md`, `LATENCY_ANALYSIS.md`, and `DEPLOYMENT_REPORT.md`.
