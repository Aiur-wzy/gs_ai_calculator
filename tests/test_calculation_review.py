"""Bounded review proves what exists without inventing expected game coverage."""
import copy
import json

import pytest

from genshin_app.calculation_review import read_bounded_json, review_calculation
from genshin_app.cli import main
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError, new_plan
from genshin_app.equipment.service import run_plan_calc


def _saved():
    hits = [{"id": f"cast.{cast}.{i}", "character": "actor-a", "skill": "元素爆发",
             "damage_type_ids": ["damage.elemental_burst"], "t": cast * 10 + i / 10,
             "expected": 10.0 + i, "count": 1, "zones": {}, "hit_name": "持续接触"}
            for cast in range(3) for i in range(1, 21)]
    result = {"hits": hits, "summary": {"total_damage": sum(h["expected"] for h in hits), "dps": 41.0,
              "by_character": {"actor-a": 1230.0}, "by_skill": {"元素爆发": 1230.0}},
              "action_event_compilations": [{"generated": [{"hit_id": h["id"],
                  "action_id": ".".join(h["id"].split(".")[:2]), "ability_id": "burst-a"} for h in hits]}],
              "target_condition_trace": [{"event_id": hits[0]["id"], "owner": "actor-b", "t": .1,
                  "fixed_source_id": "team-source", "applied": False, "reason": "recipient_mismatch"}]}
    return {"result": result, "result_receipt": {"result_fingerprint": fingerprint(result)}}


def test_summary_distinguishes_twenty_events_and_pagination_never_loses_hits():
    saved = _saved()
    original = copy.deepcopy(saved)
    report = review_calculation(saved, {"filters": {"character": "actor-a", "damage_type_id": "damage.elemental_burst"}})
    assert report["items"][0]["event_count"] == 60
    assert report["items"][0]["expected"] == 1230
    ids = []
    offset = 0
    while offset is not None:
        page = review_calculation(saved, {"view": "hits", "offset": offset, "limit": 7,
                                         "filters": {"action_id": "cast.1"}})
        assert page["total_rows"] == 20
        ids += [r["id"] for r in page["items"]]
        offset = page["next_offset"]
    assert ids == [f"cast.1.{i}" for i in range(1, 21)]
    assert saved == original


def test_time_half_open_and_rejection_reason_are_visible():
    saved = _saved()
    page = review_calculation(saved, {"view": "hits", "filters": {"start": 10.1, "end": 10.3}})
    assert [r["id"] for r in page["items"]] == ["cast.1.1", "cast.1.2"]
    page = review_calculation(saved, {"view": "target_conditions", "filters": {"source_id": "team-source"}})
    assert page["items"][0]["reason"] == "recipient_mismatch"
    assert page["items"][0]["json_pointer"] == "/result/target_condition_trace/0"
    assert page["items"][0]["applied"] is False


def test_target_filter_uses_the_saved_hit_target():
    saved = _saved()
    saved["result"]["hits"][0]["target_id"] = "enemy.a"
    saved["result"]["hits"][1]["target_id"] = "enemy.b"
    saved["result_receipt"]["result_fingerprint"] = fingerprint(saved["result"])
    report = review_calculation(saved, {"view": "hits", "filters": {"target_id": "enemy.b"}})
    assert [(hit["id"], hit["target_id"]) for hit in report["items"]] == [("cast.0.2", "enemy.b")]


def test_resource_pages_keep_actual_units_source_identity_and_original_pointers():
    saved=_saved()
    saved['result']['resource_counters']={'trace':[
        {'type':'counter_accumulation','counter_id':'count','time_us':t,'requested':8,'resource_accepted':3,'accepted':3,
         'before':16,'after':19,'source_id':'counter-source'} for t in (100000,200000,300000)]}
    saved['result']['resource_counter_views']={'trace':[{
        'type':'counter_view_read','event_id':'cast.0.1','t':.3,'effect_source_id':'buff-source','source_value':1.6,
        'single_value':.16,'layer_count':3,'combined_value':.48,'reading':{
            'source_id':'view-source','counter_source_id':'counter-source','raw_atoms':19,'unit_scale':10,
            'numerator':16,'denominator':10,'cap_applied':True,'counter_cursor':{'control_sequence':7,'ledger_sequence':3},
            'sampling_authority':'mechanics_committed_ledger'}}]}
    saved['result_receipt']['result_fingerprint']=fingerprint(saved['result'])
    before=copy.deepcopy(saved)
    page=review_calculation(saved,{'view':'resource_counters','limit':1,'filters':{'start':.2,'end':.31,'source_id':'counter-source'}})
    assert page['total_rows']==2 and page['next_offset']==1
    row=page['items'][0]
    assert (row['t'],row['requested'],row['resource_accepted'],row['accepted'])==(.2,8,3,3)
    assert row['json_pointer']=='/result/resource_counters/trace/1'
    for source in ('view-source','counter-source','buff-source'):
        page=review_calculation(saved,{'view':'resource_counter_views','filters':{'source_id':source}})
        row=page['items'][0]
        assert row['reading']['raw_atoms']==19 and row['reading']['counter_cursor']['ledger_sequence']==3
        assert (row['single_value'],row['layer_count'],row['combined_value'])==(.16,3,.48)
        assert row['json_pointer']=='/result/resource_counter_views/trace/0'
    assert not review_calculation(saved,{'view':'resource_counter_views','filters':{'source_id':'unknown'}})['items']
    assert saved==before


