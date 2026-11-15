"""D02 HTTP adapter: real socket, strict parameters and unchanged legacy envelopes."""

import json
import sqlite3
import threading
import urllib.error
import urllib.request

import pytest

from genshin_db import queries
from genshin_db.equipment import EquipmentError
from genshin_db.service import EQUIPMENT_ENDPOINTS, GenshinHTTPServer
from tests.test_equipment import equipment_db  # noqa: F401
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401


@pytest.fixture(scope="module")
def http_server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("equipment-http")
    with sqlite3.connect(data_dir / "genshin.db") as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)")
        conn.execute("INSERT INTO meta VALUES ('game_version','test')")
        conn.execute("CREATE TABLE character (id INTEGER,version_line TEXT)")
        conn.execute("INSERT INTO character VALUES (1,'release')")
    srv = GenshinHTTPServer(data_dir, port=0)
    worker = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    worker.start()
    yield srv
    srv.shutdown()
    worker.join(timeout=3)
    srv.server_close()


def request(srv, path, *, method="GET", body=None, token=False):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(srv.base_url + path, data=payload, method=method)
    if token:
        req.add_header("X-Local-Token", srv.token)
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read().decode("utf-8")), response.headers


@pytest.mark.parametrize("path,function,object_id,params", [
    ("/v1/weapons", "list_weapons", None,
     {"version_line": "release", "page": 1, "page_size": 20, "catalog_scope": "codex", "name": ""}),
    ("/v1/weapons/11401", "get_weapon_detail", 11401, {"version_line": "release"}),
    ("/v1/weapons/11401/stats?level=1&ascension=0", "get_weapon_stats", 11401,
     {"version_line": "release", "level": 1, "ascension": 0}),
    ("/v1/artifact-sets", "list_artifact_sets", None,
     {"version_line": "release", "page": 1, "page_size": 20, "catalog_scope": "codex", "name": ""}),
    ("/v1/artifact-sets/15001?version_line=beta", "get_artifact_set_detail", 15001, {"version_line": "beta"}),
    ("/v1/artifacts?set_id=&equip_type=&rarity=&catalog_scope=all", "list_artifacts", None,
     {"version_line": "release", "page": 1, "page_size": 20, "catalog_scope": "all", "name": "",
      "set_id": None, "equip_type": None, "rarity": None}),
    ("/v1/artifacts/71543", "get_artifact_detail", 71543, {"version_line": "release"}),
    ("/v1/artifacts/71543/main-stats?level=0&main_prop_id=14001", "get_artifact_main_stats", 71543,
     {"version_line": "release", "level": 0, "main_prop_id": 14001}),
    ("/v1/artifact-main-props?depot_id=4000", "list_artifact_main_props", None,
     {"version_line": "release", "page": 1, "page_size": 20, "depot_id": 4000}),
    ("/v1/artifact-substats?depot_id=501&prop_type=FIGHT_PROP_HP&page=2", "list_artifact_substats", None,
     {"version_line": "release", "page": 2, "page_size": 20, "depot_id": 501, "prop_type": "FIGHT_PROP_HP"}),
    ("/v1/equip-affixes/1135010", "get_equip_affix_detail", 1135010, {"version_line": "release"}),
])
def test_all_eleven_routes_call_shared_query_with_exact_types(http_server, monkeypatch, path, function, object_id, params):
    calls = []

    def query(db, *ids, **kwargs):
        calls.append((db, ids, kwargs))
        return {"sentinel": function, "data_version": {"upstream_commit": "fixture"}}

    monkeypatch.setattr(queries, function, query, raising=False)
    status, body, headers = request(http_server, path)
    assert status == 200 and body["code"] == 0 and body["data"]["sentinel"] == function
    assert calls == [(http_server.db_path, () if object_id is None else (object_id,), params)]
    assert headers["Content-Type"] == "application/json; charset=utf-8"


@pytest.mark.parametrize("path", [
    "/v1/weapons?name=x&name=y", "/v1/weapons?typo=1", "/v1/weapons?rarity=0",
    "/v1/weapons?rarity=6", "/v1/weapons?rarity=1.0", "/v1/weapons?rarity=true",
    "/v1/weapons?weapon_type=AXE", "/v1/artifacts?equip_type=FLOWER",
    "/v1/artifacts?set_id=0", "/v1/artifact-sets?catalog_scope=owned",
    "/v1/weapons?page=0", "/v1/weapons?page=-1", "/v1/weapons?page=1.5",
    "/v1/weapons?page=1e2", "/v1/weapons?page=true", "/v1/weapons?page=",
    "/v1/weapons?page_size=0", "/v1/weapons?page_size=-1",
    "/v1/weapons?page=9007199254740992", "/v1/weapons?version_line=missing",
    "/v1/weapons/1?level=90", "/v1/weapons/0", "/v1/weapons/-1",
    "/v1/weapons/1.0", "/v1/weapons/true", "/v1/weapons/9007199254740992",
    "/v1/weapons/11401/stats", "/v1/weapons/11401/stats?level=1",
    "/v1/weapons/11401/stats?ascension=0", "/v1/weapons/11401/stats?level=0&ascension=0",
    "/v1/weapons/11401/stats?level=1&ascension=-1", "/v1/weapons/11401/stats?level=1&ascension=0.0",
    "/v1/artifacts/71543/main-stats?level=0", "/v1/artifacts/71543/main-stats?main_prop_id=14001",
    "/v1/artifacts/71543/main-stats?level=-1&main_prop_id=14001",
    "/v1/artifact-main-props", "/v1/artifact-main-props?depot_id=0",
    "/v1/artifact-substats?depot_id=501&prop_type=TYPO",
    "/v1/artifact-substats?depot_id=501&level=20", "/v1/equip-affixes/1135010?refinement=1",
])
def test_invalid_parameters_are_400_before_dataset_lookup(http_server, monkeypatch, path):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid HTTP parameter reached query layer")

    for spec in EQUIPMENT_ENDPOINTS:
        monkeypatch.setattr(queries, spec["query"], forbidden, raising=False)
    status, body, _ = request(http_server, path)
    assert status == 400 and body["code"] == 400 and body["reason"] == "invalid_parameter"
    assert body["error"]


