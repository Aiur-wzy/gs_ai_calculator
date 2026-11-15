"""Pinned complete source materials and per-suite synthetic SQLite isolation."""
import hashlib
import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures/action_resources"


@lru_cache(maxsize=32)
def material_bytes(name):
    manifest = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))
    archive = FIXTURES / "materials.zip"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest["archive_sha256"]
    expected = manifest["members"][name]
    with ZipFile(archive) as source:
        assert set(source.namelist()) == set(manifest["members"])
        data = source.read(name)
    assert len(data) == expected["bytes"]
    assert hashlib.sha256(data).hexdigest() == expected["sha256"]
    return data


def load_material(name):
    # Parse anew so a negative test cannot mutate another test's fixture.
    return json.loads(material_bytes(name))


@pytest.fixture
def action_resource_synthetic_db_only(tmp_path, monkeypatch):
    """Deny opening anything outside this test's new temporary directory."""
    original = sqlite3.connect

    def connect(database, *args, **kwargs):
        value = str(database)
        if value != ":memory:":
            if value.startswith("file:"):
                value = unquote(urlsplit(value).path)
                if len(value) >= 3 and value[0] == "/" and value[2] == ":":
                    value = value[1:]
            assert Path(value).resolve().is_relative_to(tmp_path.resolve()), value
        return original(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
