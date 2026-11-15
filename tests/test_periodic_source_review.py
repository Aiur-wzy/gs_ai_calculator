"""Independent periodic graph cases use invented IDs and no formal database."""
from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import query_runtime
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_periodic_attack import periodic_db  # noqa: F401

# ruff: noqa: F811
ABILITY = "BinOutput/Ability/Temp/ReviewPeriodic.json"
GADGET = "BinOutput/Gadget/ReviewPeriodic.json"
TALENT = "BinOutput/Talent/AvatarTalents/ReviewPeriodic.json"
GADGET_EXCEL = "ExcelBinOutput/GadgetExcelConfigData.json"


def write(repo, path, value):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False), "utf8")


@pytest.fixture
def graph_db(tmp_path, fake_repo, stub_fetcher):
    """The unreviewed controls must remain pending, but exact graph facts exist."""
    write(fake_repo, ABILITY, [
        {"Default": {"abilityName": "ReviewCreator", "onAdded": [
            {"$type": "CreateGadget", "gadgetID": 401},
            {"$type": "CreateGadget", "gadgetID": 401}]}},
        {"Default": {"abilityName": "ReviewChild", "abilitySpecials": {"Damage": 0}}},
    ])
    write(fake_repo, GADGET, {"ReviewConfig": {"abilities": [{"abilityName": "ReviewChild"}]}})
    write(fake_repo, GADGET_EXCEL, [{"id": 401, "jsonName": "ReviewConfig"}])
    write(fake_repo, TALENT, {"ReviewQ": [{"$type": "ModifyAbility", "abilityName": "ReviewChild",
        "paramSpecial": "Damage", "paramDelta": "%1"}]})
    write(fake_repo, "ExcelBinOutput/ProudSkillExcelConfigData.json", [{
        "proudSkillId": 9100210, "proudSkillGroupId": 91002, "level": 10,
        "paramList": [2], "paramDescList": [], "openConfig": "ReviewQ"}])
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def query(db, **extra):
    return query_runtime(db, {"action": "periodic_attack_profile", "character_id": 90000001,
                              "slot": "burst", **extra})


def rebuild(db, repo, path, mutate):
    raw = json.loads((repo / path).read_text("utf8"))
    mutate(raw)
    write(repo, path, raw)
    fixture_builder.build_database(db.parent, force=True)


def test_review_two_creation_leaves_are_distinct_and_neither_is_auto_selected(graph_db):
    out = query(graph_db)
    found = out["creation_candidates"]
    assert len(found) == 2
    assert len({c["origin_reference_id"] for c in found}) == 2
    assert {c["gadget_id"] for c in found} == {401}
    assert {c["association"] for c in found} == {"parameter_related_only"}
    assert out["discovery"]["status"] == "selection_required"
    assert out["status"] == "pending" and out["selected_candidates"] == []
    assert out["action_creation_association"] == "unproven"
    assert out["dispatch_semantics_status"] == "not_proven"


def test_review_same_config_name_does_not_collapse_distinct_gadget_identity(graph_db, fake_repo):
    rebuild(graph_db, fake_repo, GADGET_EXCEL, lambda rows: rows.append({"id": 402, "jsonName": "ReviewConfig"}))
    rebuild(graph_db, fake_repo, ABILITY, lambda rows: rows[0]["Default"]["onAdded"][1].update(gadgetID=402))
    out = query(graph_db)
    assert {c["gadget_id"] for c in out["creation_candidates"]} == {401, 402}
    assert len(out["creation_candidates"]) == 2


def test_review_no_parameter_related_creation_is_distinct_from_an_ambiguous_chain(graph_db, fake_repo):
    rebuild(graph_db, fake_repo, GADGET, lambda data: data["ReviewConfig"].update(abilities=[]))
    out = query(graph_db)
    assert out["creation_candidates"] == []
    assert out["discovery"]["status"] == "no_parameter_related_creation"
    assert out["discovery"]["blocked_paths"] == []


