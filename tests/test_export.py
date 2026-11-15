"""G3 导出测试：JSON/CSV/xlsx 三格式 + mock fixture 新鲜度对拍。"""

import copy
import csv
import json
import zipfile
from pathlib import Path

import pytest

from genshin_app.export import export_result, hit_rows, summary_rows
from genshin_engine import calculate

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "freeze_team.json"
FIXTURE = EXAMPLE.with_name("freeze_team_result.json")


@pytest.fixture(scope="module")
def result():
    template = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    return calculate(template)


def test_mock_fixture_fresh(result):
    """dev_mock 桥用的预算结果 fixture 必须与引擎当前输出一致（防漂移）。"""
    saved = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert saved == result


def test_summary_rows(result):
    rows = summary_rows(result)
    assert rows[0] == ["指标", "数值"]
    assert rows[1] == ["总伤害", result["summary"]["total_damage"]]
    assert any(r and r[0] == "按角色" for r in rows)
    assert any(r and r[0] == "ayaka" for r in rows)


def test_summary_rows_exposes_declared_reaction_verification(result):
    value = copy.deepcopy(result)
    value["rotation_check"] = {"damage_interpretation": {"reaction_verification": {
        "mode": "declared_not_simulated", "declared_reaction_hit_count": 2,
        "required_follow_up": "逐攻击核实并重算",
    }}}
    rows = summary_rows(value)
    assert ["反应验证", "预填声明，未由元素引擎复核"] in rows
    assert ["声明反应命中数", 2] in rows
    assert ["后续处理", "逐攻击核实并重算"] in rows


def test_hit_rows_dynamic_zone_columns(result):
    rows = hit_rows(result)
    header, body = rows[0], rows[1:]
    zone_cols = [c for c in header if c.startswith("zone.")]
    assert "zone.base" in zone_cols and "zone.crit_expected" in zone_cols
    assert len(body) == len(result["hits"])
    assert all(len(r) == len(header) for r in body)


def test_export_json_roundtrip(result, tmp_path):
    p = export_result(result, "json", tmp_path / "r.json")
    assert json.loads(p.read_text(encoding="utf-8")) == result


def test_export_csv(result, tmp_path):
    p = export_result(result, "csv", tmp_path / "r.csv")
    with p.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0][:3] == ["时间（秒）", "角色", "技能"]
    assert len(rows) == 1 + len(result["hits"])
    # 明细第一行 = 班尼特 burst，期望伤害数值落位
    assert rows[1][1] == "bennett"
    assert float(rows[1][6]) == pytest.approx(result["hits"][0]["expected"])


def test_export_xlsx_structure(result, tmp_path):
    p = export_result(result, "xlsx", tmp_path / "r.xlsx")
    with zipfile.ZipFile(p) as z:
        names = set(z.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                "xl/_rels/workbook.xml.rels", "xl/styles.xml",
                "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml",
                "xl/worksheets/sheet3.xml"} <= names
        workbook = z.read("xl/workbook.xml").decode("utf-8")
        for sheet in ("汇总", "逐事件明细", "增益覆盖率"):
            assert f'name="{sheet}"' in workbook
        sheet2 = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
        # 明细 sheet：1 表头 + 4 hit 行；中文 inlineStr 落位
        assert sheet2.count("<row ") == 5
        assert "美妙旅程" in sheet2
        # 数值单元格直写
        assert f'<v>{result["hits"][0]["expected"]}</v>' in sheet2


def test_export_unknown_format(result, tmp_path):
    with pytest.raises(ValueError, match="未知导出格式"):
        export_result(result, "pdf", tmp_path / "r.pdf")
