# Architecture

## Goal

Implement Plan B: Hermes is always the brain. Qwen-Audio-3.1-Realtime-Plus is only the real-time speech shell.

```text
Microphone (16 kHz PCM)
        |
        v
Qwen-Audio-3.1-Realtime-Plus
  - smart_turn / VAD
  - realtime ASR
  - barge-in
  - speech generation
        |
        | mandatory function call for substantive turns
        v
Hermes Voice Gateway
  hermes_start / hermes_steer / hermes_stop
        |
        v
Hermes Agent API :8642
  - /v1/runs
  - /v1/runs/{id}/steer
  - /v1/runs/{id}/stop
  - memory / skills / tools / terminal / files / web / email
```

## Why /v1/runs

A spoken request can launch a long Hermes task. The gateway waits briefly for fast answers. If the run exceeds the inline threshold, it returns a short working acknowledgement to Qwen and monitors Hermes in the background. The microphone remains live. A subsequent spoken correction maps to /steer; a spoken cancel maps to /stop.

## Session continuity

The gateway sends:
- session_id in the Hermes run payload
- X-Hermes-Session-Id
- X-Hermes-Session-Key

This keeps voice turns tied to one Hermes session/memory scope rather than creating a fresh agent context each utterance.

## Full duplex and acoustic echo

The API supports interruption, but a desktop Python process does not automatically provide acoustic echo cancellation (AEC). For the MVP use a headset and AUDIO_MODE=headset for true barge-in. AUDIO_MODE=safe suppresses microphone upload while output audio is playing and is therefore not true full duplex.

For the phone phase, use Qwen's HarmonyOS/Android SDK with VoIP/AEC and keep the same Hermes tool contract.

## Security

- Never commit .env.
- Keep the QwenCloud key server-side for production mobile deployments.
- Bind Hermes to localhost or a trusted private network; use a bearer key.
- Do not expose Hermes :8642 directly to the public Internet.
