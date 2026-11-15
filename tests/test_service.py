"""db-service 测试：真实 HTTP 服务（端口 0）+ 假数据库验证端点行为。"""

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from urllib.parse import quote

import pytest

from genshin_db.annotation_store import create_annotation_tables
from genshin_db.extractor import build_curated_layer
from genshin_db.raw_store import build_raw_layer
from genshin_db.service import GenshinHTTPServer
from tests.equipment_fixtures import equipment_files


@pytest.fixture
def server(tmp_path):
    """用 fake 上游建库（借用 test_extractor 的假数据），起真实服务。"""
    from tests.test_extractor import (
        FAKE_CURVE,
        FAKE_PROMOTE,
        TEXTS,
    )

    repo = tmp_path / "repo"
    (repo / "ExcelBinOutput").mkdir(parents=True)
    (repo / "TextMap").mkdir()
    texts = TEXTS
    files = {
        "AvatarExcelConfigData": [{
            "id": 90000001, "nameTextMapHash": 100, "descTextMapHash": 101,
            "qualityType": "QUALITY_ORANGE", "weaponType": "WEAPON_SWORD_ONE_HAND",
            "hpBase": 12000.0, "attackBase": 300.0, "defenseBase": 700.0,
            "critical": 0.05, "criticalHurt": 0.5, "chargeEfficiency": 1.0,
            "propGrowCurves": [
                {"type": "FIGHT_PROP_BASE_HP", "growCurve": "GROW_CURVE_HP_S5"},
                {"type": "FIGHT_PROP_BASE_ATTACK", "growCurve": "GROW_CURVE_ATTACK_S5"},
                {"type": "FIGHT_PROP_BASE_DEFENSE", "growCurve": "GROW_CURVE_DEFENSE_S5"},
            ],
            "skillDepotId": 9001, "avatarPromoteId": 900,
        }],
        "AvatarSkillExcelConfigData": [
            {"id": 90011, "nameTextMapHash": 200, "descTextMapHash": 201,
             "proudSkillGroupId": 91001},
            {"id": 90013, "nameTextMapHash": 220, "descTextMapHash": 221,
             "cdTime": 15, "costElemVal": 60, "costElemType": "Rock",
             "proudSkillGroupId": 91003},
        ],
        "AvatarTalentExcelConfigData": [
            {"talentId": 9001, "nameTextMapHash": 300, "descTextMapHash": 301,
             "paramList": [0.1], "addProps": []},
        ],
        "AvatarSkillDepotExcelConfigData": [{
            "id": 9001, "skills": [90011, 0, 0, 0], "subSkills": [],
            "inherentProudSkillOpens": [], "energySkill": 90013,
            "talents": [9001, 0, 0, 0, 0, 0],
        }],
        "ProudSkillExcelConfigData": [
            {"proudSkillGroupId": 91001, "level": 1, "paramList": [0.5]},
            {"proudSkillGroupId": 91003, "level": 10, "paramList": [3.0]},
            {"proudSkillGroupId": 91003, "level": 13, "paramList": [3.5]},
        ],
        "AvatarCurveExcelConfigData": FAKE_CURVE,
        "AvatarPromoteExcelConfigData": FAKE_PROMOTE,
        "FetterInfoExcelConfigData": [],
    }
    for name, data in files.items():
        (repo / "ExcelBinOutput" / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    (repo / "TextMap" / "TextMap_MediumCHS.json").write_text(
        json.dumps(texts, ensure_ascii=False), encoding="utf-8"
    )
    for rel, data in equipment_files().items():
        (repo / rel).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    build_db = data_dir / "genshin.db"
    build_raw_layer(repo, build_db)
    build_curated_layer(build_db, "7.0", "release")
    # 候选构建阶段预建知识表；服务启动本身不得再修改活动库。
    create_annotation_tables(build_db)
    conn = sqlite3.connect(build_db)
    with conn:
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('upstream_commit', 'abc123')")
    conn.close()

    srv = GenshinHTTPServer(data_dir, port=0)
    srv.write_service_info()
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.remove_service_info()
    srv.server_close()


def _get(srv, path, token=None):
    req = urllib.request.Request(srv.base_url + path)
    if token:
        req.add_header("X-Local-Token", token)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _post(srv, path, body, token=None):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(srv.base_url + path, data=data, method="POST")
    if token:
        req.add_header("X-Local-Token", token)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_meta_version(server):
    status, body = _get(server, "/v1/meta/version")
    assert status == 200 and body["code"] == 0
    assert body["data"]["game_version"] == "7.0"
    assert body["data"]["upstream_commit"] == "abc123"


def test_characters_list_and_filter(server):
    status, body = _get(server, "/v1/characters")
    assert status == 200
    assert body["data"]["total"] == 1
    item = body["data"]["items"][0]
    assert item["name"] == "测试角色" and item["atk_90"] == 300.0 * 2.0 + 40.0

    _, body2 = _get(server, f"/v1/characters?name={quote('测试')}")
    assert body2["data"]["total"] == 1
    _, body3 = _get(server, f"/v1/characters?element={quote('火')}")
    assert body3["data"]["total"] == 0


def test_character_detail_aggregate(server):
    status, body = _get(server, "/v1/characters/90000001")
    assert status == 200
    d = body["data"]
    assert d["character"]["name"] == "测试角色"
    assert d["character"]["ascension_prop"] == "暴击伤害"
    assert len(d["skills"]) == 2
    burst = next(s for s in d["skills"] if s["slot"] == "burst")
    assert burst["params"]["10"]["param_list"] == [3.0]
    assert burst["params"]["13"]["param_list"] == [3.5]
    assert d["talents"][0]["name"] == "一命·测试"


def test_character_not_found(server):
    status, body = _get(server, "/v1/characters/99999999")
    assert status == 404 and body["code"] == 404


def test_unknown_path_404(server):
    status, _ = _get(server, "/v1/nope")
    assert status == 404


def test_annotation_submit_requires_token(server):
    # 无令牌 → 401
    status, body = _post(server, "/v1/annotations", {
        "target_type": "character", "target_id": 90000001,
        "category": "ICD", "title": "测试", "content": "内容",
    })
    assert status == 401
    # 有令牌也不能修改已接纳数据库；应引导到变更提案/完整版本发布。
    status, body = _post(server, "/v1/annotations", {
        "target_type": "character", "target_id": 90000001,
        "category": "ICD", "title": "测试", "content": "内容",
    }, token=server.token)
    assert status == 410
    assert body["reason"] == "accepted_database_is_immutable"
    assert body["details"]["active_database_modified"] is False
    _, q = _get(server, "/v1/annotations")
    assert q["data"]["total"] == 0
    _, q2 = _get(server, "/v1/annotations?status=draft")
    assert q2["data"]["total"] == 0


def test_service_info_written(server, tmp_path):
    info = json.loads((server.data_dir / "service.json").read_text(encoding="utf-8"))
    assert info["base_url"] == server.base_url
    assert info["port"] == server.server_address[1]


def test_meta_capabilities(server):
    status, body = _get(server, "/v1/meta/capabilities")
    assert status == 200 and body["code"] == 0
    paths = [e["path"] for e in body["data"]["endpoints"]]
    assert "/v1/characters/{id}" in paths and "/v1/search" in paths


def test_meta_version_lines(server):
    _, body = _get(server, "/v1/meta/version")
    assert body["data"]["version_lines"] == ["release"]


def test_version_line_filter(server):
    _, body = _get(server, "/v1/characters?version_line=beta")
    assert body["data"]["total"] == 0
    status, _ = _get(server, "/v1/characters/90000001?version_line=beta")
    assert status == 404


def test_skill_detail(server):
    status, body = _get(server, "/v1/skills/90013")
    assert status == 200
    item = body["data"]["items"][0]
    assert item["name"] == "测试爆发"
    assert item["cooldown"] == 15 and item["energy_cost"] == 60
    assert item["params"]["13"]["param_list"] == [3.5]

    _, body2 = _get(server, "/v1/skills/90013?character_id=90000001")
    assert len(body2["data"]["items"]) == 1
    status, _ = _get(server, "/v1/skills/99999")
    assert status == 404


def test_skill_params(server):
    status, body = _get(server, "/v1/skill-params/91003")
    assert status == 200
    levels = [i["level"] for i in body["data"]["items"]]
    assert levels == [10, 13]
    _, body2 = _get(server, "/v1/skill-params/91003?level=13")
    assert len(body2["data"]["items"]) == 1
    status, _ = _get(server, "/v1/skill-params/99999")
    assert status == 404


def test_search(server):
    status, body = _get(server, f"/v1/search?q={quote('测试')}")
    assert status == 200
    d = body["data"]
    assert d["characters"][0]["name"] == "测试角色"
    assert any(s["name"] == "测试爆发" for s in d["skills"])
    assert d["talents"][0]["name"] == "一命·测试"

    status, _ = _get(server, "/v1/search")
    assert status == 400


def _patch(srv, ann_id, body, token=None):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        srv.base_url + f"/v1/annotations/{ann_id}", data=data, method="PATCH"
    )
    if token:
        req.add_header("X-Local-Token", token)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _delete(srv, ann_id, token=None):
    req = urllib.request.Request(
        srv.base_url + f"/v1/annotations/{ann_id}", method="DELETE"
    )
    if token:
        req.add_header("X-Local-Token", token)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_annotation_patch_is_deprecated(server):
    ann_id = 1
    # 无令牌 → 401
    status, _ = _patch(server, ann_id, {"title": "x"})
    assert status == 401
    # 已认证写入仍统一拒绝，且不进入字段级修改逻辑。
    status, body = _patch(server, ann_id, {"title": "新标题"}, token=server.token)
    assert status == 410 and body["reason"] == "accepted_database_is_immutable"
    status, body = _patch(server, ann_id, {"status": "nope"}, token=server.token)
    assert status == 410 and body["reason"] == "accepted_database_is_immutable"


