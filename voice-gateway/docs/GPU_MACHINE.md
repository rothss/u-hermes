# Windows deployment (ZhuanZ voice host)

The MVP uses **Alibaba Cloud Bailian, cn-beijing** for Qwen-Audio-3.1-Realtime-Plus. No local GPU is required for the speech layer.

The Windows PC closest to the microphone/speaker should host the Voice Gateway. Hermes can be local or remote over LAN/Tailscale.

## 1. Hermes API server

On the Hermes machine:

```dotenv
API_SERVER_ENABLED=true
API_SERVER_KEY=replace-with-a-long-random-secret
```

Verify the reachable endpoint from ZhuanZ:

```powershell
curl http://<HERMES_HOST>:8642/health
```

Expected: `{"status":"ok"}`.

## 2. Voice gateway on ZhuanZ

```powershell
cd D:\HermesVoiceGateway\voice-gateway
powershell -ExecutionPolicy Bypass -File .\scripts\windows\bootstrap.ps1
notepad .env
```

Set at minimum:

```dotenv
DASHSCOPE_API_KEY=...
QWEN_REALTIME_MODEL=qwen-audio-3.1-realtime-plus
QWEN_REALTIME_URL=wss://<WORKSPACE_ID>.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime

HERMES_API_KEY=...
HERMES_BASE_URL=http://<HERMES_HOST>:8642
HERMES_SESSION_ID=voice-main
HERMES_SESSION_KEY=agent:main:voice:primary

AUDIO_CHUNK_MS=40
AUDIO_MODE=safe
```

Use `AUDIO_MODE=headset` when a working headset is connected and you want true barge-in/full duplex.

Check Hermes capabilities:

```powershell
.\scripts\windows\check.ps1
```

Run:

```powershell
.\scripts\windows\run.ps1
```

## 3. Optional local Hermes model later

The Voice Gateway does not care which LLM Hermes uses. Hermes can later point at a local OpenAI-compatible model server without changing the speech gateway.