@pytest.mark.parametrize("reason,status", [
    ("invalid_equipment_state", 400), ("not_found", 404), ("dataset_unavailable", 409),
    ("unsupported_schema", 409), ("dataset_invalid", 500),
])
def test_shared_domain_errors_preserve_status_reason_details(http_server, monkeypatch, reason, status):
    def query(*args, **kwargs):
        raise EquipmentError("fixture reason", reason, status, {"field": "level", "state": "fixture"})

    monkeypatch.setattr(queries, "get_weapon_detail", query, raising=False)
    actual, body, _ = request(http_server, "/v1/weapons/11401")
    assert actual == status and body == {"code": status, "error": "fixture reason", "reason": reason,
                                        "details": {"field": "level", "state": "fixture"}}


def test_valid_source_missing_value_remains_200_and_null(http_server, monkeypatch):
    data = {"level": 0, "stat": {"value": None, "value_status": "source_missing", "unit": "flat"},
            "warnings": [{"code": "source_missing"}], "data_version": {"version_line": "release"}}
    monkeypatch.setattr(queries, "get_artifact_main_stats", lambda *args, **kwargs: data, raising=False)
    status, body, _ = request(http_server, "/v1/artifacts/71543/main-stats?level=0&main_prop_id=14001")
    assert status == 200 and body == {"code": 0, "data": data}


def test_caps_describe_real_routes_strict_fields_units_and_states(http_server):
    status, body, _ = request(http_server, "/v1/meta/capabilities")
    assert status == 200
    caps = body["data"]
    equipment = caps["equipment"]
    assert len(equipment["endpoints"]) == 11
    assert all(e in caps["endpoints"] for e in equipment["endpoints"])
    assert equipment["supported_schema_versions"] == [1]
    assert equipment["properties"]["FIGHT_PROP_CRITICAL"]["unit"] == "ratio"
    assert equipment["annotation_targets"] == ["weapon", "artifact_set", "artifact", "equip_affix"]
    params = next(e for e in equipment["endpoints"] if e["query"] == "get_artifact_main_stats")["params"]
    assert params["level"]["minimum"] == 0 and params["level"]["required"] is True
    assert params["main_prop_id"]["required"] is True
    assert "default" not in params["level"]
    assert equipment["state_paths"]["update_status"] == "data.meta.datasets.equipment"


def test_new_read_routes_remain_token_free_and_wrong_method_is_405(http_server, monkeypatch):
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *a, **kw: {}, raising=False)
    assert request(http_server, "/v1/weapons/11401")[0] == 200
    status, body, headers = request(http_server, "/v1/weapons/11401", method="POST", body={})
    assert status == 405 and body["code"] == 405 and headers["Allow"] == "GET"
    assert request(http_server, "/v1/weapons/11401/nope")[0] == 404
    status, body, _ = request(http_server, "/v1/annotations", method="POST", body={})
    assert status == 401 and "reason" not in body


def test_legacy_bad_input_envelope_is_unchanged(http_server):
    status, body, _ = request(http_server, "/v1/characters?page=0")
    assert status == 400 and set(body) == {"code", "error"}


def test_update_endpoint_is_deprecated_and_cannot_call_legacy_pipeline(http_server):
    status, body, _ = request(http_server, "/v1/db/update", method="POST", body={}, token=True)
    assert status == 410
    assert body["reason"] == "accepted_database_is_immutable"
    assert body["details"]["active_database_modified"] is False
    assert http_server.updating is False


def test_update_busy_reason_and_status_meta_location(http_server, monkeypatch):
    meta = {"game_version": "test", "datasets": {"equipment": {"lines": {"release": {"state": "missing"}}}}}
    monkeypatch.setattr(queries, "get_meta", lambda *args: meta)
    assert request(http_server, "/v1/meta/version")[1]["data"] == meta
    status = request(http_server, "/v1/db/update/status")[1]["data"]
    assert status["meta"] == meta
    http_server.update_mutex.acquire()
    try:
        status, body, _ = request(http_server, "/v1/db/update", method="POST", body={}, token=True)
        assert status == 410 and body["reason"] == "accepted_database_is_immutable"
    finally:
        http_server.update_mutex.release()


