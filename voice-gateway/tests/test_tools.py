from hermes_voice_gateway.bridge import HermesBridge


def test_tools_force_hermes_brain_contract():
    schemas = HermesBridge.tool_schemas()
    names = [x["function"]["name"] for x in schemas]
    assert names == ["hermes_start", "hermes_steer", "hermes_stop"]
    assert "MANDATORY" in schemas[0]["function"]["description"]
