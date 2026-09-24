# HarmonyOS Mobile Client — P0 Implementation Plan

## Goal

Make a HarmonyOS phone the primary full-duplex voice terminal while keeping Hermes as the only agent brain.

The phone does **not** receive the long-lived Bailian API key and does **not** send microphone PCM through ZhuanZ.

```text
HarmonyOS phone
  ├─ Audio/Data ───────────> Alibaba Cloud Bailian AOQ
  │                           qwen-audio-3.1-realtime-plus
  │                           full duplex / smart_turn / AEC
  │
  └─ Control WebSocket ────> ZhuanZ Mobile Gateway :8787
                              ├─ one-time AOQ token broker
                              └─ Hermes tool bridge
                                      │
                                      v
                                   Hermes
```

## Why AOQ first

Alibaba Cloud's Qwen Audio realtime guide recommends AOQ for client-side use when stable latency, weak-network handling, full duplex, noise suppression and echo cancellation matter.

HarmonyOS is supported by the AOQ client SDK (API 12, arm64-v8a). Keep the long-lived Bailian API key on the AppServer and request a fresh one-time AOQ connection credential before every connection.

Do not hard-code DASHSCOPE_API_KEY in the HarmonyOS project.

Official guide:
https://help.aliyun.com/zh/model-studio/real-time-voice-conversation-using-aoq-access-qwen-audio-3-0-realtime-plus

## ZhuanZ AppServer

The Python package now exposes:

```text
hermes-voice-mobile
```

Start it with:

```powershell
.\scripts\windows\run-mobile-server.ps1
```

Default listener:

```text
0.0.0.0:8787
```

Required local `.env`:

```dotenv
MOBILE_GATEWAY_HOST=0.0.0.0
MOBILE_GATEWAY_PORT=8787
MOBILE_GATEWAY_TOKEN=<random device secret>
QWEN_AOQ_TOKEN_URL=

DASHSCOPE_API_KEY=<Bailian cn-beijing key>
QWEN_REALTIME_MODEL=qwen-audio-3.1-realtime-plus
QWEN_REALTIME_URL=wss://<workspace>.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime

HERMES_BASE_URL=http://<hermes-host>:8642
HERMES_API_KEY=<hermes key>
HERMES_SESSION_KEY=agent:main:voice:primary
```

Generate a mobile gateway secret locally:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Do not commit it.

## AppServer API

### Health

```http
GET /health
```

### One-time AOQ token

```http
POST /v1/mobile/aoq-token
Authorization: Bearer <MOBILE_GATEWAY_TOKEN>
```

The server uses DASHSCOPE_API_KEY to call Bailian's realtime token endpoint and returns only:

```json
{
  "aoqTokenForClient": "...",
  "sid": "...",
  "clientRelayCertFingerprint": "...",
  "clientRelayEndpoints": [],
  "workspaceIdHash": "..."
}
```

The token is one-time use. Fetch another token for every new AOQ connection.

### Hermes control channel

```text
WS /v1/mobile/control
```

After WebSocket connect, the first frame MUST be:

```json
{
  "type": "auth",
  "token": "<MOBILE_GATEWAY_TOKEN>",
  "client_id": "huawei-phone"
}
```

Server returns:

```json
{
  "type": "ready",
  "client_id": "huawei-phone",
  "instructions": "...",
  "tools": [],
  "model": "qwen-audio-3.1-realtime-plus",
  "voice": "longanqian_v3.1",
  "turn_detection": "smart_turn"
}
```

Use exactly the returned `instructions` and `tools` in Qwen `session.update`.

This keeps the phone and server tool schemas synchronized.

## HarmonyOS connection state machine

### 1. Permissions

Declare and request:

```text
ohos.permission.INTERNET
ohos.permission.MICROPHONE
```

### 2. Connect control channel first

Connect to ZhuanZ and send the auth frame.

Store the returned:
- instructions
- tools
- model
- voice
- turn_detection

Do not start microphone transmission yet.

### 3. Request one-time AOQ credentials

Call:

```text
POST https://<gateway>/v1/mobile/aoq-token
```

Use the returned fields to populate the AOQ connection config.

### 4. Configure AOQ audio

Use:
- upstream: 16 kHz, mono
- downstream: 24 kHz, mono
- VoIP mode: enabled
- full-duplex capture/playback
- AEC/noise suppression according to the official SDK sample

Prefer Opus on the AOQ transport where supported.

### 5. Connect AOQ with Audio + Data tracks

Publish:
- Audio
- Data

Subscribe:
- Audio
- Data

Initially keep Audio sending disabled.

### 6. Send Qwen session.update

