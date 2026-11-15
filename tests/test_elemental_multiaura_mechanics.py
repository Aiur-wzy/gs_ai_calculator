"""End-to-end mechanics projection for the v4 elemental runtime."""
from __future__ import annotations

import pytest
from test_elemental_multiaura_v4 import aura, model
from test_mechanics_execution import fixture

from genshin_engine.mechanics import execute_mechanics


def _single_application(element: str, *, initial=()):
    template, rotation, mechanics = fixture((0.2,))
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    template["hits"][0]["element"] = element
    mechanics["hit_events"][0]["application"] = {
        "profile_id": element,
        "target_id": "enemy",
        "landed": True,
    }
    mechanics["elemental_model"] = model(initial)
    return template, rotation, mechanics


def test_electro_swirl_projects_native_reaction_damage_and_chinese_label():
    out = execute_mechanics(*_single_application("anemo", initial=[aura("electro", 0.7, "electro-owner")]))
    assert out["status"] in ("passed", "unproven"), out["issues"]
    hits = out["resolved_template"]["hits"]
    assert [hit["id"] for hit in hits] == ["h0", "h0:reaction_damage:swirl:electro"]
    assert hits[1]["hit_name"] == "雷扩散"
    assert hits[1]["reaction"] == {"type": "swirl"}
    assert hits[1]["element"] == "electro"
    assert out["result"]["hits"][1]["expected"] == pytest.approx(781.299)


def test_crystallize_projects_semantic_reaction_and_shard_without_fake_damage():
    out = execute_mechanics(*_single_application("geo", initial=[aura("hydro", 0.7, "hydro-owner")]))
    assert out["status"] in ("passed", "unproven"), out["issues"]
    assert [hit["id"] for hit in out["resolved_template"]["hits"]] == ["h0"]
    reactions = [row for row in out["mechanics_trace"] if row.get("type") == "elemental_reaction"]
    assert len(reactions) == 1
    assert reactions[0]["reaction_type"] == "crystallize"
    application = next(row for row in out["elemental_trace"]
                       if row.get("type") == "elemental_application")
    shard = application["after"]["crystallize"]["shards"][0]
    assert shard["element"] == "hydro"
    assert shard["owner"] == "a"