def test_aggregated_hit_count_does_not_multiply_damage_twice():
    saved = _saved()
    saved["result"]["hits"][0]["count"] = 20
    saved["result_receipt"]["result_fingerprint"] = fingerprint(saved["result"])
    report = review_calculation(saved, {"filters": {"event_id": "cast.0.1"}})
    row = report["items"][0]
    assert row["event_count"] == 1 and row["declared_hit_count"] == 20
    assert row["expected"] == 11


@pytest.mark.parametrize("query", [
    {"limit": True}, {"limit": 201}, {"offset": -1}, {"view": "guess_missing_hits"},
    {"filters": {"name_contains": "burst"}}, {"filters": {"start": 5, "end": 5}},
    {"filters": {"start": float("inf")}}, {"filters": {"event_id": ""}}, {"unknown": 1},
])
def test_invalid_requests_not_silently_relaxed(query):
    with pytest.raises((PlanError, ValueError)):
        review_calculation(_saved(), query)


def test_edited_or_duplicate_result_cannot_be_reviewed_as_bound():
    data = _saved()
    data["result"]["hits"][0]["expected"] += 100
    with pytest.raises(PlanError, match="指纹"):
        review_calculation(data)
    data["result"]["hits"][1]["id"] = data["result"]["hits"][0]["id"]
    data["result_receipt"]["result_fingerprint"] = fingerprint(data["result"])
    with pytest.raises(PlanError, match="重复"):
        review_calculation(data)


def test_read_limit_and_nonfinite_json(tmp_path):
    path = tmp_path / "input.json"
    path.write_bytes(b'{"a": 1234}')
    with pytest.raises(PlanError, match="上限"):
        read_bounded_json(path, 5)
    path.write_bytes(b'{"a": NaN}')
    with pytest.raises(PlanError):
        read_bounded_json(path, 100)


def test_cli_readonly_and_protects_result_and_request(tmp_path, capsys):
    path, query, output = [tmp_path / p for p in ("result.json", "query.json", "page.json")]
    path.write_text(json.dumps(_saved(), ensure_ascii=False), encoding="utf-8")
    query.write_text('{"view":"hits","limit":3}', encoding="utf-8")
    before = path.read_bytes()
    cmd = ["--data-dir", str(tmp_path), "equipment", "review", "--calculated", str(path), "--request", str(query)]
    assert main(cmd + ["--output", str(output)]) == 0
    response = json.loads(capsys.readouterr().out)["data"]
    assert response["next_offset"] == 3 and len(response["items"]) == 3
    assert output.exists() and not (tmp_path / "genshin.db").exists()
    for protected in (path, query):
        assert main(cmd + ["--output", str(protected), "--overwrite"]) == 1
        assert json.loads(capsys.readouterr().err)["reason"] == "file_conflict"
    assert path.read_bytes() == before


def test_real_fresh_calculation_package_is_accepted_and_integrity_edit_rejected(tmp_path):
    from genshin_app.equipment.delivery import build_calculation_delivery
    template = {"version": 2, "form": "resolved", "id_registry_version": 1,
        "type_definitions": {"damage_types": []}, "meta": {"name": "审查工程样例"},
        "enemy": {"level": 90, "resistance": {"default": .1}},
        "characters": [{"id": "a", "name": "测试角色", "level": 90,
                        "stats": {"atk": 100, "crit_rate": 0, "crit_dmg": .5}}],
        "buffs": [], "switches": [{"t": 0, "character": "a"}], "loop_duration": 10,
        "hits": [{"id": "h1", "damage_type_ids": ["damage.elemental_skill"], "character": "a",
                  "skill": "skill", "hit_name": "测试战技", "t": 1, "multiplier": 1,
                  "damage_system": "classic", "element": "physical"}]}
    calculated = run_plan_calc(new_plan(template), tmp_path / "none.db", capture_display=True)
    built = build_calculation_delivery(calculated, input_file_sha="1" * 64)
    package = json.loads(built["artifacts"]["calculated"])
    result = review_calculation(package)
    assert result["validation"] == "full_calculation_package"
    assert result["items"][0]["expected"] == 45
    package["integrity"]["calculated_fingerprint"] = "0" * 64
    with pytest.raises(PlanError, match="完整性"):
        review_calculation(package)


def test_cli_budget_includes_file_receipt_before_publication(tmp_path, capsys, monkeypatch):
    from genshin_app import calculation_review
    from genshin_app.equipment.files import json_bytes
    value = _saved()
    size = len(json_bytes(review_calculation(value)))
    monkeypatch.setattr(calculation_review, "MAX_RESPONSE_BYTES", size)
    source, target = tmp_path / "source.json", tmp_path / "page.json"
    source.write_bytes(json_bytes(value))
    assert main(["equipment", "review", "--calculated", str(source), "--output", str(target)]) == 1
    assert json.loads(capsys.readouterr().err)["reason"] == "review_size_limit"
    assert not target.exists()
