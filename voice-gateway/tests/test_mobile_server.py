from types import SimpleNamespace

import pytest

from hermes_voice_gateway.mobile_server import (
    _safe_client_id,
    derive_aoq_token_url,
    extract_aoq_credentials,
)


def test_derive_aoq_token_url_from_workspace_realtime_url():
    settings = SimpleNamespace(
        qwen_aoq_token_url="",
        qwen_realtime_url=(
            "wss://workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
        ),
        qwen_realtime_model="qwen-audio-3.1-realtime-plus",
    )
    assert derive_aoq_token_url(settings) == (
        "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/webrtc/realtime"
        "?model=qwen-audio-3.1-realtime-plus"
    )


def test_explicit_aoq_token_url_wins():
    settings = SimpleNamespace(
        qwen_aoq_token_url="https://example.invalid/token",
        qwen_realtime_url="wss://ignored.invalid/realtime",
        qwen_realtime_model="ignored",
    )
    assert derive_aoq_token_url(settings) == "https://example.invalid/token"


def test_extract_aoq_credentials_returns_only_client_fields():
    payload = {
        "aoqTokenForClient": "one-time",
        "sid": "sid-1",
        "clientRelayCertFingerprint": "fp",
        "clientRelayEndpoints": ["relay-1"],
        "extraInfo": {"workspaceIdHash": "workspace-hash", "private": "drop-me"},
        "serverSecret": "drop-me",
    }
    assert extract_aoq_credentials(payload) == {
        "aoqTokenForClient": "one-time",
        "sid": "sid-1",
        "clientRelayCertFingerprint": "fp",
        "clientRelayEndpoints": ["relay-1"],
        "workspaceIdHash": "workspace-hash",
    }


def test_extract_aoq_credentials_accepts_nested_output():
    payload = {
        "output": {
            "aoqTokenForClient": "one-time",
            "sid": "sid-1",
            "clientRelayCertFingerprint": "fp",
            "clientRelayEndpoints": ["relay-1"],
            "extraInfo": {"workspaceIdHash": "workspace-hash"},
        }
    }
    assert extract_aoq_credentials(payload)["aoqTokenForClient"] == "one-time"


def test_extract_aoq_credentials_rejects_incomplete_payload():
    with pytest.raises(ValueError):
        extract_aoq_credentials({"sid": "missing-token"})


def test_safe_client_id_is_bounded_and_path_safe():
    value = _safe_client_id("../../手机 A / 123")
    assert "/" not in value
    assert ".." not in value
    assert len(value) <= 48
