import hashlib
import json

from test_formula_workbook import envelope, read_sheet

from genshin_app.formula_sources import local_numeric_edit, source_chunks
from genshin_app.formula_workbook import build_formula_workbook


def test_compact_audit_chunks_preserve_unicode_and_json_identity():
    value = {"文本": "原神😀" * 12000, "source": [1, 2, {"a": True}]}
    packed = source_chunks(value)
    restored = "".join(packed["chunks"])
    assert json.loads(restored) == value
    assert hashlib.sha256(restored.encode("utf-8")).hexdigest() == packed["sha256"]
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 30000 for chunk in packed["chunks"])


def test_source_index_is_compact_while_complete_audit_is_separate():
    import io
    import zipfile
    from xml.etree import ElementTree

    data = build_formula_workbook(envelope())
    sources = read_sheet(data, 4)
    assert any("完整来源JSON!" in str(cell["value"]) for cell in sources.values())
    assert len(sources) < 1000
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        tree = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        titles = [s.attrib["name"] for s in tree.findall("{*}sheets/{*}sheet")]
        assert titles[-1] == "完整来源JSON"
    summary = read_sheet(data, 1)
    assert any("未经App重新接纳" in (cell.get("formula") or "") for cell in summary.values())


def test_only_explicit_local_parameters_avoid_broad_fixed_sampling_gate():
    assert local_numeric_edit("enemy.resistance.cryo")
    assert local_numeric_edit("hits[3].multiplier")
    assert not local_numeric_edit("characters[0].stats.atk")
    assert not local_numeric_edit("mechanics_model.rules[0].effects[0].value")
