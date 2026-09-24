# Codex Handoff — HarmonyOS Mobile P0

## Objective

Build and run a HarmonyOS phone client for Hermes Voice Gateway.

Do **not** reimplement the desktop PyAudio path on the phone.

Use Alibaba Cloud Bailian AOQ as the audio transport and the new ZhuanZ Mobile Gateway as the Hermes control plane.

## Source branch

```text
rothss/u-hermes
feature/hermes-voice-gateway
```

Read first:

```text
voice-gateway/docs/MOBILE_HARMONYOS.md
voice-gateway/src/hermes_voice_gateway/mobile_server.py
```

## Non-negotiable architecture

```text
HarmonyOS phone
  ├─ AOQ Audio/Data -> Bailian qwen-audio-3.1-realtime-plus
  └─ Control WS     -> ZhuanZ :8787 -> Hermes
```

Hermes remains the only reasoning/tool-use brain.

The phone must NEVER contain:
- DASHSCOPE_API_KEY
- HERMES_API_KEY

The phone may store only the user-provisioned MOBILE_GATEWAY_TOKEN.

## Phase 1 — ZhuanZ AppServer

Pull the latest branch:

```powershell
cd D:\HermesVoiceGateway
git fetch origin
git checkout feature/hermes-voice-gateway
git pull --ff-only origin feature/hermes-voice-gateway
cd voice-gateway
```

Reinstall editable dependencies because FastAPI/Uvicorn were added:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Generate a local mobile token:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

Add to `.env`:

```dotenv
MOBILE_GATEWAY_HOST=0.0.0.0
MOBILE_GATEWAY_PORT=8787
MOBILE_GATEWAY_TOKEN=<generated token>
QWEN_AOQ_TOKEN_URL=
```

Keep the existing Bailian Beijing and Hermes configuration.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\windows\check.ps1
.\scripts\windows\run-mobile-server.ps1
```

From another device on the LAN confirm:

```text
http://<ZHUANZ_LAN_IP>:8787/health
```

Do not expose port 8642.

Do not expose port 8787 publicly in Phase 1.

## Phase 2 — HarmonyOS project

Use DevEco Studio and the official Alibaba Cloud AOQ HarmonyOS SDK.

Official current AOQ guide supports:
- HarmonyOS API 12
- arm64-v8a
- qwen-audio-3.1-realtime-plus
- Audio + Data tracks
- full-duplex VoIP mode
- AEC/noise suppression path

Download the current SDK/example from Alibaba Cloud official documentation.

IMPORTANT:
- Do not invent AOQ SDK method names.
- Copy/adapt the HarmonyOS interfaces from the downloaded official SDK example.
- Keep the vendor .har/.so binaries out of Git unless their redistribution license explicitly allows committing them.
- Add a README telling the user exactly where to place those local SDK files.

## Phase 3 — Minimal app UI

Create one simple page only.

Fields/settings:
- Gateway URL
- Mobile Gateway Token
- Client ID, default `huawei-phone`

Buttons/status:
- Connect
- Disconnect
- Mute/unmute
- connection state
- Qwen session state
- Hermes task state
- transcript area

Do not spend time on visual polish.

## Phase 4 — Control WebSocket

Connect:

```text
ws://<ZHUANZ_LAN_IP>:8787/v1/mobile/control
```

First frame:

```json
{
  "type": "auth",
  "token": "<mobile token>",
  "client_id": "huawei-phone"
}
```

Wait for `ready`.

Use the server-returned:
- instructions
- tools
- model
- voice
- turn_detection

Do not hard-code a second copy if it can be avoided.

Implement:
- ping/pong
- reconnect with same client_id
- tool_call -> tool_result
- background_result

## Phase 5 — AOQ token

Request a fresh token before EVERY AOQ connect:

```http
POST http://<ZHUANZ_LAN_IP>:8787/v1/mobile/aoq-token
Authorization: Bearer <mobile token>
```

Map:
- aoqTokenForClient
- sid
- clientRelayCertFingerprint
- clientRelayEndpoints
- workspaceIdHash

to the official AOQ connection config.

Never cache/reuse an AOQ token.

## Phase 6 — Audio

Configure official AOQ capture/player for VoIP/full duplex.

Required:
- microphone permission
- Internet permission
- upstream 16k mono
- downstream 24k mono
- AEC enabled through the official VoIP/AEC path
- Audio + Data tracks

Keep the outgoing Audio track disabled until Qwen emits:

```text
session.updated
```

Then enable microphone publishing.

## Phase 7 — Qwen session

Build `session.update` from the server `ready` payload.

Must include:
- returned instructions
- returned tools
- model/voice configuration
- smart_turn
- enable_search=false

Hermes tools:
- hermes_start
- hermes_steer
- hermes_stop
- hermes_approval

## Phase 8 — Function calling

When Qwen emits:

```text
response.function_call_arguments.done
```

forward it to ZhuanZ:

```json
{
  "type": "tool_call",
  "call_id": "...",
  "name": "...",
  "arguments": {}
}
```

When `tool_result` arrives, inject the matching Qwen:

```text
conversation.item.create(function_call_output)
response.create
```

Do not execute Hermes tools on the phone.

## Phase 9 — Background result

When the control WS receives:

```json
{
  "type": "background_result",
  "text": "..."
}
```

inject a matched synthetic:
1. function_call
2. function_call_output
3. response.create

Use a new local call id such as `background_<uuid>`.

Do not inject background results as a plain system message.

## Phase 10 — Approval

If Hermes returns `waiting_for_approval`, Qwen must ask the user.

Only support:
- explicit approve once -> hermes_approval {choice:"once"}
- explicit deny -> hermes_approval {choice:"deny"}

Never expose session/always approval through voice.

## Phase 11 — P0 Golden Path

Run on the real Huawei/HarmonyOS phone and collect logs/evidence.

Pass conditions:

1. Phone gets one-time AOQ token without receiving DASHSCOPE_API_KEY.
2. AOQ connects and receives session.updated.
3. Microphone starts only after session.updated.
4. Speaker playback works.
5. Ask a factual question -> Qwen invokes hermes_start.
6. Hermes answer is spoken.
7. Long Hermes task -> initial working acknowledgement -> final background result is spoken automatically.
8. User changes a running task -> hermes_steer.
9. User cancels -> hermes_stop.
10. Protected action -> explicit approve-once / deny flow works.
11. While Qwen speaks through the phone speaker, user interrupts:
    - microphone remains active
    - self-echo does not trigger a false turn
    - Qwen stops the old response
    - new utterance is processed
12. Toggle Wi-Fi off/on:
    - control WS reconnects with same client_id
    - AOQ reconnect obtains a fresh token
    - no Hermes side effect is duplicated.

## Required output

Commit the HarmonyOS source and docs to:

```text
voice-gateway/mobile/harmony/
```

Do not commit vendor SDK binaries unless redistribution is explicitly allowed.

Update:

```text
voice-gateway/DEPLOYMENT_REPORT.md
```

with:
- phone model
- HarmonyOS version/API level
- DevEco version
- AOQ SDK version
- LAN gateway address (do not include secret)
- Golden Path PASS/FAIL matrix
- AEC/barge-in result
- reconnect result
- any crash/error logs with secrets redacted

Push to the existing:

```text
feature/hermes-voice-gateway
```

and update PR #2. Do not create a duplicate PR.
