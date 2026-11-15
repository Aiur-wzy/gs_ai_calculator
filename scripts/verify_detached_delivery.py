"""Exercise EC05 packaged calculation/export and delayed HTTP rejection.

Uses the frozen EC04 engineering input read-only. The HTTP server uses its own
temporary database and hidden process, never the user's installed service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli-exe", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    allowed = ROOT / "outputs/engine-continuation-20261002/ec05"
    if not output.is_relative_to(allowed.resolve()) or output == allowed.resolve():
        parser.error("输出必须位于ec05独立子目录，不得改写冻结材料")
    output.mkdir(parents=True, exist_ok=True)
    exe = args.cli_exe.resolve()
    frozen = ROOT / "tests/fixtures/engine-continuation/ec04/delivery"
    native = frozen / "普通扩散-原生方案.json"
    baseline = json.loads((frozen / "普通扩散-完整计算结果.json").read_text("utf8"))
    db = ROOT / "data/genshin.db"
    before_plan, before_db, checks = digest(native), digest(db), []

    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})
        if not passed:
            raise AssertionError(name)

    def cli(label, *arguments):
        process = subprocess.run([str(exe), "--data-dir", str(db.parent), *map(str, arguments)],
            capture_output=True, encoding="utf8", cwd=ROOT, timeout=120, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        (output / (label + ".stdout.json")).write_text(process.stdout, "utf8")
        (output / (label + ".stderr.json")).write_text(process.stderr, "utf8")
        check(label + " exit", process.returncode == 0)
        payload = json.loads(process.stdout)
        return payload.get("data", payload)

    caps = cli("capabilities", "capabilities")
    check("candidate version", caps["version"] == "0.8.1")
    result_path = output / "普通扩散-计算结果.json"
    cli("calc", "equipment", "calc", "--plan", native, "--output", result_path, "--summary", "--overwrite")
    result = json.loads(result_path.read_text("utf8"))
    check("frozen EC04 damage and execution unchanged", result["result"] == baseline["result"])
    check("source fingerprint unchanged", result["result_receipt"]["source_fingerprint"] == baseline["result_receipt"]["source_fingerprint"])
    check("ordinary calculation avoids unused display capture", "display_source" not in result["result_receipt"])
    for fmt in ("csv", "xlsx"):
        path = output / ("普通扩散-结果." + fmt)
        receipt = cli(fmt, "equipment", "export", "--plan", native, "--format", fmt, "--output", path, "--overwrite")
        check(fmt + " display source is frozen", receipt["result_receipt"]["display_source"]["kind"] == "calculation_snapshot")
        check(fmt + " display fingerprint exists", bool(receipt["result_receipt"]["display_source"]["catalog_fingerprint"]))
        check(fmt + " numerical result unchanged", receipt["result"] == baseline["result"])
        check(fmt + " artifact exists", path.stat().st_size > 100)
    cli("workbook", "equipment", "workbook", "--plan", native,
        "--output", output / "普通扩散-活公式.xlsx", "--overwrite")

    # This private database is deliberately unrelated to production.
    private = output / ("http-fixture-" + uuid.uuid4().hex[:8])
    private.mkdir()
    conn = sqlite3.connect(private / "genshin.db")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)")
    conn.commit()
    conn.close()
    server = subprocess.Popen([str(exe), "--data-dir", str(private), "db", "serve", "--port", "0"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    observations = []
    try:
        discovery = private / "service.json"
        deadline = time.monotonic() + 20
        while not discovery.exists() and time.monotonic() < deadline and server.poll() is None:
            time.sleep(.05)
        check("private packaged service started", discovery.exists() and server.poll() is None)
        port = json.loads(discovery.read_text("utf8"))["port"]
        for path, expected in (("/v1/weapons/11401", 405), ("/v1/no-such-route", 404)):
            for delay in (.01, .05):
                for _ in range(3):
                    with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
                        sock.sendall(f"POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n\r\n".encode())
                        time.sleep(delay)
                        sock.sendall(b"{}")
                        data = bytearray()
                        while block := sock.recv(4096):
                            data.extend(block)
                    header, body = bytes(data).split(b"\r\n\r\n", 1)
                    size = next(int(line.split(b":", 1)[1]) for line in header.split(b"\r\n") if line.lower().startswith(b"content-length:"))
                    ok = header.startswith(f"HTTP/1.1 {expected}".encode()) and len(body) == size and json.loads(body)["code"] == expected
                    observations.append({"path": path, "delay_seconds": delay, "complete": ok})
                    check(f"packaged delayed HTTP {expected} {delay} attempt {len(observations)}", ok)
    finally:
        if server.poll() is None:
            server.terminate()
        _, error = server.communicate(timeout=10)
        (output / "private-service.stderr.txt").write_bytes(error or b"")
    check("production database unchanged", digest(db) == before_db)
    check("frozen native input unchanged", digest(native) == before_plan)
    report = {"status": "passed", "passed": len(checks), "checks": checks,
        "exe_sha256": digest(exe), "database_sha256": before_db, "native_sha256": before_plan,
        "http_observations": observations, "not_verified": ["前台GUI", "安装器", "实际游戏客户端", "Excel桌面重算"],
        "scope": "同源工程方案兼容与原生导出显示来源；独立进程服务拒绝响应。并发锁/输入隔离由单元与独立反例另证。"}
    write(output / "verification.json", report)
    print(json.dumps({"passed": len(checks), "output": str(output), "exe_sha256": report["exe_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
