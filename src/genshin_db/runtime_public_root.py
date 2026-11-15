"""Complete public source material only; no mounted roots or execution rules."""
import copy

from genshin_db.runtime_catalog import (
    GLOBAL_COMBAT_PATH,
    RuntimeConfigError,
    dumps,
    extract_definitions,
    fingerprint,
)

MAX_BYTES = 2 * 1024 * 1024


def _finish(result, max_bytes):
    result["content_fingerprint"] = fingerprint(result)
    if len(dumps(result).encode("utf8")) > max_bytes:
        raise RuntimeConfigError("完整公共资料响应超过预算，未裁剪原文", "response_too_large", 413)
    return copy.deepcopy(result)


def public_root_capabilities():
    return {"projection_version": 1, "minimum_indexer_version": "7", "max_bytes": MAX_BYTES,
            "source_path": GLOBAL_COMBAT_PATH, "scope": "complete_raw_public_combat_root",
            "mount_semantics": "not_evaluated", "execution_authority": False}


def get_public_root_catalog(db_path, *, version_line="release", snapshot_id=None, max_bytes=MAX_BYTES):
    from genshin_db.runtime_queries import _definition_summary, _Nodes, _read
    from genshin_db.runtime_store import (
        validate_definition_material_references,
        validate_definition_object_references,
        validate_definition_references,
    )

    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
        raise ValueError("max_bytes必须为1至2097152的整数")
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        result = {"projection_version": 1, "snapshot": state,
                  "scope": "complete_raw_public_combat_root", "execution_authority": False,
                  "mount_status": "not_evaluated", "mounted_ability_roots": None,
                  "role_review_status": "not_evaluated", "activation_eligibility": "not_evaluated",
                  "root": None}
        if state["indexer_version"] != "7":
            return _finish({**result, "status": "missing", "reason": "public_source_not_indexed"}, max_bytes)
        sources = conn.execute(
            "SELECT source_size_bytes,source_family FROM runtime_source WHERE version_line=? AND source_path=? LIMIT 2",
            (version_line, GLOBAL_COMBAT_PATH),
        ).fetchall()
        if not sources:
            return _finish(
                {
                    **result,
                    "status": "missing",
                    "reason": "public_source_unavailable",
                },
                max_bytes,
            )
        if len(sources) != 1 or sources[0]["source_family"] != "global_combat":
            raise RuntimeConfigError("公共原配置来源缺失或不唯一", "dataset_invalid", 500)
        source = sources[0]
        if type(source[0]) is not int or not 0 <= source[0] <= max_bytes:
            raise RuntimeConfigError("公共原配置超过读取预算，未解压", "response_too_large", 413)
        # This source has exactly one raw definition. Looking up only that
        # definition's kind would silently ignore injected executable siblings.
        rows = conn.execute(
            "SELECT * FROM runtime_definition WHERE version_line=? AND source_path=? LIMIT 2",
            (version_line, GLOBAL_COMBAT_PATH),
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeConfigError("公共原配置定义缺失或不唯一", "dataset_invalid", 500)
        row, nodes = rows[0], _Nodes(conn, version_line)
        # Verify the entire source's empty edge directory as well as the root's
        # own edges; malformed indexes may attach an edge to a different owner.
        for table in ("runtime_reference", "runtime_object_reference", "runtime_material_reference"):
            if conn.execute(
                f"SELECT 1 FROM {table} WHERE version_line=? AND source_path=? LIMIT 1",
                (version_line, GLOBAL_COMBAT_PATH),
            ).fetchone():
                raise RuntimeConfigError("公共原配置不能携带执行引用", "dataset_invalid", 500)
        if row["json_pointer"] != "":
            raise RuntimeConfigError("公共配置必须绑定完整根", "dataset_invalid", 500)
        raw, origin = nodes.node(row)
        expected = extract_definitions(GLOBAL_COMBAT_PATH, "global_combat", raw, state["snapshot_id"],
                                       indexer_version=state["indexer_version"])[0]
        if any(row[key] != value for key, value in expected.items() if key != "payload"):
            raise RuntimeConfigError("公共原配置索引与原根不一致", "dataset_invalid", 500)
        for validate in (validate_definition_references, validate_definition_object_references,
                         validate_definition_material_references):
            validate(conn, version_line, row, raw, normalization=nodes.normalization,
                     indexer_version=state["indexer_version"])
        if conn.execute("SELECT 1 FROM runtime_parameter_binding WHERE version_line=? AND origin_definition_id=? LIMIT 1",
                        (version_line, row["definition_id"])).fetchone():
            raise RuntimeConfigError("公共原配置不能携带执行参数绑定", "dataset_invalid", 500)
        result.update(status="ready", reason=None,
                      root={"definition": _definition_summary(row), **origin, "raw": raw})
        return _finish(result, max_bytes)
