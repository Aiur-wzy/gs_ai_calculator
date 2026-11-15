"""从本地已冻结上游生成最小身份别名资源；不联网、不复制机制或数值。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def build(repo: Path):
    paths = [f"ExcelBinOutput/{name}ExcelConfigData.json" for name in
             ("Avatar", "Weapon", "ReliquarySet", "EquipAffix")]
    paths += [f"TextMap/TextMap_Medium{language}.json" for language in ("CHS", "EN")]
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", *paths], cwd=repo, check=True,
                   capture_output=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    content = {path: (repo / path).read_bytes() for path in paths}
    raw = {Path(path).stem: json.loads(value.decode("utf-8-sig")) for path, value in content.items()}
    chs, en = raw["TextMap_MediumCHS"], raw["TextMap_MediumEN"]
    entities = []

    def add(kind, rid, record, source_table, aliases=()):
        text_hash = str(record.get("nameTextMapHash", ""))
        if not chs.get(text_hash) or not en.get(text_hash):
            return
        entities.append({"entity_type": kind, "id": rid, "name_zh": chs[text_hash],
                         "name_text_map_hash": text_hash, "source_table": source_table,
                         "source_record_id": record.get("affixId", record.get("id", rid)),
                         "aliases": sorted({en[text_hash], *aliases})})

    for row in raw["AvatarExcelConfigData"]:
        icon = row.get("iconName", "")
        aliases = ([icon.removeprefix("UI_AvatarIcon_")]
                   if icon.startswith("UI_AvatarIcon_") and row.get("useType") == "AVATAR_FORMAL"
                   else [])
        add("character", row["id"], row, "AvatarExcelConfigData", aliases)
    for row in raw["WeaponExcelConfigData"]:
        add("weapon", row["id"], row, "WeaponExcelConfigData")
    effects = {}
    for row in sorted(raw["EquipAffixExcelConfigData"], key=lambda r: (r.get("level", 0), r["affixId"])):
        if chs.get(str(row.get("nameTextMapHash", ""))):
            effects.setdefault(row["id"], row)
    for row in raw["ReliquarySetExcelConfigData"]:
        effect = effects.get(row.get("EquipAffixId"))
        if effect:
            add("artifact_set", row["setId"], effect, "EquipAffixExcelConfigData")
    return {"schema_version": 1, "upstream_url": "https://gitlab.com/GuraFoundation/YuanShenResources",
            "upstream_commit": commit,
            "source_files": {path: hashlib.sha256(value).hexdigest() for path, value in content.items()},
            "policy": "身份兼容别名；只有编号及中文名同时一致才启用。不是当前版本线来源证明。",
            "entities": sorted(entities, key=lambda r: (r["entity_type"], r["id"]))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.repo.resolve())
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"entities": len(result["entities"]), "upstream_commit": result["upstream_commit"]}))
