#!/usr/bin/env python3
"""Build the reviewed AYAKA benchmark retained/archive file plans.

This command is deliberately read-only with respect to the benchmark cases.  It
hashes every current file below AYAKA-OPT-01/02/03, classifies it as either a
Git-retained review artifact or local process-history archive material, and
writes two machine-readable plans:

* an exhaustive retained-set ledger containing both dispositions; and
* an archive_repository_history.py compatible archive/delete plan.

The fixed counts and byte totals make source drift fail closed.  Review this
script and both generated JSON files before running the archive tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHUNK_SIZE = 4 * 1024 * 1024
CASES = ("ayaka-opt-01", "ayaka-opt-02", "ayaka-opt-03")

EXPECTED = {
    "ayaka-opt-01": {
        "total_files": 527,
        "total_bytes": 71_165_699,
        "retain_files": 148,
        "retain_bytes": 10_053_641,
        "archive_files": 379,
        "archive_bytes": 61_112_058,
    },
    "ayaka-opt-02": {
        "total_files": 429,
        "total_bytes": 99_662_986,
        "retain_files": 102,
        "retain_bytes": 16_942_530,
        "archive_files": 327,
        "archive_bytes": 82_720_456,
    },
    "ayaka-opt-03": {
        "total_files": 911,
        "total_bytes": 387_277_461,
        "retain_files": 255,
        "retain_bytes": 72_592_189,
        "archive_files": 656,
        "archive_bytes": 314_685_272,
    },
}

FINAL_ARTIFACTS = {
    "final-plan.json",
    "final-resolved.json",
    "final-result.json",
    "final-receipt.json",
    "final-template.xlsx",
    "candidate-ledger.json",
    "rotation.md",
    "issues.json",
    "lookups.jsonl",
    "web_findings.json",
    "audit.json",
    "index.md",
}
RUN_HOST_PROVENANCE = {
    "artifact-seal.json",
    "freeze.json",
    "public-manifest.json",
    "session-input.json",
    "summary.json",
}
RUN_SESSION_PROVENANCE = {"request.json", "summary.json", "final.md", "thread-start.json"}
SUCCESSFUL_RUN = {
    "ayaka-opt-01": "sol-high-r2",
    "ayaka-opt-02": "sol-max-r1",
    "ayaka-opt-03": "sol61-high-r1-recovery1",
}
CASE_FINAL_EXTRAS = {
    "ayaka-opt-01": {"timeline-r4.md"},
    "ayaka-opt-02": {"final-composed.json", "timeline-r6.md", "verification.json"},
    "ayaka-opt-03": {
        "final-delivery-check.json",
        "final-reopen-verification.json",
        "final-review.md",
        "final-workbook-mapping.json",
        "final-workbook-verification.json",
        "inspection-9aefa23a0946475892d0ba3032f537e4-1.png",
        "timeline-r1.md",
        "timeline-r9.md",
        "r9-conversion-notes.md",
        "r9-stamina-audit.json",
    },
}

OPT02_APP_REVIEW = {
    "independent-app-review.json",
    "panel-perturbations.json",
    "r5-r6-ablation.json",
    "repeat-proof-review.json",
}
OPT02_EXCEL_REVIEW = {
    "app-perturbation-comparison.json",
    "artifact-audit.json",
    "events-calculation.png",
    "events-left.png",
    "excel-review.json",
    "parameters.png",
    "README.md",
    "report.md",
    "sources-scope.png",
    "summary.png",
}
OPT03_APP_REVIEW = {
    "comparison.json",
    "display-comparison-check.json",
    "display-comparison.json",
    "display-compose-check.json",
    "display-copy-initial-pending.json",
    "display-copy-initial-pending.md",
    "display-copy-initial-pending.plan.json",
    "display-copy.json",
    "display-copy.md",
    "display-dependency-review.json",
    "display-final-preservation.json",
    "display-gui.json",
    "display-patch.json",
    "display-preservation.json",
    "display-shortcut-com.json",
    "display-shortcut-reviewed-host.json",
    "display-shortcut-reviewed-retry.json",
    "display-shortcut-reviewed.json",
    "preflight.json",
    "preservation.json",
    "report.json",
    "report.md",
    "validate-data.json",
    "validate.json",
}


REWRITE_ACTIONS = [
    {
        "document": "benchmarks/followup/ayaka-opt-01/README.md",
        "reason": "The text says later candidates/raw trials remain in the live artifacts directory.",
        "replacement": "Say final/selected files remain in Git and all intermediate candidates are members of the AYAKA process-history archive manifest.",
        "archived_targets": ["benchmarks/followup/ayaka-opt-01/runs/sol-high-r2/artifacts/{draft,composed,resolved,result,check}-*"],
    },
    {
        "document": "benchmarks/followup/ayaka-opt-01/review/interim-issues.md",
        "reason": "A live Markdown link targets the archived paused r1 candidate.",
        "replacement": "Replace the link with the archive manifest and member path.",
        "archived_targets": ["benchmarks/followup/ayaka-opt-01/runs/sol-high-r1/artifacts/candidate-v1-plan.json"],
    },
    {
        "document": "benchmarks/followup/ayaka-opt-01/review/r2/report.md",
        "reason": "The report links the complete artifacts directory and app-replay controls, both of which become wholly or partly archived.",
        "replacement": "Keep links to final retained files and point candidate/replay evidence to the archive manifest member paths.",
        "archived_targets": [
            "benchmarks/followup/ayaka-opt-01/review/app-replay-r2/controls.json",
            "benchmarks/followup/ayaka-opt-01/runs/sol-high-r2/artifacts/<intermediate candidates>",
        ],
    },
    {
        "document": "benchmarks/followup/ayaka-opt-01/review/r2/candidate-r1-audit.md",
        "reason": "The retained review conclusion names archived r1 candidate and result files as live artifacts.",
        "replacement": "Add an archive-manifest pointer without rewriting the immutable JSON audit.",
        "archived_targets": [
            "benchmarks/followup/ayaka-opt-01/runs/sol-high-r2/artifacts/composed-r1-v5.json",
            "benchmarks/followup/ayaka-opt-01/runs/sol-high-r2/artifacts/result-r1-v5.json",
        ],
    },
    {
        "document": "benchmarks/followup/ayaka-opt-02/README.md",
        "reason": "The active README presents the top-level host workspace as a live repository path.",
        "replacement": "Point host/workspace provenance to the archive manifest; keep public case, frozen input, delivery, and retained run provenance links live.",
        "archived_targets": ["benchmarks/followup/ayaka-opt-02/host/**"],
    },
    {
        "document": "benchmarks/followup/ayaka-opt-02/review/r1/report.md",
        "reason": "The report says the artifacts directory contains the full candidate chain and cites host/evidence as live.",
        "replacement": "Describe the retained final set separately and point full candidates/host evidence to archive member paths.",
        "archived_targets": [
            "benchmarks/followup/ayaka-opt-02/runs/sol-max-r1/artifacts/<intermediate candidates>",
            "benchmarks/followup/ayaka-opt-02/host/evidence/**",
        ],
    },
    {
        "document": "benchmarks/followup/ayaka-opt-03/README.md",
        "reason": "The README says the complete raw submission remains in the live tree.",
        "replacement": "Say reviewable delivery/final files remain in Git and the complete raw process is in the verified local archive.",
        "archived_targets": ["benchmarks/followup/ayaka-opt-03/runs/**/<process files>"],
    },
    {
        "document": "benchmarks/followup/ayaka-opt-03/delivery/0.27.0/使用说明.md",
        "reason": "The 'four artifact delivery' link targets archived original delivery-r9.",
        "replacement": "Use delivery/0.27.0/app/index.json for the live display delivery and add the archive member path for the byte-original r9 delivery.",
        "archived_targets": ["benchmarks/followup/ayaka-opt-03/runs/sol61-high-r1-recovery1/artifacts/delivery-r9/index.json"],
    },
    {
        "document": "benchmarks/followup/ayaka-opt-03/review/r1/app/display-copy-initial-pending.md",
        "reason": "A live Markdown link targets archived display-compose stdout.",
        "replacement": "Replace the link with the archive manifest and exact member path.",
        "archived_targets": ["benchmarks/followup/ayaka-opt-03/review/r1/app/display-compose.stdout.json"],
    },
    {
        "document": "benchmarks/followup/ayaka-opt-03/review/r1/report.md",
        "reason": "The original r9 delivery link targets an archived process copy.",
        "replacement": "Link the archive manifest member for the byte-original r9; keep the curated delivery link separate.",
        "archived_targets": ["benchmarks/followup/ayaka-opt-03/runs/sol61-high-r1-recovery1/artifacts/delivery-r9/index.json"],
    },
    {
        "document": "benchmarks/followup/ayaka-opt-03/review/r1/workflow-review.md",
        "reason": "The active workflow review describes original/intermediate run artifacts and both host workspaces as live retained material.",
        "replacement": "Keep final provenance links live and point process/facility/recovery workspaces and old deliveries to archive member paths.",
        "archived_targets": [
            "benchmarks/followup/ayaka-opt-03/host/**",
            "benchmarks/followup/ayaka-opt-03/host-recovery/**",
            "benchmarks/followup/ayaka-opt-03/runs/sol61-high-r1*/artifacts/<intermediate files>",
        ],
    },
    {
        "document": "benchmarks/followup/ayaka-opt-03/delivery/0.28.0-supplement/REPORT.md",
        "reason": "The preservation statement implies every old raw file remains in the Git worktree; supplement inputs move to the archive.",
        "replacement": "Distinguish Git-retained supplement review/output files from byte-original archived inputs and cite the archive manifest.",
        "archived_targets": ["benchmarks/followup/ayaka-opt-03/review/supplement-028/inputs/**"],
    },
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False, suffix=".tmp"
    ) as stream:
        stream.write(payload)
        temp_path = Path(stream.name)
    os.replace(temp_path, path)


def classify(case: str, relative: Path) -> tuple[str, str]:
    """Return (disposition, reason) for a case-relative file path."""

    parts = relative.parts
    name = relative.name
    if len(parts) == 1:
        return "retain", "case_top_level_contract_or_index"
    if parts[0] in {"frozen", "frozen-r2"}:
        return "retain", "frozen_skill_fixture_or_capability"
    if parts[0] == "public":
        return "retain", "public_case_input"
    if parts[0] == "delivery":
        return "retain", "human_or_app_delivery"

    if parts[0] == "runs":
        if len(parts) == 4 and parts[2] == "host" and name in RUN_HOST_PROVENANCE:
            return "retain", "minimal_run_provenance"
        if len(parts) == 4 and parts[2] == "session" and name in RUN_SESSION_PROVENANCE:
            return "retain", "minimal_session_provenance"
        if len(parts) >= 4 and parts[1] == SUCCESSFUL_RUN[case] and parts[2] == "artifacts":
            if parts[3] == "facts":
                return "retain", "selected_run_fact_cache"
            if len(parts) == 4 and name in FINAL_ARTIFACTS:
                return "retain", "selected_final_artifact"
            if len(parts) == 4 and name in CASE_FINAL_EXTRAS[case]:
                return "retain", "selected_final_direct_evidence"
        return "archive", "intermediate_candidate_replay_or_run_workspace"

    if parts[0] in {"host", "host-recovery"}:
        return "archive", "reproducible_host_test_workspace"

    if parts[0] == "review":
        if case == "ayaka-opt-01":
            if len(parts) >= 2 and parts[1] == "app-replay-r2":
                return "archive", "large_post_run_app_replay"
            return "retain", "review_conclusion_or_direct_evidence"

        if case == "ayaka-opt-02":
            if len(parts) <= 3:
                return "retain", "review_conclusion"
            if parts[2] == "app":
                if name in OPT02_APP_REVIEW:
                    return "retain", "compact_independent_app_review"
                return "archive", "large_app_replay_or_generated_result"
            if parts[2] == "excel":
                if name in OPT02_EXCEL_REVIEW:
                    return "retain", "compact_excel_review_or_visual_evidence"
                return "archive", "large_workbook_inventory_or_review_workspace"
            return "retain", "review_conclusion_or_direct_evidence"

        if case == "ayaka-opt-03":
            if parts[1] == "r1":
                if len(parts) <= 3:
                    return "retain", "review_conclusion"
                if parts[2] in {"error-review", "facility"}:
                    return "retain", "direct_error_or_facility_evidence"
                if parts[2] == "app":
                    if len(parts) >= 4 and parts[3] == "fresh-delivery":
                        return "archive", "duplicate_fresh_delivery_replay"
                    if name in OPT03_APP_REVIEW:
                        return "retain", "compact_app_review_evidence"
                    return "archive", "large_app_replay_or_generated_stdout"
                if parts[2] == "excel":
                    if name.endswith(".ndjson"):
                        return "archive", "large_streamed_workbook_inventory"
                    # These two small scripts are live report links and directly reproduce the review.
                    return "retain", "excel_review_evidence_or_reproducer"
                return "retain", "review_conclusion_or_direct_evidence"
            if parts[1] == "supplement-028":
                if len(parts) >= 3 and parts[2] == "inputs":
                    return "archive", "supplement_input_copy"
                return "retain", "supplement_review_conclusion_or_direct_evidence"

    return "archive", "nonselected_process_material"


def summarize(records: list[dict[str, Any]], case: str) -> dict[str, int]:
    selected = [record for record in records if record["case"] == case]
    retained = [record for record in selected if record["disposition"] == "retain"]
    archived = [record for record in selected if record["disposition"] == "archive"]
    return {
        "total_files": len(selected),
        "total_bytes": sum(record["bytes"] for record in selected),
        "retain_files": len(retained),
        "retain_bytes": sum(record["bytes"] for record in retained),
        "archive_files": len(archived),
        "archive_bytes": sum(record["bytes"] for record in archived),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="repository root (default: inferred from this script)",
    )
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    followup_root = workspace / "benchmarks" / "followup"

    records: list[dict[str, Any]] = []
    for case in CASES:
        case_root = followup_root / case
        if not case_root.is_dir():
            raise FileNotFoundError(case_root)
        for path in sorted((item for item in case_root.rglob("*") if item.is_file()), key=lambda p: p.as_posix()):
            relative = path.relative_to(case_root)
            disposition, reason = classify(case, relative)
            records.append(
                {
                    "path": path.relative_to(workspace).as_posix(),
                    "case": case,
                    "disposition": disposition,
                    "reason": reason,
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "preserve_original_bytes": True,
                }
            )

    paths = [record["path"] for record in records]
    if len(paths) != len(set(paths)):
        raise RuntimeError("the classified AYAKA source set contains duplicate paths")
    dispositions = {record["disposition"] for record in records}
    if dispositions != {"retain", "archive"}:
        raise RuntimeError(f"unexpected or incomplete disposition set: {sorted(dispositions)}")

    summaries = {case: summarize(records, case) for case in CASES}
    for case in CASES:
        if summaries[case] != EXPECTED[case]:
            raise RuntimeError(
                f"{case} source drifted; reviewed={EXPECTED[case]!r}, current={summaries[case]!r}"
            )

    aggregate = {
        key: sum(summaries[case][key] for case in CASES)
        for key in (
            "total_files",
            "total_bytes",
            "retain_files",
            "retain_bytes",
            "archive_files",
            "archive_bytes",
        )
    }
    if aggregate["total_files"] != aggregate["retain_files"] + aggregate["archive_files"]:
        raise RuntimeError("file coverage assertion failed")
    if aggregate["total_bytes"] != aggregate["retain_bytes"] + aggregate["archive_bytes"]:
        raise RuntimeError("byte coverage assertion failed")

    generated_at = datetime.now(UTC).isoformat()
    retained_plan = {
        "schema_version": 1,
        "plan_id": "2026-10-21-ayaka-retained-set",
        "generated_at": generated_at,
        "policy": {
            "goal": "Keep reviewable products and direct evidence in Git; preserve the complete process byte-for-byte in verified local archives.",
            "all_source_files_are_immutable_evidence": True,
            "archive_before_delete": True,
            "archive_delete_requires_archive_repository_history_verification": True,
            "classification_implementation": "scripts/maintenance/build_ayaka_retained_set_plan.py",
        },
        "summaries": {**summaries, "aggregate": aggregate},
        "assertions": {
            "source_file_set_unique": True,
            "retain_archive_mutually_exclusive": True,
            "retain_archive_cover_all_source_files": True,
            "file_count_identity": f"{aggregate['total_files']} = {aggregate['retain_files']} + {aggregate['archive_files']}",
            "byte_count_identity": f"{aggregate['total_bytes']} = {aggregate['retain_bytes']} + {aggregate['archive_bytes']}",
        },
        "rewrite_actions": REWRITE_ACTIONS,
        "historical_reference_policy": "Do not rewrite sealed JSON/audit evidence containing old paths. Active Markdown navigation and current-state wording must instead cite the archive manifest plus exact member path.",
        "files": records,
    }

    archive_groups = []
    for case in CASES:
        sources = [
            record["path"]
            for record in records
            if record["case"] == case and record["disposition"] == "archive"
        ]
        archive_groups.append(
            {
                "id": case,
                "kind": "benchmark_process_history",
                "archive": f"{case}.zip",
                "archive_sources": sources,
                "delete_sources": sources,
                "require_full_archive_coverage": True,
                "metadata": {
                    "source_case": f"benchmarks/followup/{case}",
                    "files": summaries[case]["archive_files"],
                    "uncompressed_bytes": summaries[case]["archive_bytes"],
                    "retained_set_plan": "benchmarks/archive-manifests/2026-10-21-ayaka-retained-set.json",
                },
            }
        )

    archive_plan = {
        "schema_version": 1,
        "archive_id": "2026-10-21-ayaka-process-history",
        "archive_root": "archives/2026-10-21-ayaka-process-history",
        "manifest_path": "benchmarks/archive-manifests/2026-10-21-ayaka-process-history.json",
        "allowed_source_roots": [f"benchmarks/followup/{case}" for case in CASES],
        "inventory_roots": [f"benchmarks/followup/{case}" for case in CASES],
        "retained_sources": [
            record["path"] for record in records if record["disposition"] == "retain"
        ],
        "groups": archive_groups,
    }

    retained_path = workspace / "benchmarks/archive-manifests/2026-10-21-ayaka-retained-set.json"
    archive_path = workspace / "benchmarks/archive-manifests/2026-10-21-ayaka-process-history-plan.json"
    atomic_write_json(retained_path, retained_plan)
    atomic_write_json(archive_path, archive_plan)
    print(json.dumps({"retained_plan": retained_path.as_posix(), "archive_plan": archive_path.as_posix(), "summary": aggregate}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
