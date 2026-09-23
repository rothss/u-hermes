from hermes_voice_gateway.bridge import HermesBridge


def test_tools_force_hermes_brain_contract():
    schemas = HermesBridge.tool_schemas()
    names = [x["function"]["name"] for x in schemas]
    assert names == ["hermes_start", "hermes_steer", "hermes_stop", "hermes_approval"]
    assert "MANDATORY" in schemas[0]["function"]["description"]


def test_voice_approval_is_one_time_or_deny_only():
    schema = HermesBridge.tool_schemas()[-1]["function"]
    assert schema["name"] == "hermes_approval"
    assert schema["parameters"]["properties"]["choice"]["enum"] == ["once", "deny"]
