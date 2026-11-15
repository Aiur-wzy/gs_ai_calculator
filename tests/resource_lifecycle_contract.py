"""Test-only v3/clock2 schema opt-in while App source closure remains pending."""
import pytest

from genshin_engine.mechanics_schema import MECHANICS_SCHEMA
from genshin_engine.resource_threshold_trigger_schema import (
    RESOURCE_THRESHOLD_TRIGGER_SCHEMA,
    RESOURCE_THRESHOLD_TRIGGER_V2_SCHEMA,
    RESOURCE_THRESHOLD_TRIGGER_V3_SCHEMA,
)
from genshin_engine.state_runtime_schema import (
    STATE_RUNTIME_SCHEMA,
    STATE_RUNTIME_V2_SCHEMA,
)


@pytest.fixture
def private_lifecycle_contract(monkeypatch):
    properties = MECHANICS_SCHEMA["properties"]
    monkeypatch.setitem(properties, "state_runtime", {"oneOf": [STATE_RUNTIME_SCHEMA, STATE_RUNTIME_V2_SCHEMA]})
    monkeypatch.setitem(properties, "resource_threshold_triggers", {"oneOf": [
        RESOURCE_THRESHOLD_TRIGGER_SCHEMA, RESOURCE_THRESHOLD_TRIGGER_V2_SCHEMA, RESOURCE_THRESHOLD_TRIGGER_V3_SCHEMA]})
