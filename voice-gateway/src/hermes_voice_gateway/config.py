from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    dashscope_api_key: str = Field(alias="DASHSCOPE_API_KEY")
    qwen_realtime_model: str = Field(default="qwen-audio-3.1-realtime-plus", alias="QWEN_REALTIME_MODEL")
    qwen_realtime_url: str = Field(
        default="wss://maas.qwencloudapi.com/api-ws/v1/realtime",
        alias="QWEN_REALTIME_URL",
    )
    qwen_voice: str = Field(default="longanqian_v3.1", alias="QWEN_VOICE")
    qwen_turn_detection: str = Field(default="smart_turn", alias="QWEN_TURN_DETECTION")
    qwen_max_history_turns: int = Field(default=10, alias="QWEN_MAX_HISTORY_TURNS")

    hermes_base_url: str = Field(default="http://127.0.0.1:8642", alias="HERMES_BASE_URL")
    hermes_api_key: str = Field(default="", alias="HERMES_API_KEY")
    hermes_session_id: str = Field(default="voice-main", alias="HERMES_SESSION_ID")
    hermes_session_key: str = Field(default="agent:main:voice:primary", alias="HERMES_SESSION_KEY")
    hermes_session_rollover_runs: int = Field(default=4, alias="HERMES_SESSION_ROLLOVER_RUNS")
    hermes_inline_wait_seconds: float = Field(default=2.0, alias="HERMES_INLINE_WAIT_SECONDS")
    hermes_poll_interval_seconds: float = Field(default=0.25, alias="HERMES_POLL_INTERVAL_SECONDS")
    hermes_request_timeout_seconds: float = Field(default=15.0, alias="HERMES_REQUEST_TIMEOUT_SECONDS")

    audio_input_device: int | None = Field(default=None, alias="AUDIO_INPUT_DEVICE")
    audio_output_device: int | None = Field(default=None, alias="AUDIO_OUTPUT_DEVICE")
    audio_chunk_ms: int = Field(default=100, alias="AUDIO_CHUNK_MS")
    audio_mode: str = Field(default="headset", alias="AUDIO_MODE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
