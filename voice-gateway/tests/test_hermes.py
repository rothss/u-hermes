from hermes_voice_gateway.hermes import HermesClient


def test_voice_session_rolls_over_after_configured_run_count():
    client = HermesClient(
        "http://127.0.0.1:8642",
        "test-key",
        "voice-main",
        "voice-key",
        session_rollover_runs=2,
    )
    try:
        assert client._session_id_for_next_run() == "voice-main"
        assert client._session_id_for_next_run() == "voice-main"
        assert client._session_id_for_next_run() == "voice-main-1"
        assert client._session_id_for_next_run() == "voice-main-1"
        assert client._session_id_for_next_run() == "voice-main-2"
    finally:
        import asyncio

        asyncio.run(client.close())
