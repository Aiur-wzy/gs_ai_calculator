"""Exercise shipped CLI boundaries; no GUI window or installed database mutation."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.7.0")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output_dir or root / "outputs/engine-continuation-20261002/package"
    if args.version != "0.7.0" and args.output_dir is None:
        parser.error("验证新版本必须指定独立output-dir，保留EC-01验收记录")
    output = (root / output).resolve()
    if (root / "outputs").resolve() not in output.parents:
        parser.error("验收输出须位于工作区outputs内")
    output.mkdir(parents=True, exist_ok=True)
    exe = root / "dist" / args.version / "GenshinCalc/GenshinCalc.exe"
    db = root / "data/genshin.db"
    before_db = digest(db)
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        assert condition, name

    def run(name, *args, success=True):
        proc = subprocess.run([str(exe), "--data-dir", str(root / "data"), *map(str, args)],
                              capture_output=True, encoding="utf-8", cwd=root, timeout=90, check=False)
        (output / (name+".stdout.json")).write_text(proc.stdout, encoding="utf-8")
        (output / (name+".stderr.json")).write_text(proc.stderr, encoding="utf-8")
        check(name+" exit", (proc.returncode == 0) == success)
        payload = json.loads(proc.stdout if success else proc.stderr)
        return payload.get("data", payload), len(proc.stdout.encode("utf-8"))

    caps, _ = run("capabilities", "capabilities")
    check("version", caps["version"] == args.version)
    check("elemental capability", "gauge-basic-v1" in json.dumps(caps))
    if args.version != "0.7.0":
        check("database elemental compiler capability", "database-icd-v1" in json.dumps(caps))
        for group, status in (("default", "ready"), ("Airborne_ChestAtk", "pending")):
            request = output / (group+"-request.json")
            request.write_text(json.dumps({"action": "attenuation_profile", "group": group}), encoding="utf8")
            facts, size = run(group, "db", "runtime", "--input", request, "--output", output / (group+".json"), "--summary", "--overwrite")
            check(group+" executable status is distinct", facts["status"] == "ready" and facts["execution"]["status"] == status)
            check(group+" output bounded", size < 4000)
    request = output / "action-request.json"
    request.write_text(json.dumps({"action": "action_facts", "character_id": 10000002, "slot": "burst"}), encoding="utf-8")
    facts, size = run("action-facts", "db", "runtime", "--input", request, "--output", output / "action-facts.json", "--summary", "--overwrite")
    check("bounded stdout", size < 3500)
    check("facts ready dispatch pending", facts["status"] == "ready" and facts["action_dispatch"]["status"] == "pending")
    check("effective level ten", facts["level"]["effective_level"] == 10)
    plan_path = root / "tests/fixtures/engine-continuation/delivery/基础融化-原生方案.json"
    before_plan = digest(plan_path)
    result, size = run("native-calc", "equipment", "calc", "--plan", plan_path,
                       "--output", output / "result.json", "--summary", "--overwrite")
    data = json.loads((output / "result.json").read_text(encoding="utf-8"))["result"]
    check("native damage", [h["expected"] for h in data["hits"]] == [450, 900])
    check("actual melt", data["elemental"]["trace"][1]["reaction"]["type"] == "melt")
    check("once not cycle proof", data["rotation_check"]["energy_check"]["proven_cycle"] is None)
    check("complete provided window", data["mechanics"]["complete_first_cycle"])
    check("calc bounded stdout", size < 3500)
    check("no Chinese presentation gaps", result["presentation_issue_count"] == 0)
    unknown = copy.deepcopy(json.loads(plan_path.read_text(encoding="utf-8")))
    unknown["mechanics_model"]["elemental_model"]["target"]["knowledge"] = "unknown"
    unknown_path = output / "unknown-input.json"
    unknown_path.write_text(json.dumps(unknown, ensure_ascii=False), encoding="utf-8")
    run("unknown", "equipment", "calc", "--plan", unknown_path, "--output", output / "must-not-exist.json", "--summary", success=False)
    check("unknown no result file", not (output / "must-not-exist.json").exists())
    run("workbook", "equipment", "workbook", "--plan", plan_path, "--output", output / "formulas.xlsx", "--overwrite")
    with zipfile.ZipFile(output / "formulas.xlsx") as archive:
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        cells = {c.attrib["r"]: c for c in ET.fromstring(archive.read("xl/worksheets/sheet3.xml")).findall(".//s:c", ns)}
        check("workbook cached independent values", [float(cells[f"V{i}"].find("s:v", ns).text) for i in (5, 6)] == [450, 900])
        check("workbook real formulas", all(cells[f"V{i}"].find("s:f", ns) is not None for i in (5, 6)))
    legacy_path = root / "benchmarks/followup/ayaka-opt-02/delivery/神鹤万心-界面查看.json"
    if legacy_path.exists():
        old_sha = digest(legacy_path)
        old, _ = run("legacy", "engine", "calc", legacy_path, "--output", output / "legacy-result.json")
        check("legacy numeric unchanged", abs(old["summary"]["total_damage"] - 3370462.80505405) < 1e-6)
        check("legacy file unchanged", digest(legacy_path) == old_sha)
    check("input plan unchanged", digest(plan_path) == before_plan)
    check("project DB unchanged", digest(db) == before_db)
    resources = []
    if args.version != "0.7.0":
        for source_dir, packed_dir in ((root / "src/genshin_app/gui/web", exe.parent / "_internal/genshin_app/gui/web"),
                                       (root / "skill", exe.parent / "skill")):
            for path in sorted(source_dir.rglob("*")):
                if path.is_file():
                    target = packed_dir / path.relative_to(source_dir)
                    resources.append({"source": str(path.relative_to(root)), "sha256": digest(path)})
                    assert target.is_file() and digest(target) == digest(path), path
        check("current GUI and Skill resources byte identical", bool(resources))
    summary = {"version": args.version, "checks": checks, "passed": len(checks), "exe_sha256": digest(exe),
               "resource_files": resources,
               "gui_sha256": digest(exe.with_name("GenshinCalcGUI.exe")), "database_sha256": before_db,
               "not_verified": ["前台桌面操作", "安装器", "实际游戏客户端", "完整角色动作自动展开"]}
    (output / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": len(checks), "output": str(output), "exe_sha256": summary["exe_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
