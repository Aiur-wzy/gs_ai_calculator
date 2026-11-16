"""Read-only reference queries for whiteboard handoff construction.

Returned data is a knowledge/reference proposal.  This gateway never confirms
models, emits executable certificates, compiles a rule, or writes a database.
"""

from __future__ import annotations

import sqlite3
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

    def character_data_version(self, character_id, version_line):
        """Bind an actual catalog character to the same snapshot as composition.

        This only records source identity. It does not replace a user's finished
        panel, grant a rule execution authority, or confirm game mechanics.
        """
        from genshin_app.equipment.data_version import equipment_data_version
        from genshin_app.equipment.schema import PlanError
        from genshin_db import queries

        try:
            with queries.equipment_read_session(self.database_path):
                queries.get_character_detail(self.database_path, character_id, version_line)
                return equipment_data_version(queries.get_meta(self.database_path), version_line)
        except (sqlite3.Error, queries.NotFoundError, PlanError):
            # Handoff remains an editable draft when reference material cannot
            # be read. Never stamp an identity for an unverified character;
            # ordinary composition still reports the missing/invalid source.
            return None

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
