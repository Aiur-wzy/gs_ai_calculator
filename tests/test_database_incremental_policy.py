from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_incremental_policy_preserves_publication_boundaries_without_freezing_algorithm() -> None:
    adr = _read("docs/decisions/0016-上游变更集驱动的增量数据库维护.md")
    design = _read("docs/maintenance/database-incremental-update-design.md")

    for required in (
        "增量发现、增量派生、分级失效；完整候选验收、整库原子切换",
        "不能直接修改正在服务的 SQLite",
        "当前 `src/genshin_db/pipeline.py` 仍执行全量 B 构建",
        "固定裁决与实现自由度",
        "不在本 ADR 中写死",
        "用户启动数据库收尾或具体更新任务时",
    ):
        assert required in adr

    for required in (
        "upstream-change-set-v1",
        "component-dependency-manifest-v1",
        "incremental-build-receipt-v1",
        "no_op|metadata_only|incremental|component_rebuild|full_rebuild",
        "不预设固定数字",
        "参考实现",
        "不是冻结实施配方",
        "对正在服务的`genshin.db`直接`UPDATE/DELETE`",
        "内容身份与接受快照必须分开",
        "一个可选参考",
        "自动检测只读、安装显式同意",
        "用户明确同意前不下载、不stage、不启动迁移",
    ):
        assert required in design


def test_incremental_policy_keeps_manual_knowledge_out_of_upstream_overwrite() -> None:
    design = _read("docs/maintenance/database-incremental-update-design.md")

    for required in (
        "annotation",
        "character_note",
        "mechanic semantic facts",
        "不受上游change set覆盖",
        "不再从live二次同步",
        "只应用本次获批的项目内容change set",
        "数据库知识依然没有直接执行权",
        "未申报内容未漂移",
    ):
        assert required in design


def test_current_conservative_incremental_implementation_is_reported_honestly() -> None:
    runbook = _read("docs/maintenance/database-upstream-update-runbook.md")
    status = _read("docs/pm/status.md")
    todo = _read("docs/pm/todo.md")

    assert "已经能对固定base／target生成确定性来源diff" in runbook
    assert "保守全量重建是正常结果" in runbook
    assert "数据库收尾工作源码已经实现" in status
    assert "首轮维护实现已完成" in todo
    assert "后续按真实新快照和性能收益继续优化" in todo


def test_full_rebuild_remains_a_valid_per_update_choice() -> None:
    legacy = _read("docs/decisions/0009-装备公共数据与既有数据库增量对齐.md")
    pipeline = _read("docs/design/database/pipeline.md")

    assert "长期默认算法" in legacy
    assert "当前已验证实现" in pipeline
    assert "不只用于失败回退" in pipeline
