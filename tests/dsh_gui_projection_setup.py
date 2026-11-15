"""Write deterministic inputs for the actual-browser/GuiApi integration test."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from genshin_app.equipment.files import save_plan
from genshin_app.equipment.schema import new_plan
from genshin_app.whiteboard import save_whiteboard
from tests.test_native_dsh_gui_workflow_runner import calculation_fixture
from tests.test_queries import db_path


def main():
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    database_root = root / "fixture-database"
    database_root.mkdir()
    database = db_path.__wrapped__(database_root)
    board, calls = calculation_fixture(root, database)
    input_path = root / "input.whiteboard.json"
    save_whiteboard(input_path, board)
    plan = new_plan()
    plan["manual_template"]["meta"]["name"] = "工程计算方案"
    native_input_path = root / "input.native.json"
    save_plan(native_input_path, plan)
    script = root / "provider-script.json"
    script.write_text(json.dumps({"calls": calls, "repeatCalls": False}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"database": str(database), "input": str(input_path), "native_input": str(native_input_path), "script": str(script),
                      "title": board["title"]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
