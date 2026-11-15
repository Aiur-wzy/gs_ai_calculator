"""One-shot JSON bridge for GUI tests; no database or files are created."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.external_damage_schema import external_damage_contract
from genshin_app.equipment.schema import PlanError
from tests.test_external_damage import external_plan


def main():
    request = json.load(sys.stdin)
    database = Path(__file__).with_name("external-gui-absent.db")
    if database.exists():
        raise RuntimeError("The GUI fixture requires an absent database path")
    try:
        operation = request["operation"]
        if operation == "init":
            data = {"plan": external_plan(), "contract": external_damage_contract()}
        elif operation == "compose":
            data = compose_equipment_plan(request["plan"], database)
        elif operation == "calc":
            data = run_plan_calc(request["plan"], database)
        else:
            raise ValueError("Unsupported test operation")
        response = {"code": 0, "data": data}
    except (PlanError, ValueError) as exc:
        response = {"code": 1, "message": str(exc)}
    print(json.dumps(response, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
