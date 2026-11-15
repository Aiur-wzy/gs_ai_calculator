"""Bounded context caching with real manual-content invalidation and entity file indexes."""
from __future__ import annotations

import json
from pathlib import Path

from genshin_app.equipment.files import fingerprint, json_bytes, publish_files
from genshin_db import queries
from genshin_db.context_query import query_context, validate_context_request


def protect_inputs(outputs, protected_paths):
    """Include publication backups/locks, which can otherwise overwrite an input."""
    from genshin_app.output_policy import validate_output_paths
    validate_output_paths(list(outputs), input_paths=tuple(protected_paths))


def cached_context(db_path, request, cache_dir=None, *, refresh=False, protected_paths=()):
    request = validate_context_request(request)
    db_path = Path(db_path).resolve()
    with queries.equipment_read_session(db_path):
        meta = queries.get_meta(db_path)
        key = fingerprint({"database": str(db_path), "request": request, "meta": meta})
        path = Path(cache_dir).resolve() / f"context-{key}.json" if cache_dir else None
        data, hit = None, False
        if path and path.is_file() and not refresh:
            try:
                if path.stat().st_size <= 16 * 1024 * 1024:
                    cached = json.loads(path.read_text(encoding="utf-8-sig"))
                    if (cached.get("key") == key and cached.get("request") == request
                            and cached.get("content_fingerprint") == fingerprint(cached.get("data"))):
                        data, hit = cached["data"], True
            except (OSError, ValueError, TypeError, AttributeError):
                # Corrupt local cache is not permission to use stale values.
                data = None
        if data is None:
            data = query_context(db_path, request)
        index = []
        if path:
            outputs = [(path, json_bytes({"key": key, "request": request, "data": data,
                                         "content_fingerprint": fingerprint(data)}))]
            for position, item in enumerate(data["items"]):
                item_path = path.parent / f"{key}-{position}-{item['entity_type']}-{item['id']}.json"
                index.append({"entity_type": item["entity_type"], "id": item["id"], "request": item["request"],
                              "status": item["status"], "path": str(item_path)})
                outputs.append((item_path, json_bytes({"snapshot": data["snapshot"], **item})))
            def intact(target, expected):
                try:
                    return target.stat().st_size == len(expected) and target.read_bytes() == expected
                except OSError:
                    return False
            # Entity projections are disposable: repair missing or edited cache files.
            if not hit or any(not intact(target, content) for target, content in outputs):
                protect_inputs([target for target, _ in outputs], [db_path, *protected_paths])
                publish_files(outputs, overwrite=True, input_paths=[db_path, *protected_paths])
        return {**data, "cache": {"hit": hit, "key": key, "path": str(path) if path else None,
                                   "entity_files": index,
                                   "validation": "数据库元信息及完整人工内容指纹；含修改和删除"}}
