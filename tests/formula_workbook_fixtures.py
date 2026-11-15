"""Synthetic damage/receipt input shared by unit and real-workbook checks."""
from genshin_app.equipment.files import fingerprint
from genshin_engine import calculate, convert_v1_to_v2
from genshin_engine.mechanics import execute_mechanics


def probe_template():
    template = {"version": 1, "form": "resolved", "meta": {"assumptions": ["虚构工程样例，不代表真实队伍轴。"]},
                "loop_duration": 20, "enemy": {"level": 95, "resistance": {"default": .1}},
                "characters": [{"id": "a", "name": "测试角色甲", "level": 90,
                                "stats": {"atk": 1800, "base_atk": 800, "em": 300, "crit_rate": .5,
                                          "crit_dmg": 1.5, "energy_recharge": 1.5, "dmg_bonus": {"pyro": .4, "all": .1}}},
                               {"id": "b", "name": "测试角色乙", "level": 90,
                                "stats": {"atk": 900, "base_atk": 700, "em": 200}}],
                "switches": [{"t": 0, "character": "a"}, {"t": 4, "character": "b"}],
                "buffs": [{"id": "buff-a", "name": "工程测试增益", "source": "工程输入",
                           "start": 0, "end": 5, "scope": "frontline", "effects": [
                               {"zone": "stat_pct", "stat": "atk", "value": .2},
                               {"zone": "stat_add", "stat": "atk", "value": 100},
                               {"zone": "base_add", "stat": "all", "value": 50},
                               {"zone": "base_mult", "stat": "all", "value": 1.1},
                               {"zone": "dmg_bonus", "stat": "pyro", "value": .2},
                               {"zone": "crit_rate", "stat": "all", "value": .1},
                               {"zone": "em", "stat": "all", "value": 80},
                               {"zone": "reaction_bonus", "stat": "all", "value": .15},
                               {"zone": "reaction_flat", "stat": "all", "value": 25},
                               {"zone": "res_shred", "stat": "all", "value": .3},
                               {"zone": "def_reduction", "stat": "all", "value": .1},
                               {"zone": "lunar_base", "stat": "all", "value": .3},
                               {"zone": "lunar_mult", "stat": "all", "value": 1.2},
                               {"zone": "lunar_elevate", "stat": "all", "value": .1},
                           ]}], "hits": []}
    for i, (system, reaction) in enumerate([
        ("classic", {}), ("classic", {"type": "amplify", "factor": 1.5}),
        ("classic", {"type": "aggravate"}), ("classic", {"type": "spread"}),
        ("transformative", {"type": "swirl"}), ("lunar", {"type": "lunar_electro"}),
        ("stellar", {"type": "stellar_superconduct", "layers": 7}),
    ]):
        template["hits"].append({"t": i, "character": "a", "skill": f"工程技能{i}", "hit_name": f"测试伤害{i}",
                                 "multiplier": 2.5, "count": 2, "damage_system": system, "element": "pyro",
                                 **({"reaction": reaction} if reaction else {})})
    result = convert_v1_to_v2(template, hit_damage_types=[["damage.elemental_skill"]] * len(template["hits"]))
    result["hits"][4].update(snapshot=True, cast_t=1)
    return result


def envelope(template=None):
    template = template or probe_template()
    result = calculate(template)
    result["rotation_check"] = {"status": "unproven"}
    plan = {"plan_id": "engineering-formula-probe", "revision": 2, "name": "活公式工程验收",
            "resolved_template": template}
    return {"plan": plan, "result": result,
            "result_receipt": {"plan_id": plan["plan_id"], "revision": plan["revision"],
                               "input_fingerprint": fingerprint(plan), "output_fingerprint": fingerprint(template),
                               "result_fingerprint": fingerprint(result), "source_fingerprint": "engineering-source"},
            "equipment_summary": {"kind": "calculation_snapshot"}}