def test_annotation_delete_is_deprecated(server):
    ann_id = 1
    status, _ = _delete(server, ann_id)
    assert status == 401
    status, body = _delete(server, ann_id, token=server.token)
    assert status == 410 and body["reason"] == "accepted_database_is_immutable"


def test_db_update_status(server):
    status, body = _get(server, "/v1/db/update/status")
    assert status == 200
    assert body["data"]["updating"] is False
    assert body["data"]["meta"]["game_version"] == "7.0"


def test_single_instance_guard(server):
    from genshin_db.service import check_existing_service

    info_path = server.data_dir / "service.json"
    # 发现文件指向存活服务 → 拒绝多开，返回其地址
    info_path.write_text(
        json.dumps({"base_url": server.base_url}), encoding="utf-8"
    )
    assert check_existing_service(server.data_dir) == server.base_url
    # 发现文件失效（端口无服务）→ 清理并放行
    info_path.write_text(
        json.dumps({"base_url": "http://127.0.0.1:9"}), encoding="utf-8"
    )
    assert check_existing_service(server.data_dir) is None
    assert not info_path.exists()
    # 无发现文件 → 放行
    assert check_existing_service(server.data_dir) is None


@pytest.mark.parametrize("body, expected", [
    ([], 400),
    (None, 400),
    ({"target_type": "typo", "target_id": 1, "content": "x"}, 410),
    ({"target_type": "character", "target_id": {}, "content": "x"}, 410),
    ({"target_type": "character", "target_id": True, "content": "x"}, 410),
    ({"target_type": "character", "target_id": 1.5, "content": "x"}, 410),
    ({"target_type": "character", "target_id": 1, "content": "  "}, 410),
    ({"target_type": "character", "target_id": 1, "content": ["x"]}, 410),
])
def test_annotation_write_deprecation_precedes_field_validation(server, body, expected):
    status, response = _post(server, "/v1/annotations", body, token=server.token)
    assert status == expected and response["code"] == expected
    if expected == 410:
        assert response["reason"] == "accepted_database_is_immutable"


