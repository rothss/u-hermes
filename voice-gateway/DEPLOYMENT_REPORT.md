# Hermes Voice Gateway 部署报告

状态：源码已修复。当前手动启动的旧进程需要退出后重新运行 run.ps1 才会加载新代码；本轮未启动常驻网关。

## 后台结果重复“正在查询”修复

- 旧实现追加 system 消息后触发 response，真实日志显示 Qwen 仍重复旧 running 提示。
- 新实现使用独立 call_id 注入 function_call / function_call_output 配对，再触发 response.create；加锁防止并发播报，等待 session ready，发送失败时保留结果。
- 按百炼文档技能核对协议，参考 raw/model-api-reference/audio-api-references/voice-conversation-api-references/real-time-voice-conversation-api-references/fun-audiochat-client-events.md 与 https://help.aliyun.com/en/model-studio/fun-audiochat-client-events 。
- 实际 Qwen WebSocket 回归：先生成“正在查询海口天气”，再注入固定测试结果，生成包含“多云、31摄氏度、带伞”的语音及转写；最终结果生成 2.02 秒。
- 该回归未启用麦克风或扬声器，也未重新查询实际天气；验证范围为后台结果到 Qwen 音频生成，不等同于用户听感验收或 Hermes 查询耗时。
- 自动测试：7 passed；覆盖结果配对、并发排队、会话未就绪以及发送失败后重试。

## Environment

- OS：Microsoft Windows 11 家庭版
- Python：3.13.14
- Hermes：0.21.3（ai-node）
- Gateway branch：`feature/hermes-voice-gateway`
- Gateway commit：`9da2e62679c6fb96fbb964d0cae23471246180a1`
- 安装目录：`D:\HermesVoiceGateway\voice-gateway`

## Connectivity

- Qwen realtime websocket：PASS，已收到 `session.updated`
- Qwen endpoint：本机百炼 `cn-beijing` 工作空间专属 WebSocket
- Hermes health：PASS，`http://100.74.90.6:8642/health`
- Hermes authentication：PASS

## Hermes capabilities

- `run_submission`：PASS
- `run_status`：PASS
- `run_steer`：PASS
- `run_stop`：PASS

## Audio

- Input：device 1，麦克风 (Realtek(R) Audio)，PASS
- Output：device 3，扬声器 (Realtek(R) Audio)，PASS
- Capture sample rate：16 kHz
- Playback sample rate：24 kHz
- Mode：`safe`；当前使用桌面 Realtek 端点，阻止扬声器回灌；接入耳机后可恢复 `headset` 以测试 barge-in
- Bluetooth headset endpoints：当前无法打开（PyAudio `-9999`）

## Tests

- Unit tests：PASS，5 passed
- Latency optimization tests：PASS，5 passed
- Background monitor regression：PASS，inline 2 秒后转后台，Hermes 在 4.6 秒内返回 completed
- Normal Hermes request：PARTIAL，已观察到 `/v1/runs` 返回 202，需人工确认语音问答内容
- Memory：待人工语音测试
- Skill：待人工语音测试
- Long run：待人工语音测试
- Steer：待人工语音测试
- Stop：待人工语音测试
- Background completion announcement：待人工语音测试
- User barge-in：待接入可用耳机后测试

## Latency optimization

- Voice session rollover：每 4 次 Hermes run 自动切换 session ID
- Run completion：SSE 优先，退避轮询兜底
- 短 inline 等待使用退避轮询，避免取消 SSE 造成 Hermes 单队列事件丢失；后台 SSE 每 5 秒状态兜底
- Timing logs：Qwen、tool call、Hermes run、首个音频包和 response done
- Qwen 空闲超时/断线自动重连，避免运行约 3 分钟后网关静默退出
- Current audio mode：`safe`，避免 Realtek 扬声器回灌；耳机接入后切换到 `headset`

## Security

- Hermes 与 Qwen key 仅保存在本地 `.env`，`.env` 已被 Git 忽略。
- 未将任何密钥写入源码、README、报告或 Git。
- Hermes API 通过 Tailscale 地址访问，未关闭认证。

## 启动

配置 `DASHSCOPE_API_KEY` 后运行：

```powershell
cd D:\HermesVoiceGateway\voice-gateway
.\scripts\windows\check.ps1
.\scripts\windows\run.ps1
```
