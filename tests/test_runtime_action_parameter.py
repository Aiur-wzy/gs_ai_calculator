"""The scalar projection proves a parameter, not its gameplay interpretation."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest
from test_runtime_action_facts import (  # noqa: F401
    action_db,
    facts,
    fake_repo,
    stub_fetcher,
)

from genshin_db.runtime_action_parameter import (
    TEXT_MAP_PATH,
    get_action_parameter_context,
    project_action_parameter,
)
from genshin_db.runtime_catalog import RuntimeConfigError, fingerprint
from genshin_db.runtime_queries import query_runtime
from tests import fixture_candidate_builder as fixture_builder

# ruff: noqa: F811


def scalar(db, **extra):
    return query_runtime(db, {"action": "action_parameter", "character_id": 90000001,
                             "slot": "normal", "param_index": 0, **extra})


def test_exact_scalar_and_source_do_not_claim_dispatch(action_db):
    out = scalar(action_db)
    assert out["status"] == out["scalar_projection"]["status"] == "ready"
    assert out["scalar_projection"]["value"] == .5
    assert out["source"]["parameter_pointer"] == "/0/paramList/0"
    assert out["source"]["node_sha256"] == out["source"]["definition"]["node_sha256"]
    assert set(out["entry_sources"]) == {"avatar", "depot", "skill"}
    assert out["action_dispatch"]["status"] == "pending"
    assert out["operation_execution"] == out["gameplay_interpretation"] == "not_evaluated"


@pytest.mark.parametrize(("selection", "issue"), [
    ({"constellation": 1}, "effective_talent_level_required"),
    ({"talent_level": 9}, "missing_parameter_record"),
])
def test_missing_exact_effective_level_rows_remain_pending(action_db, selection, issue):
    out = scalar(action_db, **selection)
    assert out["scalar_projection"]["status"] == "pending"
    assert any(row["code"] == issue for row in out["scalar_projection"]["issues"])


@pytest.mark.parametrize("level", [10, 13])
def test_explicit_effective_level_allows_nonzero_constellation_without_inference(action_db, level):
    out = scalar(action_db, constellation=1, talent_level=level)
    assert out["scalar_projection"]["status"] == "ready"
    assert out["level"]["effective_level"] == level
    assert out["level"]["source"] == "explicit_effective_level"
    assert out["level"]["constellation_applied"] is False


def test_other_action_facts_pending_does_not_make_known_scalar_disappear(action_db):
    result = facts(action_db)
    result.update(status="pending", issues=[{"code": "unknown_operation"}])
    before = copy.deepcopy(result)
    out = project_action_parameter(result, 0)
    assert out["scalar_projection"]["status"] == "ready"
    assert out["action_facts_status"] == "pending" and out["action_facts_issues"] == result["issues"]
    assert result == before


@pytest.mark.parametrize("value", [True, None, "0.5", {"expression": "x"}])
def test_non_numeric_input_cannot_become_a_scalar(action_db, value):
    result = facts(action_db)
    result["records"]["proud_skill"]["fields"]["paramList"][0] = value
    out = project_action_parameter(result, 0)
    assert out["status"] == "pending" and out["scalar_projection"]["value"] is None


def test_missing_parameter_index_is_explicit(action_db):
    out = scalar(action_db, param_index=1)
    assert out["status"] == "pending"
    assert out["scalar_projection"]["issues"][0]["code"] == "parameter_index_missing"


@pytest.mark.parametrize("index", [True, -1, 64, .5, "0", None])
def test_invalid_index_rejected(action_db, index):
    with pytest.raises(ValueError):
        scalar(action_db, param_index=index)


def test_query_missing_selection_fields_is_public_validation_error(action_db):
    for request in ({"action": "action_parameter"}, {"action": "action_parameter", "param_index": 0}):
        with pytest.raises(ValueError):
            query_runtime(action_db, request)


def descriptions_db(action_db, fake_repo, rows, *, params=None):
    path = fake_repo / "ExcelBinOutput/ProudSkillExcelConfigData.json"
    proud = json.loads(path.read_text("utf8"))
    for row in proud:
        row["paramDescList"] = list(rows)
        if params is not None:
            row["paramList"] = params
    path.write_text(json.dumps(proud), "utf8")
    path = fake_repo / TEXT_MAP_PATH
    texts = json.loads(path.read_text("utf8"))
    for key, text in rows.items():
        if text is not None:
            texts[str(key)] = text
        else:
            texts.pop(str(key), None)
    path.write_text(json.dumps(texts, ensure_ascii=False), "utf8")
    fixture_builder.build_database(action_db.parent, force=True)


def test_description_preserves_sentence_and_exact_source_evidence(action_db, fake_repo):
    sentence = "治疗量|{param1:F1P}生命值上限+{param2:I}"
    descriptions_db(action_db, fake_repo, {8001: sentence, 8002: "波纹伤害|{param3:P}"}, params=[.0792, 932.22, 1.965])
    before = hashlib.sha256(action_db.read_bytes()).hexdigest()
    first, second, third = (scalar(action_db, param_index=i) for i in range(3))
    assert first["parameter_descriptions"]["status"] == "ready"
    matched = first["parameter_descriptions"]["matched"]
    assert matched == second["parameter_descriptions"]["matched"]
    assert len(matched) == 1 and matched[0]["text"] == sentence
    assert matched[0]["param_indices"] == [0, 1]
    assert matched[0]["text_map_hash"] == 8001
    assert matched[0]["text_origin"] == {
        "source_path": TEXT_MAP_PATH, "json_pointer": "/8001",
        "source_sha256": hashlib.sha256((fake_repo / TEXT_MAP_PATH).read_bytes()).hexdigest(),
        "node_sha256": fingerprint(sentence),
    }
    reference = matched[0]["parameter_description_origin"]
    assert reference["json_pointer"] == "/0/paramDescList/0"
    assert reference["source_path"] == "ExcelBinOutput/ProudSkillExcelConfigData.json"
    assert reference["source_sha256"] == first["source"]["source_sha256"]
    assert reference["node_sha256"] == fingerprint(8001)
    assert third["parameter_descriptions"]["matched"][0]["text"] == "波纹伤害|{param3:P}"
    assert third["parameter_descriptions"]["matched"][0]["parameter_description_origin"]["json_pointer"] == "/0/paramDescList/1"
    assert hashlib.sha256(action_db.read_bytes()).hexdigest() == before


def test_exact_tokens_do_not_confuse_param1_with_param10_or_plain_text(action_db, fake_repo):
    descriptions_db(action_db, fake_repo, {
        8001: "有效无格式{param1}与带格式{param1:F2}",
        8002: "其他{param10:P}，非token param1、{param01:P}、{param1x:P}、{param1:}",
        8003: "{Param1:P}，{param1:P 未闭合",
    }, params=list(range(10)))
    out = scalar(action_db)
    assert [row["text_map_hash"] for row in out["parameter_descriptions"]["matched"]] == [8001]
    ten = scalar(action_db, param_index=9)
    assert [row["text_map_hash"] for row in ten["parameter_descriptions"]["matched"]] == [8002]


def test_all_matching_descriptions_kept_in_original_order(action_db, fake_repo):
    descriptions_db(action_db, fake_repo, {8010: "普通攻击|{param1:P}", 8001: "追加伤害|{param1:P}"})
    matches = scalar(action_db)["parameter_descriptions"]["matched"]
    assert [row["text_map_hash"] for row in matches] == [8010, 8001]


def test_missing_text_is_explicit_and_does_not_erase_scalar(action_db, fake_repo):
    descriptions_db(action_db, fake_repo, {8001: "技能伤害|{param1:P}", 8002: None, 0: None})
    out = scalar(action_db)
    assert out["status"] == out["scalar_projection"]["status"] == "ready"
    assert out["parameter_descriptions"]["status"] == "partial"
    unresolved = out["parameter_descriptions"]["unresolved"]
    assert len(unresolved) == 1 and unresolved[0]["text_map_hash"] == 8002
    assert unresolved[0]["code"] == "missing_description_text"
    assert unresolved[0]["parameter_description_origin"]["json_pointer"] == "/0/paramDescList/1"


@pytest.mark.parametrize("text,code", [(None, "missing_description_text"), ("", "missing_description_text")])
def test_all_text_missing_or_invalid_reports_pending_description(action_db, fake_repo, text, code):
    descriptions_db(action_db, fake_repo, {8001: text})
    out = scalar(action_db)
    assert out["parameter_descriptions"]["status"] == "pending"
    assert out["parameter_descriptions"]["unresolved"][0]["code"] == code
    assert out["scalar_projection"]["value"] == .5
    assert out["gameplay_interpretation"] == "not_evaluated"


def test_invalid_text_node_is_explicit_even_if_read_source_is_mutated(action_db, monkeypatch):
    from genshin_db.runtime_queries import _Nodes

    source = _Nodes.source

    def mutated_source(self, path):
        row, data = source(self, path)
        if path == TEXT_MAP_PATH:
            data = {**data, "201": 12}
        return row, data

    monkeypatch.setattr(_Nodes, "source", mutated_source)
    out = scalar(action_db)
    assert out["parameter_descriptions"]["status"] == "pending"
    assert out["parameter_descriptions"]["unresolved"][0]["code"] == "invalid_description_text"
    assert out["scalar_projection"]["value"] == .5


def test_known_description_without_selected_token_is_not_guessed(action_db, fake_repo):
    descriptions_db(action_db, fake_repo, {8001: "冷却时间|{param2}秒"})
    out = scalar(action_db)
    assert out["parameter_descriptions"]["status"] == "pending"
    assert out["parameter_descriptions"]["matched"] == []
    assert out["parameter_descriptions"]["issues"] == [
        {"code": "no_matching_parameter_description", "parameter_token": "param1"}]


def test_facts_only_projection_marks_description_context_unavailable(action_db):
    out = project_action_parameter(facts(action_db), 0)
    assert out["scalar_projection"]["status"] == "ready"
    assert out["parameter_descriptions"]["issues"] == [{"code": "description_context_unavailable"}]


def test_action_context_reuses_single_connection_and_loads_text_map_once(action_db, monkeypatch):
    from genshin_db import equipment_queries, runtime_queries

    connect, source = equipment_queries.sqlite3.connect, runtime_queries._Nodes.source
    connections, text_calls = [], []

    def counted_connect(*args, **kwargs):
        result = connect(*args, **kwargs)
        connections.append(result)
        return result

    def counted_source(self, path):
        if path == TEXT_MAP_PATH:
            text_calls.append(self.conn)
        return source(self, path)

    monkeypatch.setattr(equipment_queries.sqlite3, "connect", counted_connect)
    monkeypatch.setattr(runtime_queries._Nodes, "source", counted_source)
    context = get_action_parameter_context(action_db, character_id=90000001, slot="normal")
    assert len(connections) == len(text_calls) == 1
    assert text_calls[0].connection is connections[0]
    before = copy.deepcopy(context)
    out = project_action_parameter(context, 0)
    assert out["parameter_descriptions"]["matched"][0]["text"] == "造成{param1:F1P}伤害"
    out["parameter_descriptions"]["matched"][0]["text"] = "不能污染上下文"
    project_action_parameter(context, 1)
    assert context == before
    assert len(connections) == len(text_calls) == 1


def test_description_context_honors_expected_snapshot(action_db):
    with pytest.raises(RuntimeConfigError) as exc:
        get_action_parameter_context(action_db, character_id=90000001, slot="normal", snapshot_id="stale")
    assert exc.value.reason == "snapshot_changed"


def test_description_changes_affect_content_fingerprint(action_db, fake_repo):
    old = scalar(action_db)
    descriptions_db(action_db, fake_repo, {201: "倍率|{param1:P}"})
    new = scalar(action_db)
    assert old["scalar_projection"] == new["scalar_projection"]
    assert old["content_fingerprint"] != new["content_fingerprint"]


def test_description_output_limits_never_silently_truncate(action_db, fake_repo):
    descriptions_db(action_db, fake_repo, {8001: "{param1:P}" + "原文" * 11000})
    with pytest.raises(RuntimeConfigError) as exc:
        scalar(action_db)
    assert exc.value.reason == "response_too_large"


def test_description_count_bound_is_explicit(action_db, fake_repo):
    descriptions_db(action_db, fake_repo, {8000 + i: "{param1:P}" for i in range(65)})
    with pytest.raises(RuntimeConfigError) as exc:
        scalar(action_db)
    assert exc.value.reason == "response_too_large"
