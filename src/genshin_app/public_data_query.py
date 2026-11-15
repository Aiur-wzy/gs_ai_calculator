"""Bounded direct public-data CLI queries with protected publication paths."""
from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from pathlib import Path

from genshin_app.equipment.files import json_bytes, publish_files, read_input_bytes
from genshin_app.equipment.panel_basis import character_panel_basis
from genshin_app.equipment.schema import PlanError
from genshin_app.query_cache import protect_inputs

REQUEST_BYTES = 8192
RESPONSE_BYTES = 262144


def related_sources(db_path, value):
    """Navigate existing public ownership; never infer ability execution or joins."""
    import copy

    from genshin_app.agent_chat.character_appendix_index import (
        query_character_appendix_index,
    )
    from genshin_app.equipment.files import fingerprint
    from genshin_db import queries
    result = copy.deepcopy(value)
    line = value.get("version_line", "release")
    with queries.equipment_read_session(db_path):
        for item in result.get("items", []):
            if item.get("status") != "ok":
                continue
            kind, ident, data = item["entity_type"], item["id"], item["data"]
            if kind == "character":
                index = query_character_appendix_index(db_path, {"db_character_id": ident,
                    "version_line": line, "database_fingerprint": fingerprint(value.get("snapshot", {}))})
            else:
                targets = {(kind, ident)}
                for effect in data.get("effects", []):
                    rows = effect.get("refinements", []) if kind == "weapon" else [effect.get("effect")]
                    for row in rows:
                        if isinstance(row, dict) and type(row.get("affix_id")) is int:
                            targets.add(("equip_affix", row["affix_id"]))
                index = {"targets": [], "status": "selected_entities", "body_read": False,
                         "execution_authority": "none"}
                for target_type, target_id in sorted(targets):
                    row = {"target_type": target_type, "target_id": target_id, "version_line": line,
                           "categories": []}
                    for status in ("approved", "pending"):
                        found = queries.list_annotation_categories(db_path, target_type, target_id, status, line)
                        row["categories"].extend({**c, "status": status} for c in found["categories"])
                    index["targets"].append(row)
            for target in index.get("targets", []):
                for category in target.get("categories", []):
                    category["body_request"] = {key: target[key] for key in ("target_type", "target_id", "version_line")}
                    category["body_request"].update(status=category["status"], category=category["category"], page=1, page_size=10)
            item["related_sources"] = {"appendix_index": index,
                "not_requested": data.get("selection", {}).get("not_requested", []),
                "missing": data.get("selection", {}).get("missing", []),
                "body_read_tool": "gs_query_appendix", "body_read_cli": "db appendix --input REQUEST.json",
                "note": "角色本体annotations为空不能代表技能/命座无附录；目录只表示存在，尚未读取、解释或执行。装备目录按本次所选精炼/套装效果列出。"}
    return result


def panel_basis_capabilities():
    return {'contract': 'character-panel-basis-query-v1',
            'required': ['db_character_id'], 'optional': ['level', 'version_line', 'weapon'],
            'levels_with_data': [90, 100], 'request_bytes': REQUEST_BYTES, 'response_bytes': RESPONSE_BYTES,
            'weapon_fields': ['weapon_id', 'level', 'ascension'],
            'version_selection': '请求version_line唯一指定，默认release；无额外CLI版本线参数。',
            'scope': '调用现有基础面板查询，不计武器副属性/被动/套装，不推算成品面板；缺项明确保留。',
            'directory_access': '数据库内容只读；数据目录仍需允许既有跨进程锁。'}