def mechanics_probe_inputs():
    evidence = {"status": "assumed", "source": "虚构工程机制，不代表真实队伍。"}
    template = {"version": 2, "form": "resolved", "id_registry_version": 1, "type_definitions": {"damage_types": []},
                "enemy": {"level": 90, "resistance": {"default": .1}}, "loop_duration": 10,
                "characters": [{"id": "a", "name": "输出角色", "level": 90, "stats": {"atk": 100, "base_atk": 50, "em": 0, "crit_rate": 0, "crit_dmg": .5, "energy_recharge": 1}},
                               {"id": "b", "name": "增益提供者", "level": 90, "stats": {"atk": 200, "base_atk": 100, "em": 0, "crit_rate": 0, "crit_dmg": .5, "energy_recharge": 1}}],
                "hits": [{"id": f"h{i}", "t": t, "character": "a", "skill": "工程技能", "hit_name": f"第{i+1}次伤害", "damage_type_ids": ["damage.elemental_skill"],
                          "damage_system": "classic", "element": "cryo", "multiplier": 1} for i, t in enumerate((.2, 1.2, 1.5))],
                "buffs": [{"id": "provider-panel", "name": "提供者原始面板收益", "start": 0, "end": 10, "scope": "team", "target": "b", "effects": [
                    {"id": "pct", "zone": "stat_pct", "stat": "atk", "value": .2, "benefit_type_id": "benefit.stat_pct.atk", "applies_to": {"mode": "all"}}]},
                    {"id": "self:bonus", "name": "输出角色手工增益", "start": 0, "end": 2, "scope": "team", "target": "a", "effects": [
                        {"id": "damage:bonus", "zone": "dmg_bonus", "stat": "all", "value": .2, "benefit_type_id": "benefit.dmg_bonus.all", "applies_to": {"mode": "all"}}]}], "switches": []}
    rotation = {"schema_version": 1, "period": 10, "max_cycles": 3, "initial_frontline": "a",
                "characters": [{"id": cid, "element": "cryo", "energy_recharge": 1, "energy_capacity": 10, "initial_energy": 10, **evidence} for cid in ("a", "b")],
                "abilities": [{"id": "e", "character": "a", "action_type_id": "action.elemental_skill", "cooldown": None, "energy": None, **evidence}],
                "actions": [{"id": "cast", "ability_id": "e", "start": 0, "occupation_end": .1, "order": 0, "repeat": {"mode": "each_cycle"}, **evidence}],
                "events": [], "initial_cooldowns": []}
    model = {"schema_version": 1, "rules": [], "hit_events": [{"hit_id": h["id"], "action_id": "cast", "order": 10 + i, "self_snapshot_event": "cast"} for i, h in enumerate(template["hits"])],
             "hit_patterns": [], "condition_events": [{"id": "change", "t": 1, "order": 1, "condition": "change", "value": True, "repeat": {"mode": "each_cycle"}, **evidence}],
             "initial_state": {"rules": []}}
    def rule(rid, benefit, value, *, owner="a", domain="self", change=False, recipient=None, duration=2):
        return {"id": rid, "name_zh": "工程机制：" + rid, "owner": owner, "source_id": rid,
                "trigger": {"event": "condition" if change else "action_start", "phase": "pre", "event_ids" if change else "action_ids": ["change" if change else "cast"]},
                "beneficiaries": {"mode": "characters", "scope": "locked", "characters": [recipient]} if recipient else {"mode": "team", "scope": "locked"},
                "timing": {"delay": 0, "duration": duration, "cooldown": 0, "cooldown_anchor": "success"},
                "stacking": {"max": 1, "add": 1, "policy": "refresh"},
                "effects": [{"id": "value", "benefit_type_id": benefit, "value": value, "domain": domain}], **evidence}
    model["rules"] = [rule("self_change", "benefit.stat_add.atk", 100, change=True, recipient="a"),
                      rule("provider_change", "benefit.stat_add.atk", 200, change=True, recipient="b"),
                      rule("quill", "benefit.base_add.all", {"kind": "stat", "subject": "owner", "stat": "atk", "factor": .1, "add": 3, "min": 10, "max": 100, "sample": "current"}, owner="b", domain="provider"),
                      rule("enemy", "benefit.res_shred.cryo", .1, domain="enemy", duration=1.5)]
    model["initial_state"]["rules"] = [{"rule_id": rule["id"], "cooldown_remaining": 0, "instances": []} for rule in model["rules"]]
    return template, rotation, model


def mechanics_envelope(inputs=None):
    template, rotation, model = inputs or mechanics_probe_inputs()
    executed = execute_mechanics(template, rotation, model)
    if executed["result"] is None:
        raise AssertionError(executed["issues"])
    result = executed["result"]
    result.update(rotation_check=executed["rotation_check"], mechanics_trace=executed["mechanics_trace"], sampling_trace=executed["sampling_trace"])
    plan = {"plan_id": "mechanics-formula-probe", "revision": 1, "name": "分域采样活公式验收", "plan_version": 3,
            "resolved_template": executed["resolved_template"], "mechanics_model": model, "rotation_model": rotation}
    return {"plan": plan, "result": result,
            "result_receipt": {"plan_id": plan["plan_id"], "revision": plan["revision"], "input_fingerprint": fingerprint([template, rotation, model]),
                               "output_fingerprint": fingerprint(plan["resolved_template"]), "result_fingerprint": fingerprint(result)},
            "report": {"mechanics_input_template": template, "mechanics_model": model}}


def energy_window_probe_inputs():
    template, rotation, model = mechanics_probe_inputs()
    model["rules"][2]["effects"][0]["value"] = {"kind": "stat", "subject": "owner", "stat": "energy_recharge", "factor": 100, "sample": "current"}
    rotation["energy_recharge_windows"] = [{"id": "er-window", "character": "b", "start": 1, "end": 2, "delta": .5,
                                            "repeat": {"mode": "each_cycle"}, "status": "assumed", "source": "工程输入"}]
    return template, rotation, model