def test_review_duplicate_mount_target_reports_all_ambiguous_identities_not_zero_data(graph_db, fake_repo):
    rebuild(graph_db, fake_repo, ABILITY, lambda rows: rows.append({"Alt": copy.deepcopy(rows[1]["Default"])}))
    out = query(graph_db)
    assert out["creation_candidates"] == []
    assert out["discovery"]["status"] == "no_fully_resolved_creation"
    blocked = out["discovery"]["blocked_paths"]
    assert blocked and any(len(p["candidate_ids"]) == 2 for p in blocked)
    assert out["status"] == "pending"


def test_review_origin_count_is_cumulative_across_two_leaves(graph_db, monkeypatch):
    from genshin_db.runtime_periodic_attack import PERIODIC_LIMITS

    monkeypatch.setitem(PERIODIC_LIMITS, "max_origins", 1)
    with pytest.raises(RuntimeConfigError) as error:
        query(graph_db)
    assert error.value.reason == "response_too_large"
    assert error.value.details["limit_kind"] == "origins"


def test_review_legacy_index_requires_upgrade_and_batch_keeps_later_result(graph_db, fake_repo, monkeypatch):
    from genshin_db import runtime_catalog, runtime_store
    from tests.test_runtime_object_index import build

    old = graph_db.parent / "old-v4.db"
    with monkeypatch.context() as patch:
        patch.setattr(runtime_catalog, "INDEXER_VERSION", "4")
        patch.setattr(runtime_store, "INDEXER_VERSION", "4")
        build(old, fake_repo)
    with closing(sqlite3.connect(old)) as conn, conn:
        conn.execute("DROP TABLE runtime_object_reference")
    before = old.read_bytes()
    with pytest.raises(RuntimeConfigError) as error:
        query(old)
    assert error.value.reason == "gadget_index_upgrade_required" and error.value.status == 409
    batch = query_runtime(old, {"action": "batch", "items": [
        {"id": "upgrade", "request": {"action": "periodic_attack_profile", "character_id": 90000001, "slot": "burst"}},
        {"id": "prior", "request": {"action": "action_facts", "character_id": 90000001, "slot": "burst"}},
    ]})
    assert batch["status"] == "partial"
    assert batch["items"][0]["error"]["reason"] == "gadget_index_upgrade_required"
    assert batch["items"][1]["status"] == "ok"
    assert old.read_bytes() == before


@pytest.mark.parametrize("update", [
    {"candidate_ids": ["candidate"]}, {"origin_reference_id": None},
    {"origin_reference_id": "A" * 64}, {"origin_reference_id": "a" * 64, "candidate_ids": []},
    {"origin_reference_id": "a" * 64, "candidate_ids": ["x", "x"]},
    {"origin_reference_id": "a" * 64, "candidate_ids": [True]},
    {"character_id": True}, {"gadget_id": 401}, {"prepared": {}},
])
def test_review_public_invalid_selection_never_opens_database(monkeypatch, tmp_path, update):
    import genshin_db.runtime_queries as runtime

    opened = []
    def forbidden(*args, **kwargs):
        opened.append(True)
        raise AssertionError("Invalid public request entered DB I/O")
    monkeypatch.setattr(runtime, "_read", forbidden)
    with pytest.raises((RuntimeConfigError, ValueError)):
        query(tmp_path / "must-not-create.db", **update)
    assert opened == []
    assert not (tmp_path / "must-not-create.db").exists()


def selected_inputs(db):
    found = query(db)
    origin = next(c["origin_reference_id"] for c in found["creation_candidates"] if c["gadget_id"] == 41002001)
    available = query(db, origin_reference_id=origin)
    return origin, [c["candidate_id"] for c in available["candidates"]]


def raw_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from raw_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from raw_objects(child)


