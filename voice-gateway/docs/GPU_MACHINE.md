# Windows GPU-machine deployment

The MVP uses QwenCloud for the speech-to-speech model. The GPU machine hosts Hermes and the gateway.

## 1. Hermes API server

In `~/.hermes/.env`:

```dotenv
API_SERVER_ENABLED=true
API_SERVER_KEY=replace-with-a-long-random-secret
```

Start Hermes, then verify:

```powershell
curl http://127.0.0.1:8642/health
```

Expected: `{"status":"ok"}`.

## 2. Voice gateway

From the u-hermes checkout:

```powershell
cd voice-gateway
powershell -ExecutionPolicy Bypass -File .\scripts\windows\bootstrap.ps1
notepad .env
```

Set at minimum:

```dotenv
DASHSCOPE_API_KEY=...
HERMES_API_KEY=...
HERMES_BASE_URL=http://127.0.0.1:8642
AUDIO_MODE=headset
```

Check Hermes capabilities:

```powershell
.\scripts\windows\check.ps1
```

Run:

```powershell
.\scripts\windows\run.ps1
```

## 3. Optional local Hermes model later

The voice gateway does not care which LLM Hermes uses. Hermes can later point at a local OpenAI-compatible model server (vLLM/llama.cpp/etc.) without changing the voice code.
