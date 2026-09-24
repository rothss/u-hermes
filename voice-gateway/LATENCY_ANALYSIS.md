# Hermes Voice Gateway 延迟分析

分析时间：2026-09-24

## 结论

当前延迟主因是 Hermes `/v1/runs` 的上下文规模和远端任务执行时间，不是 ZhuanZ 到 ai-node 的网络，也不是 Qwen WebSocket 建连。

## 实测证据

| Run | Hermes 输入 tokens | 输出 tokens | 端到端运行时间 |
|---|---:|---:|---:|
| `run_b1bd...` | 15,844 | 29 | 5.83 s |
| `run_3eb2...` | 110,903 | 269 | 22.57 s |
| `run_2856...` | 79,128 | 71 | 17.43 s |
| `run_a5ae...` | 26,985 | 24 | 3.87 s |

- ai-node `/health`：5 次测量中位数 11.7 ms。
- Qwen WebSocket 握手：259.5 ms。
- Qwen `session.update` 到首个事件：24.3 ms。
- 日志中的 Hermes 轮询间隔约 0.27 s，与配置的 0.25 s 一致；它制造了大量 GET 请求，但不是主要的首答延迟。

## 延迟链路

```text
用户说完
  -> Qwen smart_turn 判定结束（当前未单独计时）
  -> Qwen 完成 function call 参数
  -> POST /v1/runs 立即返回 202
  -> Hermes 读取大 session 上下文并执行
  -> 网关每 250ms 轮询 /v1/runs/{id}
  -> 完成后才把 function_call_output 送回 Qwen
  -> Qwen 才开始生成语音
```

代码当前会在 [bridge.py](D:/HermesVoiceGateway/voice-gateway/src/hermes_voice_gateway/bridge.py:75) 最多等待 2 秒；超时后只能播报“正在处理”，真正结果仍需等 Hermes 完成。轮询实现位于 [hermes.py](D:/HermesVoiceGateway/voice-gateway/src/hermes_voice_gateway/hermes.py:72)。

## 优化优先级

### P0：隔离或裁剪 Hermes 语音会话上下文

当前固定使用 `voice-main` 会话，历史越积越大，已经出现 110k 输入 tokens。当前已改为每 4 次 run rollover；仍建议：

1. 语音交互使用独立、短上下文的 session；
2. 只保留最近若干轮语音对话；
3. Memory 继续依赖 Hermes 的独立 Memory 能力，不把全部历史对话重复塞进每次 `/v1/runs`；
4. 为 voice session 增加硬上限和定期 rollover。

这是预期收益最大的改动，优先于调轮询和音频块大小。

### P1：把 Hermes 完成通知从轮询改为事件流

当前每 250ms 轮询一次，长任务会产生几十到上百个 GET。Hermes 暴露了 `/v1/runs/{id}/events` 路由，后台监控已切换到 SSE；短的 2 秒 inline 等待保留退避轮询，避免取消 SSE 时触发 Hermes 的单队列传输清理。SSE 解析同时识别 `event:` 和 `data:`，每 5 秒用状态 GET 兜底，防止事件流静默时无限等待。

这主要降低请求量和日志噪声，对 Hermes 本身的推理耗时改善有限。

### P1：降低快速任务的等待阈值

`HERMES_INLINE_WAIT_SECONDS=2.0` 会让每个快速任务至少等待一段时间才决定是否播报“正在处理”。可尝试 0.5–1.0 秒，但它只改善用户收到中间确认的速度，不会缩短最终结果时间。

### P2：降低语音端点检测和音频缓冲延迟

- 当前 `QWEN_TURN_DETECTION=smart_turn`，端点判定时间尚未记录；应增加 `speech_started`、`speech_stopped`、function-call 的时间戳。
- 当前 `AUDIO_CHUNK_MS=100`，单块音频本身引入最多约 100ms 缓冲；可测试 40–60ms，避免一次把块调得过小导致 CPU/网络消息数激增。
- 官方模型示例使用 `server_vad`，可以作为低延迟对照组，但需要保留 smart_turn 与 server_vad 的 A/B 测试结果后再决定。

### P2：优化播放队列和可观测性

当前 `response.audio.delta` 直接加入播放队列，日志没有统一记录：

- 语音结束判定时间；
- function call 发出/完成时间；
- Hermes POST 返回时间；
- Hermes terminal 时间；
- Qwen 首个 audio delta 时间；
- 首个 PCM 实际播放时间。

没有这些时间点，只能看到 HTTP 轮询，无法精确区分“听懂慢”“Hermes 慢”和“扬声器开始播放慢”。

## 不建议先做的事

- 不要先把 `HERMES_POLL_INTERVAL_SECONDS` 调到极小；这只会增加请求和日志。
- 不要先换模型或关闭 Hermes；架构要求实质回答仍由 Hermes 完成。
- 不要把 Qwen 的 `max_history_turns` 当作 Hermes 上下文限制；它只限制 Qwen Realtime 会话历史，不能解决 Hermes `/v1/runs` 的大输入。

## 当前状态

## 本次已实施

- Hermes voice session 每 4 次 run 自动 rollover：`voice-main`、`voice-main-1`……，避免历史上下文无限增长。
- Hermes 后台 run 通过 `/v1/runs/{id}/events` SSE 接收完成事件；短 inline 等待使用退避轮询，SSE 断流或静默时自动回退状态 GET。
- 增加 Qwen WebSocket、session.updated、speech start/stop、tool call、Hermes run、首个音频包和 response done 的耗时日志。
- Qwen 空闲 180 秒超时或网络断开后自动重连，不再因一次空闲断线导致整个网关退出。
- 当前扬声器模式切换为 `AUDIO_MODE=safe`，阻止扬声器回灌造成的重复识别；耳机接入后可恢复 `headset`。
- 新增 session rollover 单元测试；测试结果：`5 passed`。

### 本次问题复现与验证

- 复盘日志发现两次 Hermes run 已分别在约 4.3 秒、24.4 秒完成，但旧 SSE 监控没有收到 terminal，导致没有最终语音反馈；Qwen 随后在空闲 180 秒时退出。
- 修复后以“inline 等待 2 秒后转后台监控”真实调用验证：`inline=running_after_2s`，后台返回 `completed`，总耗时 4.6 秒。

最值得继续实施的顺序是：

```text
voice session 上下文上限/rollover
  -> 完成事件流
  -> 端到端时间戳
  -> smart_turn vs server_vad / 音频块大小 A/B
```
