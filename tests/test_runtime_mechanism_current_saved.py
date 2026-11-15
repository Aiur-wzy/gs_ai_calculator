"""An actual pre-fix App save remains verifiable entirely offline."""
import copy
import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from genshin_app.equipment.mechanism_receipt import validate_mechanism_result_receipt
from genshin_app.equipment.service import result_bytes


def test_pre_current_guard_saved_result_remains_offline_valid(monkeypatch):
    path = Path(__file__).parent / "fixtures/mechanism_current/pre-guard-calculated.json.gz"
    original = json.loads(gzip.decompress(path.read_bytes()))
    saved = copy.deepcopy(original)
    def no_database(*args, **kwargs):
        pytest.fail("offline saved-result verification must not access a database")
    monkeypatch.setattr(sqlite3, "connect", no_database)
    monkeypatch.setattr(sqlite3.dbapi2, "connect", no_database)
    validate_mechanism_result_receipt(saved["result"], saved["result_receipt"])
    assert result_bytes(saved["result"], saved["result_receipt"], "json")
    assert result_bytes(saved["result"], saved["result_receipt"], "xlsx").startswith(b"PK")
    assert saved == original

    # Compatibility is explicitly tied to the historical producer contract.
    # A current receipt cannot opt into the omissions in this old saved ledger.
    current = copy.deepcopy(original)
    current["result_receipt"]["engine_version"] = "0.44.0"
    with pytest.raises(ValueError):
        validate_mechanism_result_receipt(current["result"], current["result_receipt"])

    saved["result"]["mechanism_compilations"][0]["source_material"]["source_proof"]["snapshot"]["snapshot_id"] = "0" * 64
    with pytest.raises(ValueError):
        validate_mechanism_result_receipt(saved["result"], saved["result_receipt"])
