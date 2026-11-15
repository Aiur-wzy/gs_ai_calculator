from __future__ import annotations

import hashlib

from genshin_app.agent_chat import tools


def test_skill_pages_are_redacted_before_slicing_and_have_stable_receipts(
    tmp_path, monkeypatch
):
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    source = (
        'prefix "C:\\Users\\alice\\Private Agent\\secret.json" then '
        "POST /v1/context/query and api_key=sk-cross-page-secret-123456 tail\n"
        "参见 C:\\Users\\alice\\other-secret.json。中文后续必须保留。"
    )
    (skill_root / "SKILL.md").write_text(source, encoding="utf-8")
    monkeypatch.setattr(tools, "skill_dir", lambda: skill_root)

    pages = []
    offset = 0
    while True:
        page = tools._skill(None, {
            "resource": "SKILL.md",
            "offset": offset,
            "max_chars": 13,
        })
        pages.append(page)
        assert page["offset"] == offset
        assert page["delivered_chars"] == len(page["text"])
        assert page["total"] == len(source)
        assert len(page["content_hash"]) == 64
        if page["next_offset"] is None:
            assert page["truncation_reason"] is None
            break
        assert page["truncation_reason"] == "max_chars"
        assert page["next_offset"] == offset + page["delivered_chars"]
        offset = page["next_offset"]

    delivered = "".join(page["text"] for page in pages)
    assert len(delivered) == len(source)
    assert "alice" not in delivered
    assert "Private Agent" not in delivered
    assert "sk-cross-page-secret" not in delivered
    assert "[redacted-path]" in delivered
    assert "[redacted-secret]" in delivered
    assert "POST /v1/context/query" in delivered
    assert "。中文后续必须保留。" in delivered
    assert {page["content_hash"] for page in pages} == {
        hashlib.sha256(delivered.encode("utf-8")).hexdigest()
    }
