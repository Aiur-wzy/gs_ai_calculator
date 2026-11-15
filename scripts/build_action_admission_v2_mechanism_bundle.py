"""Build the deterministic reviewed-mechanism seed for action admission v2.

The migration deliberately creates a new qualification and descriptor identity.
It never changes the successful_action_v1 Noblesse descriptor or its current
head.  Review state remains pending in source control; production approval is a
separate, explicit semantic-store operation.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/genshin_db/resources/mechanisms/reviewed-mechanisms-v3.json"
TARGET = ROOT / "src/genshin_db/resources/mechanisms/reviewed-mechanisms-v5.json"
OLD_ID = "artifact.burst-team-atk-percent"
NEW_ID = "artifact.burst-team-atk-percent.action-admission-v2"
QUALIFICATION_ID = NEW_ID + ".qualification"


def build() -> dict:
    source = json.loads(SOURCE.read_text("utf-8"))
    old_descriptor = next(row for row in source["descriptors"] if row["id"] == OLD_ID)
    old_qualification = next(
        row for row in source["facts"] if row["id"] == OLD_ID + ".qualification"
    )

    qualification = copy.deepcopy(old_qualification)
    qualification.update(id=QUALIFICATION_ID, revision=1)
    qualification["payload"].update(id=QUALIFICATION_ID, revision=1)
    parent = qualification["payload"]["claim"]["parent"]
    parent["profile_kind"] = "successful_action_v2"
    parent["required_receipts"] = ["cast_identity", "action_admission"]
    qualification["review"] = {"status": "pending", "reviewer": None, "reviewed_at": None}

    descriptor = copy.deepcopy(old_descriptor)
    descriptor.update(id=NEW_ID, revision=1)
    descriptor["payload"]["descriptor"].update(mechanism_id=NEW_ID, revision=1)
    descriptor["payload"]["descriptor"]["evidence_fact_ids"] = [
        OLD_ID + ".rule.team-atk@1",
        OLD_ID + ".parameter.factor@1",
        OLD_ID + ".parameter.duration@1",
        QUALIFICATION_ID + "@1",
    ]
    descriptor["review"] = {"status": "pending", "reviewer": None, "reviewed_at": None}

    return {
        "schema_version": 1,
        "id": "mechanisms.v5.action-admission-v2",
        "facts": [qualification],
        "descriptors": [descriptor],
        "heads": [
            {"record_kind": "fact", "record_id": QUALIFICATION_ID, "revision": 1,
             "expected_head_revision": None},
            {"record_kind": "descriptor", "record_id": NEW_ID, "revision": 1,
             "expected_head_revision": None},
        ],
    }


def main() -> None:
    TARGET.write_text(
        json.dumps(build(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        "utf-8",
    )


if __name__ == "__main__":
    main()
