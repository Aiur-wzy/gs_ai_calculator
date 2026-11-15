from __future__ import annotations

import json

import pytest

from genshin_app.database_localization import localize_database
from genshin_db import pipeline, queries
from genshin_db.live_mutation_policy import (
    AcceptedDatabaseImmutableError,
    assert_candidate_data_dir,
)
from genshin_db.semantic_store import import_bundle


def _mark_as_accepted(data_dir):
    marker = data_dir / "database-live-manifest.json"
    marker.write_text(json.dumps({"test": "accepted"}), encoding="utf-8")
    return marker


def test_candidate_directory_without_release_markers_is_allowed(tmp_path):
    assert assert_candidate_data_dir(tmp_path, "test") == tmp_path.resolve()


@pytest.mark.parametrize(
    "operation",
    [
        lambda directory: pipeline.run_update(directory),
        lambda directory: localize_database(directory / "genshin.db"),
        lambda directory: queries.create_annotation(
            directory / "genshin.db", "character", 1, "test"
        ),
        lambda directory: import_bundle(
            directory / "genshin.db", {}, dry_run=True
        ),
    ],
)
def test_low_level_writers_reject_an_accepted_database_directory(tmp_path, operation):
    marker = _mark_as_accepted(tmp_path)

    with pytest.raises(AcceptedDatabaseImmutableError) as caught:
        operation(tmp_path)

    assert caught.value.details["reason"] == "accepted_database_is_immutable"
    assert caught.value.details["active_database_modified"] is False
    assert caught.value.details["accepted_release_markers"] == [marker.name]
    assert list(tmp_path.iterdir()) == [marker]