@pytest.fixture
def real_equipment_server(request):
    db = request.getfixturevalue("equipment_db")
    srv = GenshinHTTPServer(db.parent, port=0)
    worker = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    worker.start()
    yield srv
    srv.shutdown()
    worker.join(timeout=3)
    srv.server_close()


def test_all_eleven_http_queries_with_real_shared_fixture(real_equipment_server):
    srv = real_equipment_server
    paths = ["/v1/weapons", "/v1/weapons/11401", "/v1/weapons/11401/stats?level=20&ascension=0",
             "/v1/artifact-sets", "/v1/artifact-sets/15", "/v1/artifacts", "/v1/artifacts/71543",
             "/v1/artifacts/71543/main-stats?level=0&main_prop_id=14001",
             "/v1/artifact-main-props?depot_id=4000", "/v1/artifact-substats?depot_id=501",
             "/v1/equip-affixes/917"]
    data = []
    for path in paths:
        status, body, _ = request(srv, path)
        assert status == 200, (path, body)
        data.append(body["data"])
    assert all(d["data_version"] == data[0]["data_version"] for d in data)
    assert data[0]["total"] == 1
    assert data[2]["properties"][0]["value"] == 120
    assert data[7]["level"] == 0 and data[7]["stat"]["value"] == 701
    assert data[10]["effect"]["param_list"] == [0.2, 0, 2, 0]
    assert {ref["kind"] for ref in data[10]["context_refs"]} == {"weapon", "artifact_set"}
    assert data[10]["effect"]["desc_source"]["record_id"] == 917
    assert request(srv, "/v1/weapons?name=%25")[1]["data"]["items"] == []
    page = request(srv, "/v1/weapons?page=2&page_size=1")[1]["data"]
    assert page["items"] == [] and page["total"] == 1
    assert request(srv, "/v1/weapons?page_size=999")[1]["data"]["page_size"] == 100
    state = request(srv, "/v1/meta/version")[1]["data"]["datasets"]["equipment"]["lines"]
    assert state["release"]["state"] == "ready" and state["beta"]["state"] == "missing"


@pytest.mark.parametrize("path,status,reason", [
    ("/v1/weapons/999", 404, "not_found"),
    ("/v1/artifact-main-props?depot_id=999", 404, "not_found"),
    ("/v1/weapons/11401?version_line=beta", 409, "dataset_unavailable"),
    ("/v1/weapons/11401/stats?level=19&ascension=1", 400, "invalid_equipment_state"),
    ("/v1/artifacts/71543/main-stats?level=21&main_prop_id=14001", 400, "invalid_equipment_state"),
    ("/v1/artifacts/71543/main-stats?level=0&main_prop_id=999", 400, "invalid_equipment_state"),
])
def test_real_query_error_matrix(real_equipment_server, path, status, reason):
    actual, body, _ = request(real_equipment_server, path)
    assert actual == status and body["code"] == status and body["reason"] == reason


def test_real_legal_missing_value_preserved(real_equipment_server):
    status, body, _ = request(real_equipment_server, "/v1/artifacts/71543/main-stats?level=0&main_prop_id=14002")
    assert status == 200
    assert body["data"]["stat"]["value"] is None
    assert body["data"]["stat"]["value_status"] == "source_missing"


def test_old_database_reports_missing_before_object_lookup(http_server):
    status, body, _ = request(http_server, "/v1/weapons/999")
    assert status == 409 and body["reason"] == "dataset_unavailable"
    assert request(http_server, "/v1/meta/version")[1]["data"]["game_version"] == "test"


@pytest.mark.parametrize("sql,status,reason", [
    ("UPDATE meta SET value='99' WHERE key='equipment_schema_version'", 409, "unsupported_schema"),
    ("DROP TABLE weapon_property", 500, "dataset_invalid"),
])
def test_real_corrupt_and_newer_schema_states(real_equipment_server, sql, status, reason):
    conn = sqlite3.connect(real_equipment_server.db_path)
    try:
        with conn:
            conn.execute(sql)
    finally:
        conn.close()
    actual, body, _ = request(real_equipment_server, "/v1/weapons")
    assert actual == status and body["reason"] == reason


def test_equipment_annotations_auth_status_and_line_are_not_conflated(real_equipment_server):
    srv = real_equipment_server
    payload = {"target_type": "equip_affix", "target_id": 917, "content": "仅此效果的人工机制",
               "version_line": "release"}
    assert request(srv, "/v1/annotations", method="POST", body=payload)[0] == 401
    status, body, _ = request(srv, "/v1/annotations", method="POST", body=payload, token=True)
    assert status == 410 and body["reason"] == "accepted_database_is_immutable"
    assert request(srv, "/v1/equip-affixes/917")[1]["data"]["effect"]["annotations"] == []
    payload["version_line"] = "beta"
    assert request(srv, "/v1/annotations", method="POST", body=payload, token=True)[0] == 410
    assert request(srv, "/v1/artifacts/71543")[1]["data"]["annotations"] == []
