"""Root independent public-order and early-limit counterexamples."""
import copy
from dataclasses import asdict

import pytest

from genshin_db import runtime_energy_dispatch as dispatch
from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_attack_metadata import get_attack_metadata_context
from genshin_db.runtime_catalog import RuntimeConfigError, fingerprint
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_energy_dispatch import (  # noqa: F401
    identity,
    origin,
    trigger_db,
    trigger_repo,
)


@pytest.mark.parametrize("variant,local", [("tap", False), ("hold", False), ("hold", True)])
@pytest.mark.parametrize("prewarm", [("slot",), ("id",), ("slot", "id"), ("id", "slot")])
def test_all_real_fixture_parent_fields_are_order_independent(trigger_db, variant, local, prewarm):  # noqa: F811 - shared pytest fixture
    parent = identity(trigger_db, variant, local)
    ref = origin(trigger_db, variant, local)
    cold = dispatch.get_energy_dispatch_context(trigger_db, origin_reference_id=ref, parent_identity=parent)
    cold_fields = asdict(cold)
    with equipment_read_session(trigger_db):
        for mode in prewarm:
            selection = copy.deepcopy(parent["selection"])
            if mode == "slot":
                del selection["skill_id"]
                selection["slot"] = "skill"
            get_attack_metadata_context(trigger_db, **selection)
        result = dispatch.get_energy_dispatch_context(trigger_db, origin_reference_id=ref, parent_identity=parent)
        assert fingerprint(asdict(result)) == fingerprint(cold_fields)
        result.parameter_context["facts"]["records"].clear()
        again = dispatch.get_energy_dispatch_context(trigger_db, origin_reference_id=ref, parent_identity=parent)
        assert fingerprint(asdict(again)) == fingerprint(cold_fields)


def test_full_budget_cannot_be_bypassed_by_resolved_facts_alias(monkeypatch):
    from genshin_db import equipment_queries, runtime_action_parameter
    selection = {"character_id": 1, "skill_depot_id": 2, "skill_id": 3, "talent_level": 10, "constellation": 0}
    state = {"snapshot_id": "s" * 64}
    identity = {"version_line": "release", "selection": selection}
    # Deliberately 32 distinct already-loaded requests with equivalent resolved
    # facts. None has this precise request key, so no permission to alias exists.
    cache = {("parameters", str(i)): {"facts": {"snapshot": state,
        "selection": {**selection, "slot": "skill"}, "level": {"effective_level": 10}}} for i in range(32)}
    before = copy.deepcopy(cache)
    monkeypatch.setattr(equipment_queries, "borrow_read_cache", lambda *_: cache)
    def no_read(*args, **kwargs):
        raise AssertionError("Must reject before any extra parameter read")
    monkeypatch.setattr(runtime_action_parameter, "get_action_parameter_context", no_read)
    with pytest.raises(RuntimeConfigError) as exc:
        dispatch._parameters("never-opened", identity, state)
    assert exc.value.reason == "response_too_large"
    assert cache == before
