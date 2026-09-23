# MVP status

- Architecture: Plan B — Hermes is the sole reasoning/agent brain.
- Speech transport: Qwen-Audio-3.1-Realtime-Plus over WebSocket.
- Hermes integration: /v1/runs + session continuity + /steer + /stop.
- Desktop audio: headset full-duplex mode, plus safe anti-echo mode.
- Local unit tests: 4 passed before GitHub staging.
- Secrets: not committed.

## Deployment status

Code and Windows scripts are ready. A real-machine launch still requires:
1. a QwenCloud Singapore-region API key;
2. a running Hermes API server on the target Windows machine;
3. the machine's chosen microphone/output device.

This branch is a staging location because the connected GitHub integration can edit repositories but cannot create a brand-new repository. The module can be moved to a dedicated repository later without changing its package structure.