@pytest.mark.parametrize("path", ["/v1/characters?page=0", "/v1/characters?page_size=-1",
                                  "/v1/characters?page=" + "9" * 30,
                                  "/v1/characters?version_line=nope", "/v1/characters?rarity=3",
                                  "/v1/characters?page=1&page=2", "/v1/skill-params/91003?level=0",
                                  "/v1/annotations?status=nope", "/v1/annotations?target_id=0"])
def test_invalid_query_values_return_400(server, path):
    status, response = _get(server, path)
    assert status == 400 and response["code"] == 400


def test_null_patch_content_and_force_type(server):
    assert _patch(server, 1, {"content": None}, token=server.token)[0] == 410
    assert _post(server, "/v1/db/update", {"force": "false"}, token=server.token)[0] == 410
    assert _post(server, "/v1/db/update", {"force": 1}, token=server.token)[0] == 410


@pytest.mark.parametrize("status", ["\ud800", "无效状态"])
def test_annotation_status_error_is_utf8_json(server, status):
    req = urllib.request.Request(
        server.base_url + "/v1/annotations/1",
        data=json.dumps({"status": status}).encode("utf-8"), method="PATCH",
        headers={"X-Local-Token": server.token},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 410
    assert exc.value.headers["Content-Type"] == "application/json; charset=utf-8"
    raw = exc.value.read().decode("utf-8")
    body = json.loads(raw)
    assert body["code"] == 410
    assert body["reason"] == "accepted_database_is_immutable"


def test_unicode_error_diagnostic_keeps_json_response(server, monkeypatch):
    from genshin_db import queries

    def invalid_status(*_):
        raise ValueError("非法 status: \ud800")

    monkeypatch.setattr(queries, "update_annotation", invalid_status)
    status, body = _patch(server, 1, {"status": "\ud800"}, token=server.token)
    assert status == 410
    assert body["reason"] == "accepted_database_is_immutable"


@pytest.mark.parametrize("path, status", [("/v1/characters", 405), ("/v1/nope", 404)])
@pytest.mark.parametrize("data", [None, b"not JSON"])
def test_unregistered_method_uses_json_route_errors(server, path, status, data):
    req = urllib.request.Request(server.base_url + path, method="PROPFIND", data=data)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == status
    assert exc.value.headers["Content-Type"] == "application/json; charset=utf-8"
    assert json.loads(exc.value.read().decode("utf-8"))["code"] == status
    if status == 405:
        assert exc.value.headers["Allow"] == "GET"


def test_known_routes_have_405_and_unknown_routes_404(server):
    assert _post(server, "/v1/characters", {}, token=server.token)[0] == 405
    assert _get(server, "/v1/db/update")[0] == 405
    assert _post(server, "/v1/nope", {}, token=server.token)[0] == 404
    req = urllib.request.Request(server.base_url + "/v1/characters", method="OPTIONS")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 405
    assert exc.value.headers["Allow"] == "GET"
    assert json.loads(exc.value.read())["code"] == 405


def test_annotation_default_line_and_explicit_submission_are_deprecated(server):
    for line in [None, "release", "beta"]:
        status, body = _post(server, "/v1/annotations", {
            "target_type": "character", "target_id": 90000001, "content": "x",
            "version_line": line,
        }, token=server.token)
        assert status == 410
        assert body["reason"] == "accepted_database_is_immutable"
    lines = [a["version_line"] for a in _get(server, "/v1/annotations")[1]["data"]["items"]]
    assert lines == []


def test_serve_process_lock_and_discovery_survive_probe_failure(server, monkeypatch):
    from genshin_db import service
    from tests.test_db_locking import child_process

    info_path = server.data_dir / "service.json"
    process = child_process(
        "from pathlib import Path; import sys; from genshin_db.locking import file_lock; "
        "lock=file_lock(Path(sys.argv[1])/'.service.lock'); lock.__enter__(); "
        "print('ready',flush=True); sys.stdin.read()", server.data_dir)
    try:
        assert process.stdout.readline().strip() == "ready"
        assert service.serve(server.data_dir) == 1
        original_info = info_path.read_bytes()
        monkeypatch.setattr(service, "_probe_service", lambda _: None)
        assert service.check_existing_service(server.data_dir) is None
        assert info_path.read_bytes() == original_info
        assert _get(server, "/v1/meta/version")[0] == 200
    finally:
        process.kill()
        process.communicate(timeout=5)


def test_external_update_lock_visible_in_status(server):
    from tests.test_db_locking import child_process

    process = child_process(
        "from pathlib import Path; import sys; from genshin_db.locking import file_lock; "
        "lock=file_lock(Path(sys.argv[1])/'.update.lock'); lock.__enter__(); "
        "print('ready',flush=True); sys.stdin.read()", server.data_dir)
    try:
        assert process.stdout.readline().strip() == "ready"
        assert _get(server, "/v1/db/update/status")[1]["data"]["updating"] is True
    finally:
        process.kill()
        process.communicate(timeout=5)
    assert _get(server, "/v1/db/update/status")[1]["data"]["updating"] is False


def test_two_service_processes_start_only_one_instance(server, tmp_path):
    import time

    from tests.test_db_locking import child_process

    data_dir = tmp_path / "concurrent-service"
    data_dir.mkdir()
    source = sqlite3.connect(server.db_path)
    dest = sqlite3.connect(data_dir / "genshin.db")
    source.backup(dest)
    source.close()
    dest.close()
    code = ("from pathlib import Path; import sys; from genshin_db.service import serve; "
            "print('ready',flush=True); sys.stdin.readline(); "
            "raise SystemExit(serve(Path(sys.argv[1])))")
    children = [child_process(code, data_dir) for _ in range(2)]
    try:
        for child in children:
            assert child.stdout.readline().strip() == "ready"
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and all(p.poll() is None for p in children):
            time.sleep(0.01)
        assert sorted(str(p.poll()) for p in children) == ["1", "None"]
        # Losing the lock can finish before the owner has created its server.
        # Wait for the owner's atomic discovery publication as a separate event.
        info_path = data_dir / "service.json"
        owner = next(child for child in children if child.poll() is None)
        while time.monotonic() < deadline and not info_path.is_file() and owner.poll() is None:
            time.sleep(0.01)
        assert owner.poll() is None
        assert info_path.is_file(), "Lock owner did not publish service discovery before the deadline"
        info = json.loads(info_path.read_text(encoding="utf-8"))
        assert info["pid"] == owner.pid
        with urllib.request.urlopen(info["base_url"] + "/v1/meta/version", timeout=3) as response:
            assert response.status == 200
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
