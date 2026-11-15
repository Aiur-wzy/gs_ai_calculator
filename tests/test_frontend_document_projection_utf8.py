"""The real no-window frontend applier must preserve chunked UTF-8 exactly."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from genshin_app.whiteboard import new_whiteboard
from tests.pinned_native_node import pinned_native_node

ROOT = Path(__file__).resolve().parents[1]


def test_actual_frontend_projection_preserves_multibyte_input_across_pipe_chunks(tmp_path):
    board = new_whiteboard()
    title = "数据库角色的真实技能机制，审核并同步。🧊"
    reviewed_board = {**board, "title": title}
    calculated = {"report": {"source": title}, "result": {"notes": [title]}}
    request = {"kind": "whiteboard", "board": board, "conversation_id": "utf8-projection",
        "reviewed": {"board": reviewed_board, "issues": [], "calculated_delivery": {
            "delivery_capability": "engineering-test-only", "calculated": calculated}}}
    input_file = tmp_path / "request.json"
    input_file.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    preload = tmp_path / "split-stdin.mjs"
    # Force a boundary at every UTF-8 byte, rather than hoping the OS pipe
    # happens to split one of the multibyte characters in this particular run.
    preload.write_text(
        "import {Readable} from 'node:stream';\n"
        "import {readFileSync} from 'node:fs';\n"
        "import {setImmediate} from 'node:timers/promises';\n"
        f"const raw=readFileSync({json.dumps(str(input_file))});\n"
        "async function* chunks(){for(const byte of raw){yield Buffer.from([byte]);await setImmediate();}}\n"
        "Object.defineProperty(process,'stdin',{value:Readable.from(chunks(),{objectMode:false})});\n",
        encoding="utf-8",
    )
    result = subprocess.run([str(pinned_native_node()), "--import", preload.as_uri(),
        str(ROOT / "frontend/scripts/app-document-projection.mjs")],
        capture_output=True, check=True, timeout=30)
    value = json.loads(result.stdout.decode("utf-8"))
    assert value["document"] == reviewed_board
    assert value["application_payload"] == {"kind": "whiteboard", "board": reviewed_board,
                                            "calculated": calculated}
    assert value["real_gui_tested"] is False
