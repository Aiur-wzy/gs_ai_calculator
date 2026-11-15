import copy

import pytest

from genshin_db.runtime_capacity_delta import Budget, CapacityError
from genshin_db.runtime_catalog import dumps
from genshin_db.runtime_catalog_projection import project_activation_catalog
from genshin_db.runtime_request_contracts import validate_runtime_request


def test_budget_uses_actual_utf8_not_six_bytes_per_every_character():
    value = {"text": "测试源" * 200, "values": list(range(1000))}
    exact = len(dumps(value).encode("utf-8"))
    budget = Budget(limits={"bytes": exact + 1100})
    budget.scan(value)
    assert exact <= budget.bytes <= exact + 1100
    with pytest.raises(CapacityError):
        Budget(limits={"bytes": exact // 2}).scan(value)


def test_source_catalog_pages_are_snapshot_bound_and_do_not_change_full_identity():
    source = {"contract": "fresh-action-activation-catalog-v1", "content_fingerprint": "a" * 64,
              "snapshot": {"snapshot_id": "one"}, "selection": {"character_id": 1, "constellation": 0, "ascension": None},
              "root_occurrences": [{"id": str(i)} for i in range(4)], "materials": {str(i): i for i in range(5)}}
    before = copy.deepcopy(source)
    summary = project_activation_catalog(source, view="summary", page_size=2)
    for request in summary["next_options"]:
        validate_runtime_request(request)
        assert request["snapshot_id"] == "one"
    pages = [project_activation_catalog(source, view="materials", page=i, page_size=2) for i in (1, 2, 3)]
    assert [r["key"] for page in pages for r in page["items"]] == [str(i) for i in range(5)]
    assert pages[-1]["next_page"] is None
    assert all(page["content_fingerprint"] == source["content_fingerprint"] for page in pages)
    assert source == before


def test_page_limits_are_same_in_public_schema_and_projection():
    for size in (0, 51):
        with pytest.raises(ValueError):
            validate_runtime_request({"action": "action_activation_catalog", "character_id": 1,
                                      "constellation": 0, "catalog_view": "roots", "page_size": size})
        with pytest.raises(ValueError):
            project_activation_catalog({}, view="roots", page_size=size)
