"""Public runtime batches preserve exact single-query results in one session."""
import copy

import pytest

from genshin_db import runtime_queries
from genshin_db.runtime_catalog import RuntimeConfigError, dumps, fingerprint
from genshin_db.runtime_queries import query_runtime, runtime_capabilities
from tests.test_runtime_attenuation import (  # noqa: F401
    attenuation_db,
    fake_repo,
    stub_fetcher,
)

# ruff: noqa: F811

def request(groups=("default",), **fields):
    return {"action": "batch", "items": [
        {"id": f"item-{position}", "request": {"action": "attenuation_profile", "group": group}}
        for position, group in enumerate(groups)], **fields}


def test_eight_items_share_validation_and_equal_exact_single_responses(attenuation_db, monkeypatch):
    single = query_runtime(attenuation_db, {"action": "attenuation_profile", "group": "default"})
    original, calls = runtime_queries.validate_runtime, []

    def validate(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime_queries, "validate_runtime", validate)
    req = request(("default",) * 8, snapshot_id=single["snapshot"]["snapshot_id"])
    unchanged = copy.deepcopy(req)
    result = query_runtime(attenuation_db, req)
    assert calls == [True] and req == unchanged
    assert result["status"] == "complete" and result["counts"] == {"requested": 8, "ok": 8, "error": 0, "skipped": 0}
    assert all(item["data"] == single and item["data"]["snapshot"] == result["snapshot"] for item in result["items"])
    assert fingerprint({k: v for k, v in result.items() if k != "content_fingerprint"}) == result["content_fingerprint"]
    assert len(dumps(result).encode("utf8")) <= result["limits"]["max_bytes"]
    assert runtime_capabilities()["batch"]["limits"]["max_items"] == 8


def test_missing_and_value_error_continue_without_upgrading_semantic_pending(attenuation_db):
    req = request(("missing", "nonbinary", "default", "default"))
    req["items"][2]["request"]["channel"] = "unsupported"
    data = query_runtime(attenuation_db, req)
    assert data["status"] == "partial"
    assert [item["status"] for item in data["items"]] == ["error", "ok", "error", "ok"]
    assert data["items"][0]["error"]["code"] == 404
    assert data["items"][2]["error"]["code"] == 400
    assert data["items"][1]["data"]["execution"]["status"] == "pending"


@pytest.mark.parametrize("change", [
    lambda r: r.update(items=[]), lambda r: r.update(items=request(("default",) * 9)["items"]),
    lambda r: r["items"].append(copy.deepcopy(r["items"][0])),
    lambda r: r["items"][0]["request"].update(action="batch", items=[]),
    lambda r: r["items"][0]["request"].update(action=[]),
    lambda r: r["items"][0]["request"].update(version_line="release"),
    lambda r: r["items"][0]["request"].update(snapshot_id="same"),
    lambda r: r["items"][0]["request"].update(group=None),
    lambda r: r["items"][0].update(id="../escape"),
    lambda r: r.update(max_bytes=True), lambda r: r.update(extra="unknown"),
])
def test_invalid_envelope_never_opens_read_session(tmp_path, monkeypatch, change):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid envelope opened database")
    monkeypatch.setattr(runtime_queries, "_read", forbidden)
    req = request()
    change(req)
    with pytest.raises(ValueError):
        query_runtime(tmp_path / "absent.db", req)


def test_wrong_expected_snapshot_is_global_fatal(attenuation_db):
    with pytest.raises(RuntimeConfigError) as exc:
        query_runtime(attenuation_db, request(snapshot_id="0" * 64))
    assert exc.value.reason == "snapshot_changed" and exc.value.status == 409


def test_control_character_error_has_reserved_json_escape_space(attenuation_db, monkeypatch):
    original = runtime_queries.query_runtime
    calls = []

    def query(db, req):
        calls.append(req["group"])
        if req["group"] == "bad":
            raise RuntimeConfigError("\0" * 1000, "not_found", 404, {"value": "\0" * 10000})
        data = original(db, req)
        data["bounded_extra"] = "x" * 45000
        return data

    from genshin_db.runtime_batch import query_runtime_batch

    monkeypatch.setattr(runtime_queries, "query_runtime", query)
    result = query_runtime_batch(attenuation_db, request(("default", "bad"), max_bytes=65536))
    assert calls == ["default", "bad"] and result["status"] == "partial"
    assert [item["status"] for item in result["items"]] == ["ok", "error"]
    error = result["items"][1]["error"]
    assert error["message"] == "\0" * 512 and error["details_omitted"]
    assert error["details_bytes"] > 4096 and error["details_fingerprint"]
    assert len(dumps(result).encode("utf8")) <= 65536
