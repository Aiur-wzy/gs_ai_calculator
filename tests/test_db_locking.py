"""独立 Python 进程验证提交锁与进程退出恢复，不依赖 HTTP 线程内互斥。"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from genshin_db.annotation_store import (
    Annotation,
    add_annotation,
    create_annotation_tables,
)
from genshin_db.locking import database_lock, file_lock
from genshin_db.queries import list_annotations


def child_process(code: str, *args) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONUTF8"] = "1"
    return subprocess.Popen(
        [sys.executable, "-u", "-c", code, *map(str, args)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env,
    )


def test_cross_process_writer_waits_for_database_lock(tmp_path):
    db = tmp_path / "genshin.db"
    create_annotation_tables(db)
    process = None
    try:
        with database_lock(db):
            # 同线程重入不自锁。
            add_annotation(db, Annotation("character", 1, "before"))
            process = child_process(
                "from pathlib import Path; import sys; "
                "from genshin_db.queries import create_annotation; "
                "print('ready',flush=True); "
                "print(create_annotation(Path(sys.argv[1]),'character',1,'after'))", db)
            assert process.stdout.readline().strip() == "ready"
            with pytest.raises(subprocess.TimeoutExpired):
                process.communicate(timeout=0.2)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert "draft" in stdout
        assert [a["content"] for a in list_annotations(db, status=None)["items"]] == [
            "before", "after"]
    finally:
        if process and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_process_exit_releases_os_lock(tmp_path):
    path = tmp_path / "owner.lock"
    process = child_process(
        "from pathlib import Path; import sys; from genshin_db.locking import file_lock; "
        "lock=file_lock(Path(sys.argv[1])); lock.__enter__(); "
        "print('ready',flush=True); sys.stdin.read()", path)
    try:
        assert process.stdout.readline().strip() == "ready"
        contender = child_process(
            "from pathlib import Path; import sys; "
            "from genshin_db.locking import file_lock,LockBusyError\n"
            "try:\n with file_lock(Path(sys.argv[1]),timeout=0): print('acquired')\n"
            "except LockBusyError: print('busy')", path)
        stdout, stderr = contender.communicate(timeout=5)
        assert contender.returncode == 0, stderr
        assert stdout.strip() == "busy"
    finally:
        process.kill()
        process.communicate(timeout=5)
    with file_lock(path, timeout=0):
        assert path.exists()  # 文件存在不代表仍被占用。


def test_standalone_cli_queries_wait_for_active_profile_then_succeed(tmp_path):
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    script = """
import sys
from genshin_app import cli, public_appendix_query
cli._require_runtime_database = lambda directory: None
public_appendix_query.read_appendix = lambda database, query: {"observed": True}
print("ready", flush=True)
raise SystemExit(cli.main(["--data-dir", sys.argv[1], "db", "appendix", "--input", sys.argv[2]]))
"""
    processes = []
    try:
        with file_lock(tmp_path / ".update.lock"):
            for _ in range(2):
                process = child_process(script, tmp_path, request)
                processes.append(process)
                assert process.stdout.readline().strip() == "ready"
            for process in processes:
                with pytest.raises(subprocess.TimeoutExpired):
                    process.wait(timeout=0.1)
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
            assert json.loads(stdout)["data"] == {"observed": True}
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