@pytest.mark.parametrize("field,value", [
    ("JFLGPLCIOEC", .2500001), ("JFLGPLCIOEC", 0),
    ("KALBHJILPDA", .5), ("EMILGODPBIG", 2),
    ("IAFHAPFBBOB", "Elemental_Skill"), ("DBGOKAKAHFP", 24),
])
def test_review_partial_channel_mutations_retain_only_mode_optional_contexts(periodic_db, fake_repo, field, value):
    from genshin_db.runtime_periodic_attack import get_periodic_attack_context
    from tests.test_runtime_periodic_attack import SOURCE

    origin, candidates = selected_inputs(periodic_db)
    before = query(periodic_db, origin_reference_id=origin, candidate_ids=candidates)
    assert before["status"] == "ready"
    def mutate(rows):
        # Mutate only the exact periodic parent, not its separate blast sibling.
        parent = rows[2]["Default"]
        if field in ("KALBHJILPDA", "EMILGODPBIG", "DBGOKAKAHFP"):
            holders = [obj for obj in raw_objects(parent) if "KOLOHIDMHHE" in obj]
        elif field == "IAFHAPFBBOB":
            holders = [obj for obj in raw_objects(parent) if "EHKJIDFINEK" in obj]
        else:
            holders = [obj for obj in raw_objects(parent) if field in obj]
        assert len(holders) == 1
        holders[0][field] = value
    rebuild(periodic_db, fake_repo, SOURCE, mutate)
    # Raw source mutations produce a new snapshot and legacy reference ID.
    # The public candidate path identities themselves must remain stable.
    origin, fresh_candidates = selected_inputs(periodic_db)
    assert set(fresh_candidates) == set(candidates)
    context = get_periodic_attack_context(periodic_db, character_id=90000001, slot="burst",
        origin_reference_id=origin, candidate_ids=candidates)
    out = context.public_projection
    assert out["status"] == "pending"
    if field in ("DBGOKAKAHFP", "JFLGPLCIOEC"):
        # Unknown GU is optional for a reviewed no_application channel.  An
        # invalid native callback timer is likewise optional only when a
        # separately reviewed finite topology owns every occurrence time.
        # Both consumers still need the independently proven damage scalar;
        # the finite-topology case also keeps its independent ICD profile.
        assert context.selected_parameter_projections
        if field == "JFLGPLCIOEC":
            assert context.attenuation_profiles
    else:
        assert context.attenuation_profiles == {} and context.selected_parameter_projections == {}
    assert len(out["selected_candidates"]) == 2
    assert {c["status"] for c in out["selected_candidates"]} == {"pending", "ready"}
    assert out["source_fingerprint"] != before["source_fingerprint"]


def test_review_fresh_session_ignores_mutated_previous_context_and_reads_new_timer(periodic_db, fake_repo):
    from genshin_db.equipment_queries import equipment_read_session
    from genshin_db.runtime_periodic_attack import get_periodic_attack_context
    from tests.test_runtime_periodic_attack import SOURCE

    origin, candidates = selected_inputs(periodic_db)
    with equipment_read_session(periodic_db):
        old = get_periodic_attack_context(periodic_db, character_id=90000001, slot="burst",
            origin_reference_id=origin, candidate_ids=candidates)
        prior = old.public_projection["source_fingerprint"]
        old.public_projection["selected_candidates"].clear()
        old.parameter_context.clear()
    def mutate(rows):
        parent = rows[2]["Default"]
        holder = next(obj for obj in raw_objects(parent) if "JFLGPLCIOEC" in obj)
        holder["JFLGPLCIOEC"] = .125
    rebuild(periodic_db, fake_repo, SOURCE, mutate)
    origin, fresh_candidates = selected_inputs(periodic_db)
    assert set(fresh_candidates) == set(candidates)
    out = query(periodic_db, origin_reference_id=origin, candidate_ids=candidates)
    assert out["status"] == "ready"
    timer = next(c for c in out["selected_candidates"] if c["channel_kind"] == "periodic_callback_attack")["projection"]["timer"]
    assert timer["ticks"] == 125000 and timer["seconds"] == .125
    assert out["source_fingerprint"] != prior


