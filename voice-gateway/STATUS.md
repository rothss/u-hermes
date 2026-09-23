# MVP status

- Architecture: Plan B — Hermes is the sole reasoning/agent brain.
- Speech transport: Alibaba Cloud Bailian `qwen-audio-3.1-realtime-plus` over WebSocket.
- Voice host: ZhuanZ Windows PC.
- Hermes integration: `/v1/runs` + bounded transcript session + stable memory key + SSE + `/steer` + `/stop` + explicit run approval.
- Reliability: Qwen reconnect, background-result retention, Qwen call-id dedupe, Hermes idempotent run submission.
- Audio: 40 ms input frames; headset mode is full duplex, safe mode is anti-feedback half duplex.
- Secrets: local `.env` only; not committed.

## Validation state

The previously deployed ZhuanZ build successfully connected to Bailian Beijing and Hermes 0.21.3. The optimization commits after that live run still need to be pulled and re-run locally before their tests are reported as passed.

Required validation after pull:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\windows\check.ps1
.\scripts\windows\run.ps1
```

Golden-path checks should include normal Hermes routing, long-run completion, steer, stop, explicit approve-once/deny, reconnect without duplicate side effects, and headset barge-in.
