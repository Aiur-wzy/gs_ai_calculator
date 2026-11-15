"""Read-only reference queries for whiteboard handoff construction.

Returned data is a knowledge/reference proposal.  This gateway never confirms
models, emits executable certificates, compiles a rule, or writes a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WhiteboardReferenceGateway:
    database_path: Path | str | None

    @property
    def weapon_available(self) -> bool:
        return self.database_path is not None and Path(self.database_path).exists()

    @property
    def admission_available(self) -> bool:
        return self.database_path is not None and Path(self.database_path).is_file()

    def weapon_detail(self, weapon_id, version_line):
        from genshin_db.queries import get_weapon_detail

        return get_weapon_detail(
            self.database_path, weapon_id, version_line=version_line
        )

    def action_admission_profile(self, intent):
        from genshin_db.runtime_action_admission import get_action_admission_profile

        return get_action_admission_profile(
            self.database_path,
            intent["version_line"],
            selection=intent["selection"],
            variant=intent["variant"],
            action_type_id="action.elemental_burst",
            model_ref=intent["model_ref"],
        )
