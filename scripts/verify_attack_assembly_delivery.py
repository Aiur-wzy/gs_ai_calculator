"""Real-source CLI assembly and four-artifact delivery, without foreground UI."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

from verify_action_events_delivery import ROOT, make_plan
from verify_attack_metadata_delivery import SCOPE, evidence_text, metadata_workbook


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def leaves(value):
    if isinstance(value, dict):
        return sum(leaves(v) for v in value.values())
    if isinstance(value, list):
        return sum(leaves(v) for v in value)
    return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--exe", type=Path)
    args = parser.parse_args()
    directory = args.output_dir.resolve()
    allowed = ROOT / "outputs" / "engine-continuation-20261002" / "ec21-research"
    if allowed not in directory.parents or directory.exists():
        parser.error("验证需要ec21-research下尚不存在的独立目录")
    directory.mkdir(parents=True)
    db = args.data_dir.resolve() / "genshin.db"
    initial_sha = sha(db)
    executable = [str(args.exe.resolve())] if args.exe else [sys.executable, "-B", "-m", "genshin_app"]
    checks, command_number, outputs = [], 0, []

    def check(label, value):
        checks.append({"check": label, "passed": bool(value)})
        if not value:
            raise AssertionError(label)

    def run(*tail):
        nonlocal command_number
        command_number += 1
        command = [*executable, "--data-dir", str(args.data_dir.resolve()), *map(str, tail)]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf8", timeout=180, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        envelope = json.loads(result.stdout or result.stderr)
        write(directory / "commands" / f"{command_number:02d}.json", {
            "command": command, "returncode": result.returncode, "code": envelope.get("code"),
            "stdout_bytes": len(result.stdout.encode("utf8")), "stderr": result.stderr[:4096]})
        check(f"command {command_number} succeeded", result.returncode == 0 and envelope.get("code") == 0)
        return envelope["data"]

    def accept(plan, path):
        write(path, plan)
        report = run("equipment", "compose", "--plan", path)
        for source, receipt in zip(plan["reaction_damage_sources"], report["reaction_damage_compilations"], strict=True):
            source.update(receipt["required_confirmation"])
        write(path, plan)
        report = run("equipment", "compose", "--plan", path)
        confirmation = report["action_event_compilations"][0]["required_confirmation"]
        check(path.stem + " one existing parent confirmation", bool(confirmation))
        plan["action_event_profiles"][0].update(confirmation)
        write(path, plan)
        return plan

    try:
        caps = run("capabilities")
        write(directory / "capabilities.json", caps)
        for label, name, cid, element, variant, ability, multiplier in (
            ("cryo", "神里绫华", 10000002, "cryo", "tap", "Avatar_Ayaka_Icespine_Strike_New", 4.305600166320801),
            ("anemo", "枫原万叶", 10000047, "anemo", "hold", "Avatar_Kazuha_ElementalArt_FlyAway", 4.694399833679199),
        ):
            case = directory / label
            query_path = case / "query.json"
            write(query_path, {"action": "attack_metadata_profile", "version_line": "release",
                "character_id": cid, "slot": "skill", "constellation": 0, "talent_level": 10})
            discovery = run("db", "runtime", "--input", query_path)
            write(case / "discovery.json", discovery)
            candidates = [c for c in discovery["candidates"] if c["ability_name"] == ability and c["status"] == "ready"]
            check(label + " explicit real source selection", len(candidates) == 1)
            base = evidence_text(make_plan(name, cid, element))
            base["rotation_model"]["abilities"][0]["variant"] = variant
            base["manual_template"]["hits"] = []
            base["mechanics_model"]["elemental_model"]["profiles"] = []
            for field in ("hit_parameter_sources", "elemental_sources", "action_event_profiles",
                          "action_event_bindings", "action_event_overrides"):
                base.pop(field)
            input_path, request_path, native_path = case / "base.plan.json", case / "assembly.request.json", case / "assembled.plan.json"
            write(input_path, base)
            request = {"schema_version": 1, "profile_id": "source-skill", "ability_ids": ["skill"],
                "version_line": "release", "selection": {"character_id": cid, "slot": "skill", "constellation": 0, "talent_level": 10},
                "candidate_id": candidates[0]["candidate_id"], "damage_type_ids": ["damage.elemental_skill"], "scaling_stat": "atk",
                "calibration": {"offset_seconds": .5, "precision_seconds": .01, "status": "assumed", "source": SCOPE},
                "contact_profile_id": "safe-contact", "shared_group": "declared-skill-shared", "default_landed": True,
                "self_sampling": "current", "status": "assumed", "source": SCOPE}
            write(request_path, request)
            base_sha = sha(input_path)
            summary = run("equipment", "assemble-attack", "--plan", input_path, "--request", request_path, "--output", native_path)
            write(case / "assembly-receipt.json", summary)
            draft = read(native_path)
            check(label + " native output and frozen actions", draft["kind"] == "genshin-plan"
                  and draft["rotation_model"] == base["rotation_model"] and sha(input_path) == base_sha)
            check(label + " no silent acceptance or generated numeric fields", summary["state"] == "pending"
                  and draft["action_event_profiles"][0]["accepted_profile_fingerprint"] is None
                  and not ({"multiplier", "element"} & draft["manual_template"]["hits"][0].keys())
                  and not draft["mechanics_model"]["hit_patterns"])
            # Independently hand-wire the old supported native contract using
            # the specified ID policy, not the new constructor's output fields.
            suffix = hashlib.sha256(json.dumps(request["profile_id"], separators=(",", ":")).encode()).hexdigest()
            hid, appid = "assembly.hit." + suffix, "assembly.application." + suffix
            baseline = copy.deepcopy(base)
            baseline["manual_template"]["hits"] = [{"id": hid, "damage_type_ids": ["damage.elemental_skill"],
                "t": 0, "character": "actor", "skill": "元素战技", "hit_name": "点按命中" if variant == "tap" else "长按命中",
                "damage_system": "classic", "scaling_stat": "atk", "count": 1}]
            baseline["action_event_profiles"] = [{"id": "source-skill", "kind": "calibrated_single_contact_v1",
                "prototype_hit_id": hid, "variant": variant, "application_profile_id": appid, "accepted_profile_fingerprint": None,
                **{k: copy.deepcopy(request[k]) for k in ("calibration", "contact_profile_id", "default_landed", "self_sampling")}}]
            baseline["action_event_bindings"] = [{"ability_id": "skill", "profile_id": "source-skill"}]
            baseline["action_event_overrides"] = []
            baseline["attack_metadata_sources"] = [{"action_event_profile_id": "source-skill", "purpose": "single_stat_damage_multiplier",
                **{k: copy.deepcopy(request[k]) for k in ("version_line", "selection", "candidate_id", "shared_group", "status", "source")}}]
            check(label + " equals independently wired existing native contract", draft == baseline)
            manual_fields = {"hit": baseline["manual_template"]["hits"], **{k: baseline[k] for k in
                ("action_event_profiles", "action_event_bindings", "attack_metadata_sources")}}
            counts = {"manual_structural_groups": 4, "request_groups": 1, "manual_local_ids": 3, "request_local_ids": 1,
                      "manual_leaves": leaves(manual_fields), "request_leaves": leaves(request),
                      "action_count_unchanged": 3, "existing_numeric_automation_counted_as_new": False}
            write(case / "input-reduction.json", counts)
            check(label + " fewer entered leaves", counts["request_leaves"] < counts["manual_leaves"])
            accept(draft, native_path)
            baseline_path = case / "manual-control.plan.json"
            accept(baseline, baseline_path)
            control_path = case / "manual-control-result.json"
            run("equipment", "calc", "--plan", baseline_path, "--output", control_path, "--summary")
            delivery = run("equipment", "deliver", "--plan", native_path, "--output-dir", case / "delivery")
            write(case / "delivery-receipt.json", delivery)
            index = read(case / "delivery" / "index.json")
            paths = {a["role"]: case / "delivery" / a["path"] for a in index["artifacts"]}
            for artifact in index["artifacts"]:
                path = paths[artifact["role"]]
                check(label + " " + artifact["role"] + " complete bytes", path.stat().st_size == artifact["bytes"] and sha(path) == artifact["sha256"])
            calculated = read(paths["calculated"])["calculated"]
            result = calculated["result"]
            control = read(control_path)
            check(label + " actual execution equals manual control", result == control["result"])
            expected = 450 * multiplier
            check(label + " independent damage arithmetic", len(result["hits"]) == 3
                  and all(math.isclose(h["expected"], expected, abs_tol=1e-9) for h in result["hits"]))
            for role, formula in (("result_workbook", False), ("formula_workbook", True)):
                metadata_workbook(paths[role].read_bytes(), check, label + role, expected, formula)
            retry = run("equipment", "assemble-attack", "--plan", native_path, "--request", request_path)
            check(label + " accepted retry is idempotent", retry["assembly"]["status"] == "existing"
                  and retry["plan"] == draft and retry["report"]["state"] == "ready")
            outputs.append({"case": label, "artifacts": {k: str(v) for k, v in paths.items()}, "reduction": counts})
            print(label + " real CLI assembly and four artifacts passed", flush=True)
        check("database bytes unchanged", sha(db) == initial_sha)
        write(directory / "verification.json", {"status": "passed", "checks": checks, "check_count": len(checks),
            "database_sha256": initial_sha, "executable": executable, "deliveries": outputs,
            "scope": "两个已有受支持来源的三次显式工程动作；不证明完整队伍或新增游戏机制"})
    except Exception as exc:
        write(directory / "failure.json", {"error": type(exc).__name__, "message": str(exc), "checks": checks})
        raise


if __name__ == "__main__":
    main()
