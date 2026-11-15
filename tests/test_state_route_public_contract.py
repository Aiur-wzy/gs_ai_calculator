"""Route identity is discoverable without importing a private bridge helper."""
from genshin_app.agent_chat.agent_hash_input import hash_input
from genshin_engine.state_runtime_mechanics import validate_state_runtime_bindings
from genshin_engine.state_runtime_mechanics_schema import (
    state_runtime_mechanics_capabilities,
)
from tests.test_state_runtime_mechanics import model


def test_public_hash_command_reconstructs_published_route_example():
    routing = state_runtime_mechanics_capabilities()["dispatch_routing"]
    sample = routing["example"]
    result = hash_input("canonical_json", [sample["kind"], sample["ref_id"]], maximum_bytes=4096)
    assert routing["event_id_prefix"] + result["content_hash"] == sample["event_id"]
    assert result["authority"] == routing["authority"] == "none"


def test_bad_route_returns_exact_expected_selector_but_no_execution_authority():
    data = model()
    data["rules"][0]["trigger"].pop("event_ids")
    issues = validate_state_runtime_bindings(data, character_ids=["a", "b"],
        event_ids=["cast"], action_ids=["cast"], conditions=[])
    error = next(row for row in issues if row["code"] == "state_dispatch_ownership")
    expected = error["details"]["expected_selector"]
    assert expected["event"] == "condition" and expected["phase"] == "post"
    computed = hash_input("canonical_json", ["dispatch_rule", "buff"], maximum_bytes=4096)
    assert expected["event_ids"] == ["@state_route:" + computed["content_hash"]]
    assert error["details"]["authority"] == "none"
