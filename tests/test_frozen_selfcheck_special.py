"""Generic package probes execute the public Engine, never forge App results."""
from __future__ import annotations

import copy
import sys
from collections import Counter

import pytest

from genshin_app.frozen_selfcheck_isolation import SelfcheckError
from genshin_app.frozen_selfcheck_special import verify_fixed_special_reactions
from genshin_engine import special_reactions as engine


def test_five_real_families_have_independent_engineering_numbers_and_pending():
    before = copy.deepcopy(dict(engine.REVIEWED_SPECIAL_REACTION_PROFILES))
    receipt = verify_fixed_special_reactions()
    assert receipt["status"] == "verified"
    assert receipt["input_scope"] == "synthetic_engine_input" and receipt["mechanic_status"] == "assumed"
    assert receipt["execution"]["actual_frozen_tested"] is False
    assert receipt["execution"]["layer"] == "source_probe"
    assert receipt["app_formal_result"] is receipt["benchmark_qualified"] is receipt["included_in_594_total"] is False
    assert not {"total_damage", "dps", "accepted"} & receipt.keys()
    families = receipt["families"]
    assert set(families) == set(engine.SPECIAL_REACTION_TYPES)
    assert families["stellar_superconduct"]["settlement"]["layers"] == 1
    assert families["stellar_superconduct"]["direct_formula"]["check"]["actual"] == pytest.approx(100*2*(1.4+.05))
    swirl = families["stellar_swirl"]
    assert swirl["immediate_check"]["actual"] == pytest.approx(100*.6+200*.3)
    assert swirl["detonation_check"]["actual"] == pytest.approx(200*.6+100*.3)
    assert swirl["post_damage_application"]["application_order"] == "after_damage"
    electro = families["lunar_electro"]
    assert electro["tick"]["t"] == .25
    assert electro["hydro_check"]["actual"] == pytest.approx(1-.4)
    assert electro["electro_check"]["actual"] == pytest.approx(.5-.4)
    # Enumerate both independent Crit outcomes, never ask the tested formula
    # to supply its own expected value.
    assert electro["damage_check"]["actual"] == pytest.approx(.5*(100*.6+60*.3)+.5*(180*.6+100*.3))
    assert electro["direct_formula"]["check"]["actual"] == 100*2*3
    bloom = families["lunar_bloom"]
    assert bloom["dew_before"] == 1 and bloom["consumption"]["resource_event"]["after"] == 0
    assert bloom["consumption"]["resource_event"]["lunar_damage_emitted"] is False
    assert bloom["direct_formula"]["check"]["actual"] == 100*2
    crystallize = families["lunar_crystallize"]
    assert len(crystallize["cage_damage"]) == 3
    assert crystallize["cage_subtotal_check"]["actual"] == 3*(100*.6)
    assert crystallize["direct_formula"]["check"]["actual"] == 100*2*1.6
    assert crystallize["assumptions"]["status"] == "assumed"
    pending = receipt["pending"]
    assert {row["issue"]["code"] for row in pending} == {"special_mechanics_pending", "special_qualification_pending"}
    assert all(row["state_unchanged"] for row in pending)
    assert pending[0]["mechanic_input"]["harmony_attack_cooldown_seconds"] is None
    assert pending[0]["mechanic_input"]["harmony_attack_cooldown_scope"] is None
    assert pending[0]["mechanic_input"]["status"] == "pending"
    assert engine.REVIEWED_SPECIAL_REACTION_PROFILES == before


def test_real_public_calls_are_consumed_and_all_models_remain_explicit_engineering(monkeypatch):
    counts, models = Counter(), []
    runtime = engine.SpecialReactionRuntime
    original_init = runtime.__init__

    def initialize(self, model, **kwargs):
        models.append(copy.deepcopy(model))
        return original_init(self, model, **kwargs)

    monkeypatch.setattr(runtime, "__init__", initialize)
    for name in ("replace", "replace_stellar_swirl_packet", "advance", "admit_lunar_electro_tick",
                 "consume_lunar_bloom", "record_stellar_superconduct_application"):
        original = getattr(runtime, name)

        def call(self, *args, _original=original, _name=name, **kwargs):
            counts[_name] += 1
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(runtime, name, call)
    receipt = verify_fixed_special_reactions()
    assert receipt["status"] == "verified"
    assert counts["replace"] >= 8 and counts["advance"] >= 5
    assert counts["replace_stellar_swirl_packet"] == counts["admit_lunar_electro_tick"] == 1
    assert counts["consume_lunar_bloom"] == counts["record_stellar_superconduct_application"] == 1
    assert len(models) == 7
    for model in models:
        replacement = next(iter(model["replacements"].values()))
        assert replacement["mechanics"]["status"] in ("assumed", "pending")
        assert "synthetic_engine_input" in replacement["mechanics"]["source"]
        assert "数据库资格投影" in replacement["qualification"]["source"]


@pytest.mark.parametrize("formula", ["calculate_direct_special_damage", "resolve_special_damage_event"])
def test_corrupted_actual_numeric_result_cannot_qualify_itself(monkeypatch, formula):
    original = getattr(engine, formula)

    def corrupt(*args, **kwargs):
        value = original(*args, **kwargs)
        if formula == "calculate_direct_special_damage":
            value["result"]["expected"] += 1
        else:
            value["damage"]["expected"] += 1
        return value

    monkeypatch.setattr(engine, formula, corrupt)
    with pytest.raises(SelfcheckError) as caught:
        verify_fixed_special_reactions()
    assert caught.value.reason == "selfcheck_special_numeric_mismatch"


def test_unexpected_pending_consumption_is_a_failed_probe_not_success(monkeypatch):
    original = engine.SpecialReactionRuntime.replace

    def wrongly_accept(self, *args, **kwargs):
        row = self.model["replacements"].get("lunar_crystallize")
        if row and row["mechanics"]["status"] == "pending":
            return {"generated_events": [], "state": self.state()}
        return original(self, *args, **kwargs)

    monkeypatch.setattr(engine.SpecialReactionRuntime, "replace", wrongly_accept)
    with pytest.raises(SelfcheckError) as caught:
        verify_fixed_special_reactions()
    assert caught.value.reason == "selfcheck_special_pending_was_executed"


def test_source_cannot_claim_frozen_qualification_even_with_a_flag(tmp_path, monkeypatch):
    with pytest.raises(SelfcheckError) as caught:
        verify_fixed_special_reactions(frozen_package_root=tmp_path)
    assert caught.value.reason == "selfcheck_special_helper_not_frozen"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    with pytest.raises(SelfcheckError) as caught:
        verify_fixed_special_reactions(frozen_package_root=tmp_path)
    assert caught.value.reason == "selfcheck_special_helper_loader_not_frozen"
