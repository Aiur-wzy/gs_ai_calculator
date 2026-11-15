"""Create an engineering-only RF-07 probe and independent perturbation controls.

Run with the bundled runtime, then verify_formula_workbook.mjs with bundled Node.
No real team answers or sealed experiment artifacts are read or changed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from formula_workbook_fixtures import (
    energy_window_probe_inputs,
    envelope,
    mechanics_envelope,
    mechanics_probe_inputs,
    probe_template,
)

from genshin_app.formula_workbook import write_formula_workbook


def main():
    out = ROOT / "outputs" / "engineering-formula-d20"
    out.mkdir(parents=True, exist_ok=True)
    calculated = envelope()
    controls = {"baseline": calculated["result"]["summary"]["total_damage"], "cases": []}
    for path, change in [("characters[0].stats.atk", 2200), ("hits[0].multiplier", 3.0),
                         ("buffs[0].effects[0].value", .3), ("characters[0].stats.em", 600),
                         ("enemy.resistance.default", .75)]:
        template = probe_template()
        if path == "characters[0].stats.atk": template["characters"][0]["stats"]["atk"] = change
        elif path == "characters[0].stats.em": template["characters"][0]["stats"]["em"] = change
        elif path == "hits[0].multiplier": template["hits"][0]["multiplier"] = change
        elif path == "buffs[0].effects[0].value": template["buffs"][0]["effects"][0]["value"] = change
        else: template["enemy"]["resistance"]["default"] = change
        controls["cases"].append({"path": path, "value": change, "expected": envelope(template)["result"]["summary"]["total_damage"]})
    receipt = write_formula_workbook(calculated, out / "formula-probe.xlsx")
    (out / "controls.json").write_text(json.dumps(controls, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "export-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    dynamic = mechanics_envelope()
    controls["mechanics"] = {"baseline": dynamic["result"]["summary"]["total_damage"], "cases": []}
    for field, value in [("characters[1].stats.atk", 400), ("mechanics_input.buffs[0].effects[0].value", .4),
                         ("mechanics_model.rules[1].effects[0].value", 300),
                         ("mechanics_model.rules[2].effects[0].value.factor", .4),
                         ("mechanics_model.rules[2].effects[0].value.factor", 0),
                         ("mechanics_model.rules[2].effects[0].value.max", 30)]:
        t, r, m = mechanics_probe_inputs()
        if field.startswith("characters"):
            t["characters"][1]["stats"]["atk"] = value
        elif field.startswith("mechanics_input"):
            t["buffs"][0]["effects"][0]["value"] = value
        elif "rules[1]" in field:
            m["rules"][1]["effects"][0]["value"] = value
        else:
            m["rules"][2]["effects"][0]["value"][field.rsplit(".", 1)[1]] = value
        controls["mechanics"]["cases"].append({"path": field, "value": value, "expected": mechanics_envelope((t, r, m))["result"]["summary"]["total_damage"]})
    write_formula_workbook(dynamic, out / "mechanics-formula-probe.xlsx")
    energy_window = mechanics_envelope(energy_window_probe_inputs())
    controls["energy_window"] = {"baseline": energy_window["result"]["summary"]["total_damage"], "cases": []}
    for field, value in [("rotation_model.energy_recharge_windows[0].delta", .75),
                         ("characters[1].stats.energy_recharge", 1.5)]:
        t, r, m = energy_window_probe_inputs()
        if field.startswith("rotation_model"):
            r["energy_recharge_windows"][0]["delta"] = value
        else:
            t["characters"][1]["stats"]["energy_recharge"] = value
            r["characters"][1]["energy_recharge"] = value
        controls["energy_window"]["cases"].append({"path": field, "value": value,
                                                  "expected": mechanics_envelope((t, r, m))["result"]["summary"]["total_damage"]})
    write_formula_workbook(energy_window, out / "energy-window-formula-probe.xlsx")
    (out / "controls.json").write_text(json.dumps(controls, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"path": receipt["path"], "baseline": controls["baseline"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
