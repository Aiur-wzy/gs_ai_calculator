"""Direct index publishers cannot waive readonly request roles with their CAS."""

from __future__ import annotations

import hashlib
import os

import pytest

from genshin_app import calculation_delivery_files, runtime_batch_files
from genshin_app.equipment import files
from genshin_app.equipment.schema import PlanError
from tests.test_calculation_delivery_files_review import (
    review_publication as review_publication,  # noqa: PLC0414
)
from tests.test_runtime_batch_files import batch


def race_during_index_prepare(monkeypatch, *, index, immutable):
    original = files.tempfile.mkstemp
    changed = []

    def begin(*arguments, **options):
        answer = original(*arguments, **options)
        if options.get("prefix") == ".index.json." and not changed:
            immutable.rename(immutable.with_name("external-source-snapshot"))
            os.link(index, immutable)
            changed.append(True)
        return answer

    monkeypatch.setattr(files.tempfile, "mkstemp", begin)
    return changed


def test_direct_runtime_index_cas_rechecks_immutable_request(monkeypatch, tmp_path):
    directory = tmp_path / "facts"
    runtime_batch_files.publish_batch_directory(
        runtime_batch_files.prepare_batch_directory(directory), batch()
    )
    index = directory / "index.json"
    before = index.read_bytes()
    request = tmp_path / "request.json"
    request.write_bytes(b"readonly request")
    prepared = runtime_batch_files.prepare_batch_directory(
        directory, overwrite=True, protected_paths=[request]
    )
    changed = race_during_index_prepare(monkeypatch, index=index, immutable=request)
    with pytest.raises(PlanError) as caught:
        runtime_batch_files.publish_batch_directory(prepared, batch())
    assert changed and index.read_bytes() == before
    assert caught.value.details["index_committed"] is False
    assert (tmp_path / "external-source-snapshot").read_bytes() == b"readonly request"


def test_direct_calculation_index_cas_rechecks_immutable_request(
    review_publication, monkeypatch, tmp_path
):
    source, directory, built = review_publication
    first = calculation_delivery_files.prepare_delivery_directory(
        directory, protected_paths=[source]
    )
    calculation_delivery_files.publish_calculation_delivery(
        first,
        built,
        input_path=source,
        input_file_sha=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    index = directory / "index.json"
    before = index.read_bytes()
    request = tmp_path / "request.json"
    request.write_bytes(b"readonly request")
    prepared = calculation_delivery_files.prepare_delivery_directory(
        directory, overwrite=True, protected_paths=[source, request]
    )
    changed = race_during_index_prepare(monkeypatch, index=index, immutable=request)
    with pytest.raises(PlanError) as caught:
        calculation_delivery_files.publish_calculation_delivery(
            prepared,
            built,
            input_path=source,
            input_file_sha=hashlib.sha256(source.read_bytes()).hexdigest(),
        )
    assert changed and index.read_bytes() == before
    assert caught.value.details["index_committed"] is False
    assert (tmp_path / "external-source-snapshot").read_bytes() == b"readonly request"