def test_review_creation_bundle_full_nodes_share_global_source_node_limit(periodic_db, monkeypatch):
    from genshin_db.runtime_periodic_attack import PERIODIC_LIMITS

    origin, candidates = selected_inputs(periodic_db)
    out = query(periodic_db, origin_reference_id=origin, candidate_ids=candidates)
    graph = out["source_graph"]
    identities = {n["source"]["definition"]["definition_id"] for n in graph["nodes"]}
    identities.update(n["definition"]["definition_id"] for n in graph["creation_bundle"]["sources"])
    assert len(identities) > len(graph["nodes"])
    monkeypatch.setitem(PERIODIC_LIMITS, "max_source_nodes", len(identities))
    assert query(periodic_db, origin_reference_id=origin, candidate_ids=candidates)["status"] == "ready"
    monkeypatch.setitem(PERIODIC_LIMITS, "max_source_nodes", len(identities)-1)
    with pytest.raises(RuntimeConfigError) as error:
        query(periodic_db, origin_reference_id=origin, candidate_ids=candidates)
    assert error.value.reason == "response_too_large"
    assert error.value.details["limit_kind"] == "source_nodes"


def test_review_previously_seen_prefix_cannot_hide_unseen_edges_before_raw_decode(graph_db, monkeypatch):
    from genshin_db.runtime_gadget_source import _Reader
    from genshin_db.runtime_periodic_attack import PERIODIC_LIMITS, _PeriodicReader
    from genshin_db.runtime_queries import _read

    with _read(graph_db, "release") as (conn, state):
        parent = conn.execute("SELECT * FROM runtime_definition WHERE definition_kind='ability' AND exact_key_json=?",
                              ('"ReviewCreator"',)).fetchone()
        first = conn.execute("SELECT reference_id FROM runtime_object_reference WHERE version_line='release' AND from_definition_id=? LIMIT 1",
                             (parent["definition_id"],)).fetchone()[0]
        reader = _PeriodicReader(conn, state)
        reader.all_edges[("object", first)] = {}
        monkeypatch.setitem(PERIODIC_LIMITS, "max_edges", 1)
        monkeypatch.setattr(_Reader, "object_refs", lambda *args: pytest.fail("decoded raw parent before budget rejection"))
        with pytest.raises(RuntimeConfigError) as error:
            reader.object_refs(parent)
        assert error.value.reason == "response_too_large" and error.value.details["limit_kind"] == "edges"


def test_review_revisiting_same_exact_incoming_edges_does_not_spend_budget_twice(graph_db, monkeypatch):
    from genshin_db.runtime_periodic_attack import PERIODIC_LIMITS, _PeriodicReader
    from genshin_db.runtime_queries import _read

    with _read(graph_db, "release") as (conn, state):
        target = conn.execute("SELECT * FROM runtime_definition WHERE definition_kind='gadget_record' AND exact_key_json='401'").fetchone()
        reader = _PeriodicReader(conn, state)
        first = reader.incoming(target, "gadget_id_reference")
        assert len(first) == 2
        used = len(reader.all_edges)
        monkeypatch.setitem(PERIODIC_LIMITS, "max_edges", used)
        second = reader.incoming(target, "gadget_id_reference")
        assert second == first and len(reader.all_edges) == used


def test_review_second_unknown_parameter_writer_on_selected_ability_cannot_be_ignored(periodic_db, fake_repo):
    from tests.test_runtime_periodic_attack import TALENT as PERIODIC_TALENT

    def mutate(data):
        writer = copy.deepcopy(data["PeriodicFixtureQ"][0])
        writer["PEOJKHKHAKL"] = "UnknownPotentialControl"
        data["PeriodicFixtureQ"].append(writer)
    rebuild(periodic_db, fake_repo, PERIODIC_TALENT, mutate)
    origin, candidates = selected_inputs(periodic_db)
    out = query(periodic_db, origin_reference_id=origin, candidate_ids=candidates)
    assert out["status"] == "pending"
    assert any(c["status"] == "pending" for c in out["selected_candidates"])
