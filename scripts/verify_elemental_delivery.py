"""Reproducible public-plan engineering probe; no frozen experiment is modified."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from genshin_app.equipment import new_plan, run_plan_calc
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "outputs/engine-continuation-20261002/delivery"
    output.mkdir(parents=True, exist_ok=True)
    evidence = {"status": "assumed", "source": "工程输入：显式动作与命中秒数，不声称动画帧数或完整技能机制已经自动还原"}
    template = {"version": 2, "form": "resolved", "id_registry_version": 1, "type_definitions": {"damage_types": []},
                "enemy": {"level": 90, "resistance": {"default": .1}},
                "meta": {"name": "基础融化与元素底·工程验证", "assumptions": ["基础增幅反应工程示例：倍率归一为1、给定攻击1000，无暴击与增伤；并非角色真实DPS。"]},
                "characters": [{"id": cid, "name": name, "level": 90,
                                "stats": {"atk": 1000, "em": 0, "crit_rate": 0, "crit_dmg": .5, "energy_recharge": 1}}
                               for cid, name in (("kaeya", "凯亚"), ("diluc", "迪卢克"))],
                "hits": [{"id": cid+".hit", "character": cid, "skill": "元素战技", "hit_name": name+"·战技命中",
                          "element": element, "t": time, "multiplier": 1, "damage_system": "classic",
                          "damage_type_ids": ["damage.elemental_skill"]}
                         for cid, name, element, time in (("kaeya", "凯亚", "cryo", .2), ("diluc", "迪卢克", "pyro", 1.3))],
                "buffs": [], "switches": [], "loop_duration": 5}
    rotation = {"schema_version": 1, "period": 5, "max_cycles": 3, "initial_frontline": "kaeya",
                "characters": [{"id": cid, "element": element, "energy_recharge": 1, "energy_capacity": 60,
                                "initial_energy": 60, **evidence} for cid, element in (("kaeya", "cryo"), ("diluc", "pyro"))],
                "abilities": [{"id": cid+".e", "character": cid, "action_type_id": "action.elemental_skill", "cooldown": None,
                               "energy": None, **evidence} for cid in ("kaeya", "diluc")],
                "actions": [], "events": [], "initial_cooldowns": []}
    rotation["abilities"].append({"id": "switch", "character": "kaeya", "action_type_id": "action.switch", "cooldown": None, "energy": None, **evidence})
    for aid, ability, time, end in (("kaeya.cast", "kaeya.e", 0, .5), ("switch", "switch", 1, 1), ("diluc.cast", "diluc.e", 1.1, 1.6)):
        row = {"id": aid, "ability_id": ability, "start": time, "occupation_end": end, "order": 0,
               "repeat": {"mode": "once"}, **evidence}
        if aid == "switch":
            row["switch_to"] = "diluc"
        rotation["actions"].append(row)
    mechanics = {"schema_version": 1, "rules": [], "hit_events": [], "hit_patterns": [], "condition_events": [],
                 "initial_state": {"rules": []}, "elemental_model": {"schema_version": 1, "ruleset": "gauge-basic-v1",
                   "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known", "initial_aura": None,
                              "status": "assumed", "source": "普通可附着敌人，开场无元素底"}, "profiles": []}}
    for cid, element, gu in (("kaeya", "cryo", 2), ("diluc", "pyro", 1)):
        mechanics["hit_events"].append({"hit_id": cid+".hit", "action_id": cid+".cast", "order": 10,
                                         "application": {"profile_id": cid+".e", "target_id": "enemy", "landed": True}})
        mechanics["elemental_model"]["profiles"].append({"id": cid+".e", "element": element, "application": "apply", "gauge_units": gu,
              "icd": {"kind": "none"}, "status": "confirmed", "source": f"KQM角色机制页：普通敌人E；https://library.keqingmains.com/characters/{element}/{cid}，2026-10-02核验；未宣称当前客户端实测"})
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=mechanics)
    plan_path = output / "基础融化-原生方案.json"
    write(plan_path, plan)
    calculated = run_plan_calc(plan, root / "data/genshin.db")
    expected = [450., 900.]
    actual = [h["expected"] for h in calculated["result"]["hits"]]
    assert actual == expected, actual
    assert calculated["result"]["rotation_check"]["energy_check"]["proven_cycle"] is None
    assert calculated["result"]["elemental"]["trace"][1]["reaction"]["type"] == "melt"
    write(plan_path, calculated["plan"])
    write(output / "基础融化-结果.json", calculated)
    content = build_formula_workbook(calculated)
    (output / "基础融化-公式核对.xlsx").write_bytes(content)
    write(output / "verification.json", {"expected_damage": expected, "actual_damage": actual,
          "result": "passed", "scope": "归一倍率工程情景，真实附着资料；非完整角色动作自动化或真实DPS",
          "workbook_support": formula_workbook_support(calculated), "xlsx_sha256": hashlib.sha256(content).hexdigest()})
    print(json.dumps({"status": "passed", "directory": str(output), "damage": actual}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
