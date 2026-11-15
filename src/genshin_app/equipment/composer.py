"""Read-only equipment composition; original text is evidence, never executable code."""
from __future__ import annotations

import copy
from pathlib import Path

from genshin_app.equipment.compose_actions import (
    prepare_compose_actions,
)
from genshin_app.equipment.compose_admission import (
    ComposeCompilationArtifacts,
    finalize_compose_admission,
)
from genshin_app.equipment.compose_agent_overlay import prepare_agent_execution_overlay
from genshin_app.equipment.compose_baseline import (
    prepare_compose_baseline,
)
from genshin_app.equipment.compose_compilation import build_compilation_artifacts
from genshin_app.equipment.compose_context import (
    initialize_compose_context,
)
from genshin_app.equipment.compose_effective import (
    apply_compose_overrides,
    validate_effective_execution_graph,
)
from genshin_app.equipment.compose_loadout import (
    resolve_compose_loadout,
)
from genshin_app.equipment.compose_pipeline import (
    ComposePass,
    ComposeRequest,
    run_compose_pipeline,
)
from genshin_app.equipment.compose_post_materialization import (
    finalize_post_materialization,
)
from genshin_app.equipment.compose_sources import (
    compile_compose_sources,
)
from genshin_app.equipment.data_version import equipment_data_version
from genshin_app.equipment.report_projector import project_equipment_report
from genshin_db import queries
from genshin_db.locking import database_lock

from .panel_calculation import build_character_basis
from .schema import SLOTS, PlanError, validate_plan


def prepare_character_input(db_path, db_character_id, level, version_line="release"):
    db_path = Path(db_path)
    if isinstance(level, bool) or not isinstance(level, int) or level <= 0:
        raise PlanError("角色等级必须为正整数")
    with database_lock(db_path):
        meta = queries.get_meta(db_path)
        detail = queries.get_character_detail(db_path, db_character_id, version_line)
        return {"character_ref": {"db_character_id": db_character_id, "version_line": version_line},
                "level": level, "basis": build_character_basis(meta, detail, db_character_id, level, version_line),
                "weapon": None, "artifacts": {s: {"state": "empty", "reason": "尚未装备，明确不计入"} for s in SLOTS},
                "manual_contributions": [], "data_version": equipment_data_version(meta, version_line)}






def _admit_compilation(artifacts: ComposeCompilationArtifacts):
    decision = finalize_compose_admission(artifacts.admission_inputs)
    return {
        **artifacts.projection_inputs,
        "state": decision.state,
        "resolution": decision.resolution,
    }


def _project_final_report(request, projection_inputs):
    """Include optional feedback before the final artifact is fingerprinted."""
    from genshin_app.default_assistance import safe_default_assistance

    report = project_equipment_report(**projection_inputs)
    report["default_assistance"] = safe_default_assistance(request.plan, request.db_path, report=report)
    report["panel_assistance"] = report["default_assistance"]["panels"]
    return report


def compose_equipment_plan(
    plan, db_path, *, candidate_session=None, _formal_provisional=False,
    _resolved_provisional_uses=None, _readonly_provisional_context=None,
):
    validate_plan(plan)
    db_path = Path(db_path)
    # One shared read transaction reuses equipment validation and sees one snapshot.
    # A missing path must never be opened as a new SQLite database.
    scope = queries.equipment_read_session(db_path) if db_path.is_file() else database_lock(db_path)
    with scope:
        return _compose(
            plan, db_path, candidate_session=candidate_session,
            _formal_provisional=_formal_provisional,
            _resolved_provisional_uses=_resolved_provisional_uses,
            _readonly_provisional_context=_readonly_provisional_context,
        )