The Data track sends the same Realtime event contract used by the desktop gateway:

```json
{
  "type": "session.update",
  "session": {
    "modalities": ["text", "audio"],
    "voice": "<ready.voice>",
    "instructions": "<ready.instructions>",
    "tools": "<ready.tools>",
    "turn_detection": {
      "type": "<ready.turn_detection>"
    },
    "enable_search": false
  }
}
```

### 7. Only enable microphone upload after session.updated

This is mandatory.

Do not publish user audio before Qwen confirms `session.updated`.

### 8. Forward Function Calls to ZhuanZ

When AOQ Data receives:

```text
response.function_call_arguments.done
```

send this over the control WebSocket:

```json
{
  "type": "tool_call",
  "call_id": "<Qwen call_id>",
  "name": "hermes_start",
  "arguments": {
    "request": "..."
  }
}
```

The same protocol works for:
- hermes_start
- hermes_steer
- hermes_stop
- hermes_approval

The AppServer deduplicates a repeated call_id and Hermes run creation is additionally protected by Idempotency-Key.

### 9. Return Hermes result to Qwen

Server replies:

```json
{
  "type": "tool_result",
  "call_id": "...",
  "name": "hermes_start",
  "output": "{...}",
  "replayed": false
}
```

Send the output to Qwen through the AOQ Data track:

```json
{
  "type": "conversation.item.create",
  "item": {
    "type": "function_call_output",
    "call_id": "<same call_id>",
    "output": "<server output>"
  }
}
```

Then:

```json
{
  "type": "response.create",
  "response": {
    "modalities": ["audio", "text"]
  }
}
```

### 10. Background Hermes completion

Long Hermes runs may finish after the initial Qwen response.

The control channel sends:

```json
{
  "type": "background_result",
  "text": "..."
}
```

The phone MUST inject a new matched synthetic call/result pair into the Qwen conversation before `response.create`.

Generate a local unique call id:

```text
background_<uuid>
```

First Data event:

```json
{
  "type": "conversation.item.create",
  "item": {
    "type": "function_call",
    "call_id": "background_<uuid>",
    "name": "hermes_start",
    "arguments": "{\"request\":\"Deliver the completed background task result.\"}"
  }
}
```

Then matching `function_call_output` containing the background result, followed by `response.create`.

Do not append the result as a plain system message; the desktop regression already showed Qwen may otherwise repeat the previous “working” response.

## Approval flow

If Hermes reaches a protected action, the tool result has:

```text
status=waiting_for_approval
```

Qwen must ask the user explicitly.

Only these spoken decisions are exposed:
- approve once -> `hermes_approval(choice=once)`
- deny -> `hermes_approval(choice=deny)`

Do not expose voice commands for:
- session approval
- permanent approval
- always approve

## Reconnection

### AOQ reconnect
Every new AOQ connection requires a fresh token from `/v1/mobile/aoq-token`.

Never cache/reuse an old AOQ token.

### Control WebSocket reconnect
Reconnect using the same `client_id`.

The ZhuanZ Mobile Gateway keeps the Hermes session object alive while the phone control channel is disconnected. Background completion events are queued and flushed on reconnect.

Tool results are cached by Qwen `call_id`, so a duplicated mobile frame is replayed without running the same Hermes tool twice.

## First P0 acceptance test

Use the phone and ZhuanZ on the same Wi-Fi.

1. ZhuanZ:
   - pull latest feature branch
   - run pytest
   - run `run-mobile-server.ps1`
2. Phone:
   - connect control WS
   - fetch AOQ token
   - connect AOQ
   - receive `session.updated`
   - start microphone
3. Say:
   - “你好，告诉我你连接的是什么 Agent。”
   - confirm Qwen calls `hermes_start`
4. Start a long task.
5. Say a correction -> `hermes_steer`.
6. Say stop -> `hermes_stop`.
7. Trigger an approval gate:
   - “批准这一次” resumes
   - “拒绝” blocks
8. While Qwen is speaking, interrupt it from the phone speaker:
   - microphone remains active
   - AEC prevents self-feedback
   - the previous answer stops
   - new utterance is processed
9. Turn Wi-Fi off/on:
   - AOQ gets a new one-time token
   - control WS reconnects with same client_id
   - no Hermes side effect is duplicated

## P1 after LAN success

Only after the LAN Golden Path is green, expose the Mobile Gateway through HTTPS.

Preferred options:
1. private VPN/Tailscale if available on the actual HarmonyOS device;
2. otherwise Cloudflare Tunnel + HTTPS + device token.

Do not expose raw Hermes port 8642 to the public Internet.

The mobile phone should only see the narrow Mobile Gateway API.
