from __future__ import annotations

import json
import re
from pathlib import Path

from genshin_app.agent_chat import tools
from genshin_app.version import DATABASE_RELEASE_ID
from genshin_db.runtime_catalog import INDEXER_VERSION
from genshin_runtime.normalization import NORMALIZATION_VERSION

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "skill" / "references" / "data" / "database-readme.md"
PROFILE_RESOURCE = ROOT / "src" / "genshin_runtime" / "resources" / "profiles.json"


def _contract() -> tuple[str, dict[str, str]]:
    text = README.read_text(encoding="utf-8")
    match = re.search(
        r"<!-- database-readme-contract\s+(.*?)\s+-->",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, "database README must carry a version contract"
    fields: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        key, separator, value = raw_line.strip().partition("=")
        assert separator and key and value, f"invalid database README contract line: {raw_line!r}"
        assert key not in fields, f"duplicate database README contract field: {key}"
        fields[key] = value
    return text, fields


def test_database_readme_tracks_the_current_database_and_interpretation_versions():
    _, contract = _contract()

    assert contract == {
        "schema_version": "1",
        "database_release_id": DATABASE_RELEASE_ID,
        "game_version": re.fullmatch(r"db-(.+)-r\d+", DATABASE_RELEASE_ID).group(1),
        "upstream_commit": contract["upstream_commit"],
        "runtime_indexer_version": INDEXER_VERSION,
        "normalization_version": str(NORMALIZATION_VERSION),
        "last_reviewed": contract["last_reviewed"],
    }
    assert re.fullmatch(r"[0-9a-f]{40}", contract["upstream_commit"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", contract["last_reviewed"])

    profiles = json.loads(PROFILE_RESOURCE.read_text(encoding="utf-8"))["profiles"]
    assert any(contract["upstream_commit"] in profile["commits"] for profile in profiles)


def test_database_readme_is_discoverable_without_becoming_an_execution_rule():
    text, _ = _contract()
    resource = "references/data/database-readme.md"

    assert resource in tools.skill_resource_names()
    assert resource in tools._ACTION_SKILL_RESOURCES
    assert resource in tools._EVENT_SKILL_RESOURCES
    for required in (
        "## 4. 关系范式",
        "### G. 名称引用候选的 Agent 材料化",
        "### H. opaque字段与未知`$type`的Agent研究入口",
        "## 5. 当前版本能力快照",
        "## 6. Agent最短读取流程",
        "## 7. 禁止推断",
        "## 8. 版本维护检查",
        "不是数据库外键定义",
        "不授予引擎执行权",
        "不得直接复制为引擎执行规则",
        "系统计算链会使用本数据库",
        "纯公式／状态层不查询live SQLite",
        "数据库生成的派生机制候选或预填只是便利入口",
        "`engine_agent_input_gap`",
        "`agent_input=supported | partial | unsupported`",
        "event_topology",
        "numeric_model",
    ):
        assert required in text


def test_database_readme_registers_current_opaque_tokens_as_agent_evidence_only():
    text, contract = _contract()
    assert contract["upstream_commit"] == "6044f5aae7dd79fbbe15c5748ef99b654f6132b7"

    for token in (
        "BLDKFMFJDLI",
        "BFALOOLJIAE",
        "AAMMODEMOCN",
    ):
        assert f"`{token}`" in text or f"$type={token}" in text

    for required in (
        "当前库原文次数",
        "不推断未导入的Monster",
        "代表`source_path + JSON pointer`",
        "ConfigAbility_Avatar_Mitya.json",
        "/0/Default/PGMKHCKJIKM/UNIQUE_Mitya_C2_Domain_Listen/BGPBLBIFCEG/0/BLDKFMFJDLI",
        "ConfigAbility_Animal_JellyFish_N_01.json",
        "/9/Default/HBNCGKIFKJH/1/$type",
        "可直接作为检索、版本diff和研究证据",
        "不直接执行opaque节点",
        "当前方案中的一次材料化不反写公共数据库",
        "重新扫描相对上次采用快照新增、删除或改变形状的opaque字段和未知`$type`",
    ):
        assert required in text

    facts = json.loads((ROOT / "docs/pm/audits/2026-11-19-7.2.52-database-readme-facts.json").read_text(encoding="utf-8"))
    assert facts["upstream_commit"] == contract["upstream_commit"]
    assert facts["counts"]["character"] == 171
    assert facts["counts"]["skill"] == 442
    assert {row["token"] for row in facts["opaque_scan"]["entries"]} == {
        "BLDKFMFJDLI", "BFALOOLJIAE", "AAMMODEMOCN",
    }
    for row in facts["opaque_scan"]["entries"]:
        assert row["token"] in text
        assert row["samples"][0]["json_pointer"] in text
        assert re.fullmatch(r"[0-9a-f]{64}", row["samples"][0]["source_sha256"])


def test_database_readme_defers_only_nonproduction_equipment_candidates():
    text, _ = _contract()

    for required in (
        "### I. 装备非正式候选与延期边界",
        "`deferred_nonproduction_candidate`",
        "套装 `15000`",
        "武器 `10009`～`10012`",
        "共享主词条池 `1000`",
        "`23300`、`23316`、`23318`、`23339`、`23340`",
        "正常圣遗物",
        "武器 `11420`「一心传」名刀",
        "正式“游医”效果",
        "不能删除原始记录",
        "重新核对“I”节候选",
    ):
        assert required in text

    assert "不写成数据库实体状态" in text
    assert "仅缺TextMap本身不足以证明废弃" in text