def _compose(
    plan, db_path, *, candidate_session=None, _formal_provisional=False,
    _resolved_provisional_uses=None, _readonly_provisional_context=None,
):
    """Run the one formal pipeline inside the caller's existing read scope."""

    request = ComposeRequest(
        plan=copy.deepcopy(plan),
        db_path=db_path,
        candidate_session=candidate_session,
        formal_provisional=_formal_provisional,
        resolved_provisional_uses=_resolved_provisional_uses,
        readonly_provisional_context=_readonly_provisional_context,
    )
    result, _trace = run_compose_pipeline(request, (
        ComposePass(
            pass_id="input-context-v1",
            produces=("context",),
            handler=lambda current, _artifacts: {
                "context": initialize_compose_context(current.plan),
            },
        ),
        ComposePass(
            pass_id="action-preparation-v1",
            requires=("context",),
            produces=("action_preparation",),
            handler=lambda current, artifacts: {
                "action_preparation": prepare_compose_actions(
                    artifacts.get("context"), current.db_path),
            },
        ),
        ComposePass(
            pass_id="loadout-resolution-v1",
            requires=("context", "action_preparation"),
            produces=("loadout_resolution",),
            handler=lambda current, artifacts: {
                "loadout_resolution": resolve_compose_loadout(
                    artifacts.get("context"), current.db_path,
                    candidate_session=current.candidate_session,
                    formal_provisional=current.formal_provisional,
                    resolved_provisional_uses=current.resolved_provisional_uses,
                    readonly_provisional_context=current.readonly_provisional_context,
                ),
            },
        ),
        ComposePass(
            pass_id="source-compilation-v1",
            requires=("context", "action_preparation", "loadout_resolution"),
            produces=("source_compilation",),
            handler=lambda current, artifacts: {
                "source_compilation": compile_compose_sources(
                    artifacts.get("context"), artifacts.get("action_preparation"),
                    artifacts.get("loadout_resolution"), current.db_path),
            },
        ),
        ComposePass(
            pass_id="mechanics-baseline-v1",
            requires=("context", "action_preparation", "source_compilation"),
            produces=("mechanics_baseline",),
            handler=lambda current, artifacts: {
                "mechanics_baseline": prepare_compose_baseline(
                    artifacts.get("context"), artifacts.get("action_preparation"),
                    artifacts.get("source_compilation"), current.db_path),
            },
        ),
        ComposePass(
            pass_id="agent-execution-overlay-v1",
            requires=("context", "action_preparation", "mechanics_baseline"),
            produces=("agent_overlay",),
            handler=lambda _current, artifacts: {
                "agent_overlay": prepare_agent_execution_overlay(
                    artifacts.get("context"), artifacts.get("action_preparation"),
                    artifacts.get("mechanics_baseline")),
            },
        ),
        ComposePass(
            pass_id="post-materialization-finalizers-v1",
            requires=("context", "action_preparation", "source_compilation",
                      "mechanics_baseline", "agent_overlay"),
            produces=("post_materialization",),
            handler=lambda _current, artifacts: {
                "post_materialization": finalize_post_materialization(
                    artifacts.get("context"), artifacts.get("action_preparation"),
                    artifacts.get("source_compilation"), artifacts.get("mechanics_baseline"),
                    artifacts.get("agent_overlay")),
            },
        ),
        ComposePass(
            pass_id="calculation-override-v1",
            requires=("context", "post_materialization"),
            produces=("effective_graph",),
            handler=lambda current, artifacts: {
                "effective_graph": apply_compose_overrides(
                    artifacts.get("context"), artifacts.get("post_materialization"),
                    current.db_path),
            },
        ),
        ComposePass(
            pass_id="effective-graph-validation-v1",
            requires=("context", "mechanics_baseline", "effective_graph"),
            produces=("effective_validation",),
            handler=lambda _current, artifacts: {
                "effective_validation": validate_effective_execution_graph(
                    artifacts.get("context"), artifacts.get("mechanics_baseline"),
                    artifacts.get("effective_graph")),
            },
        ),
        ComposePass(
            pass_id="compilation-envelope-v1",
            requires=("context", "action_preparation", "source_compilation", "mechanics_baseline",
                      "post_materialization", "effective_graph", "effective_validation"),
            produces=("compiled_artifacts",),
            handler=lambda _current, artifacts: {
                "compiled_artifacts": build_compilation_artifacts(
                    artifacts.get("context"), artifacts.get("action_preparation"),
                    artifacts.get("source_compilation"), artifacts.get("mechanics_baseline"),
                    artifacts.get("post_materialization"), artifacts.get("effective_graph"),
                    artifacts.get("effective_validation")),
            },
        ),
        ComposePass(
            pass_id="admission-audit-finalizer-v1",
            requires=("compiled_artifacts",),
            produces=("report_projection_inputs",),
            handler=lambda _current, artifacts: {
                "report_projection_inputs": _admit_compilation(
                    artifacts.get("compiled_artifacts")),
            },
        ),
        ComposePass(
            pass_id="report-projector-v1",
            requires=("report_projection_inputs",),
            produces=("final_report",),
            handler=lambda current, artifacts: {"final_report":
                _project_final_report(current, artifacts.get("report_projection_inputs"))},
        ),
    ))
    return result




def apply_data_update(plan, db_path):
    validate_plan(plan)
    updated = copy.deepcopy(plan)
    db_path = Path(db_path)
    scope = queries.equipment_read_session(db_path) if db_path.is_file() else database_lock(db_path)
    with scope:
        meta = queries.get_meta(Path(db_path))
        for equipment in updated["equipment_inputs"].values():
            ref = equipment.get("character_ref")
            line = (ref or equipment["data_version"] or {}).get("version_line", "release")
            equipment["data_version"] = equipment_data_version(meta, line)
            if ref and equipment.get("mode") != "panel" and equipment["basis"]["mode"] == "database":
                detail = queries.get_character_detail(Path(db_path), ref["db_character_id"], line)
                equipment["basis"] = build_character_basis(
                    meta, detail, ref["db_character_id"], equipment["level"], line)
        # This is a draft operation; the GUI/user commits the revision upon applying it.
        return _compose(updated, Path(db_path))
