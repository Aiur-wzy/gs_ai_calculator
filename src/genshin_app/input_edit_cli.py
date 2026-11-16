"""Standalone file adapter for the same editable-input service as native tools."""
from __future__ import annotations

from pathlib import Path

from genshin_app.equipment.files import (
    fingerprint,
    input_view,
    json_bytes,
    publish_files,
)
from genshin_app.equipment.schema import PlanError
from genshin_app.input_editing import (
    prepare_input_edit,
    read_input_snapshot,
    validate_editable_input,
)


def command(args):
    if not args.plan or not args.request:
        raise PlanError("equipment edit需要--plan当前输入和--request修改请求", "input_edit_request")
    original = read_input_snapshot(args.plan, getattr(args, "expected_file_sha", None))
    validate_editable_input(original["input"])
    request = read_input_snapshot(args.request, maximum_bytes=8 * 1024 * 1024)["input"]
    if not isinstance(request, dict):
        raise PlanError("编辑请求须为对象", "input_edit_request")
    if set(request) - {"mode", "operations", "input", "file_path", "expected_file_sha", "expected_input_fingerprint", "expected_revision", "reason", "preview"}:
        raise PlanError("编辑请求含未知字段", "input_edit_request")
    if "expected_revision" in request and (type(request["expected_revision"]) is not int
            or request["expected_revision"] != original["input"]["revision"]):
        raise PlanError("文件输入修订已变化", "agent_stale_revision",
                        {"expected_revision": request["expected_revision"], "actual_revision": original["input"]["revision"]})
    if "preview" in request and type(request["preview"]) is not bool:
        raise PlanError("preview须为布尔值", "input_edit_request")
    source_receipt = {}
    prepared = prepare_input_edit(original["input"], request, source_receipt=source_receipt)
    changed = input_view(prepared) != input_view(original["input"])
    preview = getattr(args, "preview", False) or request.get("preview", False) or not args.output
    if type(preview) is not bool:
        raise PlanError("preview须为布尔值", "input_edit_request")
    changed_roots = sorted(key for key in prepared.keys() | original["input"].keys()
                           if prepared.get(key) != original["input"].get(key))
    response = {"contract": "game-input-file-edit-v1", "edit_status": "preview" if preview else "committed" if changed else "no_op",
        "source": {key: original[key] for key in ("path", "file_sha", "bytes")}, "import_source": source_receipt or None,
        "previous_revision": original["input"]["revision"], "revision": original["input"]["revision"],
        "changed_roots": changed_roots, "writes": False,
        "source_input_fingerprint": fingerprint(input_view(original["input"])),
        "input_fingerprint": fingerprint(input_view(prepared)), "execution_status": "not_checked",
        "note": "编辑可保存未完成草稿，不生成计算结果或批准；文件变化须显式导入当前App候选。"}
    if preview:
        return response
    if changed:
        prepared["revision"] = original["input"]["revision"] + 1
    target = Path(args.output).resolve()
    source = Path(original["path"])
    from genshin_app.query_cache import protect_inputs
    protected = [args.request, Path(args.data_dir) / "genshin.db"]
    if source_receipt:
        protected.append(source_receipt["path"])
    if target != source:
        protected.append(source)
    elif not args.overwrite:
        raise PlanError("原地保存需要--overwrite及当前文件SHA核对", "file_conflict")
    protect_inputs([target], protected)
    response["files"] = publish_files([(target, json_bytes(prepared))], overwrite=args.overwrite,
        expected_shas={target: original["file_sha"]} if target == source else None,
        input_paths=protected)
    response.update(writes=True, revision=prepared["revision"], input_fingerprint=fingerprint(input_view(prepared)))
    if changed:
        from genshin_app.agent_chat.skill_context import (
            input_change_paths,
            recommend_focus,
        )
        response["work_card_recommendation"] = recommend_focus({"revision": prepared["revision"],
            "changes": [{"revision": prepared["revision"],
                         "input_paths": input_change_paths(original["input"], prepared)}]})
    return response