def validate_panel_request(request):
    def invalid(message, path, **details):
        raise PlanError(message, "invalid_query_request", {"path": path, **details})
    fields = {'db_character_id', 'level', 'version_line', 'weapon'}
    if not isinstance(request, dict):
        invalid('基础面板请求必须是JSON对象', '/', expected_type='object',
                request_example={'db_character_id': 10000002, 'level': 90, 'version_line': 'release'})
    if request.keys() - fields:
        invalid('基础面板请求含未知字段', '/', unknown_fields=sorted(request.keys() - fields), allowed_fields=sorted(fields))
    if 'db_character_id' not in request:
        invalid('基础面板请求必须指定db_character_id', '/db_character_id', required=True)
    normalized = dict(request, level=request.get('level', 90), version_line=request.get('version_line', 'release'))
    for key, low, high in [('db_character_id', 1, 1_000_000_000), ('level', 1, 100)]:
        value = normalized[key]
        if type(value) is not int or not low <= value <= high:
            invalid(f'{key}必须为{low}–{high}的整数', '/'+key, expected_type='integer', minimum=low, maximum=high)
    if normalized['version_line'] not in ('release', 'beta'):
        invalid('version_line必须是release或beta', '/version_line', allowed_values=['release', 'beta'])
    weapon = normalized.get('weapon')
    if weapon is not None:
        if not isinstance(weapon, dict) or weapon.keys() - {'weapon_id', 'level', 'ascension'}:
            invalid('weapon只允许weapon_id/level/ascension字段', '/weapon',
                    allowed_fields=['weapon_id', 'level', 'ascension'], expected_type='object')
        # Omitted fields remain a reported missing selection, never invented.
        for key, low, high in [('weapon_id', 1, 1_000_000_000), ('level', 1, 100), ('ascension', 0, 6)]:
            if key in weapon and (type(weapon[key]) is not int or not low <= weapon[key] <= high):
                invalid(f'weapon.{key}必须为{low}–{high}的整数', '/weapon/'+key,
                        expected_type='integer', minimum=low, maximum=high)
    return normalized


def run_lookup(args, *, catalog_function, admission_function=None, read_scope=None):
    if not args.input:
        raise PlanError('资料查询需要--input请求JSON，版本线仅由请求中的version_line指定',
                        'invalid_query_request', {'path': '/input', 'repair_hint': '提供--input 请求文件.json；请求中填写version_line。'})
    request_path = Path(args.input)
    raw = read_input_bytes(request_path, maximum_bytes=REQUEST_BYTES)
    if len(raw) > REQUEST_BYTES:
        raise PlanError('资料查询请求不得超过8KiB', 'input_file_too_large', {'path': '/input', 'maximum_bytes': REQUEST_BYTES})
    try:
        request = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PlanError('资料查询需要有效的UTF-8 JSON', 'input_json_invalid', {'path': '/input'}) from exc
    db = Path(args.data_dir)/'genshin.db'
    if args.output:
        database_companions = [db.with_name('.'+db.name+'.write.lock'),
                               *(db.with_name(db.name+suffix) for suffix in ('-wal', '-shm', '-journal'))]
        protect_inputs([args.output], [request_path, db, *database_companions])
    # Validate read-only inputs and protected publication paths before startup
    # admission.  Startup may open SQLite (which can consume/remove WAL files)
    # or initialize a frozen data directory; neither is allowed when the CLI
    # request itself is invalid or points at a missing explicit database.
    if args._lookup == 'panel-basis':
        request = validate_panel_request(request)
    elif args._lookup != 'catalog':
        raise PlanError('未知资料查询操作', 'invalid_query_request', {'path': '/operation', 'allowed_values': ['catalog', 'panel-basis']})
    if not db.is_file():
        raise PlanError('数据库不存在；资料查询不会创建或更新数据库', 'database_missing',
                        {'path': '/data_dir', 'recovery': {'action': 'select_database_directory', 'retry_same_request': False}})
    scope = read_scope(args.data_dir) if read_scope is not None else nullcontext()
    with scope:
        if admission_function is not None:
            admission_function(args.data_dir)
        if args._lookup == 'catalog':
            result = catalog_function(db, request)
        else:
            result = character_panel_basis(db, **request)
    content = json_bytes(result)
    # Bound the actual pretty CLI envelope too, not just a compact inner body.
    envelope = json_bytes({'code': 0, 'message': 'ok', 'data': result})
    if max(len(content), len(envelope)) > RESPONSE_BYTES:
        raise PlanError('资料查询输出超过256KiB，未交付截断结果', 'response_too_large')
    if args.output:
        target = str(Path(args.output).resolve())
        preview = {'operation': args._lookup, 'version_line': result['version_line'],
                   'published_paths': [target], 'file_shas': {target: hashlib.sha256(content).hexdigest()}}
        if len(json_bytes({'code': 0, 'message': 'ok', 'data': preview})) > RESPONSE_BYTES:
            raise PlanError('资料输出回执超过大小限制，未发布文件', 'response_too_large')
        receipt = publish_files([(args.output, content)], overwrite=args.overwrite,
                                input_paths=[db, request_path])
        return {'operation': args._lookup, 'version_line': result['version_line'], **receipt}
    return result
