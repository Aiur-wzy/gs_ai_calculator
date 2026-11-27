"""Bounded, read-only inspection of saved calculations; never invent coverage."""
from __future__ import annotations

import copy
import json
import math

from genshin_app.calculation_capacity import CALCULATED_ARTIFACT_BYTES
from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.verification_scope import verification_scope
from genshin_engine.execution_control import ExecutionCancelled

MAX_FILE_BYTES = CALCULATED_ARTIFACT_BYTES
MAX_REQUEST_BYTES = 8192
MAX_RESPONSE_BYTES = 256 * 1024
VIEWS = ("summary", "hits", "target_conditions", "mechanics", "sampling", "actions", "external", "buff_states",
         "resource_counters", "resource_counter_views", "resource_counter_frames", "resource_reactions", "attribute_callbacks",
         "resource_threshold_observers", "resource_threshold_triggers", "attributes", "static_inclusions",
         "damage_explanations", "special_states")
_FILTERS = ("character", "skill", "damage_type_id", "event_id", "action_id", "source_id", "target_id", "start", "end")
_ATTRIBUTE_FILTERS = ("stat", "basis", "receipt_id", "frame_id")


def review_contract():
    from genshin_app.damage_explanation_reporting import explanation_contract
    return {
        "contract": "calculation-review-v1", "command": "equipment review",
        "input": "已保存的equipment calc/export JSON或完整交付calculated.json；不生成新结果、不读数据库；执行证明可能进行有界离线重放核验",
        "request": {"view": list(VIEWS), "offset": "整数0..1000000，默认0",
                    "limit": "整数1..200，默认50", "filters": {
                        key: "精确身份匹配；非模糊文本搜索" for key in _FILTERS[:-2]
                    } | {"start": "含开始秒数", "end": "不含结束秒数"}},
        "output": "范围与指纹、引擎/含外部总量、计数、分页条目及原JSON位置；summary按角色/skill/类型组合分组",
        "limits": {"input_bytes": MAX_FILE_BYTES, "request_bytes": MAX_REQUEST_BYTES,
                   "response_bytes": MAX_RESPONSE_BYTES, "page_size": 200},
        "scope": "仅检查已保存文件内部绑定并呈现实际条目；不认证游戏正确、预期段数或当前数据库一致性",
        "attribute_filters": {key:"仅attributes视图接受，按实际回执精确匹配" for key in _ATTRIBUTE_FILTERS},
        "damage_explanations": explanation_contract(),
        "component_quota": {"view": "mechanics", "type": "quota",
                            "unit": "damage_component_target",
                            "lookup": "按规则来源 source_id 或伤害分项 event_id 读取同目标扣层凭据，按 component_hit_ids 关联伤害。"},
        "special_states": {"field": "special_state_timeline.states", "time_filter": "half_open_interval_intersection", "read_only": True},
        "static_inclusion_filters": {key: "仅按实际提供者或静态来源ID精确匹配；无事件时点"
                                     for key in ('character', 'source_id')},
        "filters": "所有过滤条件取交集；buff_states按区间相交过滤时间，其他视图按事件时点。attribute_callbacks的event_id指实际父命中，resource_threshold_triggers指触发资源操作的原始事件。resource_reactions的event_id指实际反应父事件，character匹配反应角色或资源来源角色，source_id指资源操作来源。无法定位身份的条目不猜测关联。",
    }


def read_bounded_json(path, maximum):
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("重复JSON字段")
            value[key] = item
        return value

    from genshin_app.equipment.files import read_input_bytes
    raw = read_input_bytes(path, maximum_bytes=maximum)
    if len(raw) > maximum:
        raise PlanError("审查输入超过字节上限", "review_size_limit", {"maximum": maximum})
    try:
        return json.loads(raw.decode("utf-8-sig"), parse_constant=lambda _: _bad("非有限JSON数值"),
                          object_pairs_hook=unique_object)
    except (PlanError, ExecutionCancelled):
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PlanError("无法读取审查JSON", "invalid_review_input") from exc


def _bad(message):
    raise PlanError(message, "invalid_review_input")


def _finite_number(value):
    if type(value) not in (int,float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _request(value):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {"view", "offset", "limit", "filters", "sections"}:
        _bad("审查请求含未知字段或不是对象")
    if len(json_bytes(value)) > MAX_REQUEST_BYTES:
        _bad("审查请求超过字节上限")
    view, offset, limit = value.get("view", "summary"), value.get("offset", 0), value.get("limit", 50)
    if view not in VIEWS:
        _bad("不支持的审查视图")
    if type(offset) is not int or not 0 <= offset <= 1_000_000:
        _bad("offset须为0..1000000的整数")
    if type(limit) is not int or not 1 <= limit <= 200:
        _bad("limit须为1..200的整数")
    filters = value.get("filters", {})
    allowed_filters = ({'character', 'source_id'} if view == 'static_inclusions' else
                       set(_FILTERS) | (set(_ATTRIBUTE_FILTERS) if view == 'attributes' else set()))
    if not isinstance(filters, dict) or set(filters) - allowed_filters:
        _bad("审查过滤条件含未知字段")
    for key, item in filters.items():
        if key in ("start", "end"):
            if not _finite_number(item) or item < 0:
                _bad("时间过滤须为非负有限秒数")
        elif not isinstance(item, str) or not item.strip() or len(item) > 512:
            _bad("身份过滤须为非空且不超过512字符的字符串")
    if "start" in filters and "end" in filters and filters["start"] >= filters["end"]:
        _bad("结束时间须晚于开始时间")
    if "sections" in value:
        from genshin_app.damage_explanation_reporting import SECTIONS
        sections = value["sections"]
        if (view != "damage_explanations" or not isinstance(sections, list) or not sections
                or any(not isinstance(s, str) or s not in SECTIONS for s in sections)
                or len(set(sections)) != len(sections)):
            _bad("sections仅用于逐击解释，须为无重复的受支持分区列表")
        return view, offset, limit, filters, sections
    return view, offset, limit, filters


@verification_scope
def _unpack(value, *, _session=None):
    if not isinstance(value, dict):
        _bad("计算文件须为对象")
    if "calculated" in value:
        from genshin_app.equipment.delivery import (
            PACKAGE_KIND,
            PACKAGE_SCHEMA,
            validate_delivery_calculated,
        )
        if value.get("package_schema") != PACKAGE_SCHEMA or value.get("kind") != PACKAGE_KIND:
            _bad("不是受支持的历史计算包")
        calculated = value["calculated"]
        checked = validate_delivery_calculated(calculated, _session=_session)
        if value.get("integrity") != checked["integrity"]:
            _bad("计算包完整性摘要不一致")
        result, receipt = calculated["result"], calculated["result_receipt"]
        validation = "full_calculation_package"
    elif any(key in value for key in ("plan", "report", "display_context", "presentation_issues")):
        from genshin_app.equipment.delivery import validate_delivery_calculated
        validate_delivery_calculated(value, _session=_session)
        result, receipt = value["result"], value["result_receipt"]
        validation = "full_calculation_package"
    else:
        from genshin_app.calculation_integrity import result_transport_parts
        try:
            result, receipt = result_transport_parts(value, _session=_session)
        except (PlanError, ExecutionCancelled):
            raise
        except ValueError as exc:
            raise PlanError(str(exc), "invalid_review_input") from exc
        validation = "saved_result_fingerprint"
    _check_bound_result(result, receipt, _session=_session)
    return result, receipt, validation


def _check_bound_result(result, receipt, *, _session):
    """Review-specific checks after the reader has verified the result binding."""
    try:
        from genshin_app.equipment.attribute_receipt import (
            validate_attribute_result_receipt,
        )
        validate_attribute_result_receipt(result, receipt)
    except (PlanError, ExecutionCancelled):
        raise
    except ValueError as exc:
        raise PlanError(str(exc), 'invalid_review_input') from exc
    from genshin_app.equipment.external_damage import (
        validate_external_damage_result_receipt,
    )
    validate_external_damage_result_receipt(result, receipt)
    from genshin_app.equipment.counter_frame_receipt import (
        validate_counter_frame_result_receipt,
    )
    try:
        from genshin_app.equipment.energy_delivery_receipt import (
            validate_automatic_energy_result_receipt,
        )
        validate_automatic_energy_result_receipt(result, receipt, _session=_session)
        validate_counter_frame_result_receipt(result, receipt, _session=_session)
        from genshin_app.equipment.resource_reaction_receipt import (
            validate_resource_reaction_result_receipt,
        )
        validate_resource_reaction_result_receipt(result, receipt, _session=_session)
    except (PlanError, ExecutionCancelled):
        raise
    except ValueError as exc:
        raise PlanError(str(exc), 'invalid_review_input') from exc
    from genshin_app.static_inclusion_reporting import has_static_inclusion_signal
    if has_static_inclusion_signal(result, receipt):
        from genshin_app.equipment.static_inclusion_receipt import (
            validate_static_inclusion_result_receipt,
        )
        try:
            validate_static_inclusion_result_receipt(result, receipt)
        except (PlanError, ExecutionCancelled):
            raise
        except ValueError as exc:
            raise PlanError(str(exc), 'invalid_review_input') from exc
    hits = result.get("hits")
    if not isinstance(hits, list) or not isinstance(result.get("summary"), dict):
        _bad("计算结果缺少命中或汇总")
    identities = set()
    for hit in hits:
        if (not isinstance(hit, dict) or not isinstance(hit.get("id"), str)
                or hit["id"] in identities or not isinstance(hit.get("character"), str)
                or not isinstance(hit.get("skill"), str)):
            _bad("命中身份缺失或重复")
        for key in ("t", "expected"):
            if not _finite_number(hit.get(key)):
                _bad("命中时点或期望伤害不合法")
        if type(hit.get("count", 1)) is not int or hit.get("count", 1) <= 0:
            _bad("已报告段数须为正整数")
        types = hit.get("damage_type_ids", [])
        if not isinstance(types, list) or any(not isinstance(t, str) for t in types):
            _bad("伤害类别须为字符串列表")
        identities.add(hit["id"])



def _lineage(result):
    mapping = {}
    for compiled in result.get("action_event_compilations", []):
        for generated in compiled.get("generated", []):
            hit_id = generated.get("hit_id")
            if hit_id:
                mapping[hit_id] = {key: generated[key] for key in
                    ("action_id", "ability_id", "prototype_hit_id", "channel_id", "ordinal", "landed")
                    if key in generated}
    return mapping


def _clip(value, limit=512):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…（原文见json_pointer）"
    if isinstance(value, list):
        return [_clip(item, limit) for item in value[:16]]
    if isinstance(value, dict):
        return {key: _clip(item, limit) for key, item in list(value.items())[:20]}
    return value


def _row(item, pointer, *, extra=None):
    keys = ("id", "event_id", "action_id", "run_id", "character", "actor", "recipient", "owner",
            "skill", "hit_name", "label", "t", "start", "end", "count", "expected", "crit", "snapshot_at",
            "damage_type_ids", "damage_system", "zones", "matched_effect_refs", "type", "status", "applied",
            "reason", "source_id", "fixed_source_id", "rule_id", "effect_id", "source", "predicate", "value",
            "property", "stat", "sample", "domain", "beneficiaries", "pool", "remaining", "consumed",
            "unit", "before", "after", "position", "frontline", "candidates", "hit_ids", "component_hit_ids",
            "selection_audit_id", "target_order",
            "coverage_key", "calculation_note", "non_overlap_note", "ability_id", "kind", "duration",
            "name_zh", "beneficiary", "instance_id", "evidence_status", "end_reason", "slices", "beneficiaries_scope", "filters",
            "layers", "system_factor", "area_id", "target_id", "scope", "knowledge", "start_boundary", "end_boundary", "expires_at", "cause", "source_refs")
    row = {key: _clip(item[key]) for key in keys if key in item}
    row.update(extra or {})
    row["json_pointer"] = pointer
    row["projection"] = "selected_fields; long strings/collections clipped; original unchanged"
    return row


def _row_size(item, pointer, extra, budget, maximum):
    """Measure clipped row before allocating its recursive projection."""
    from itertools import islice

    from genshin_app.review_batch_json import plain_json
    # Match _row's schema exactly; a bounded shallow mapping contains references.
    keys = ("id", "event_id", "action_id", "run_id", "character", "actor", "recipient", "owner",
            "skill", "hit_name", "label", "t", "start", "end", "count", "expected", "crit", "snapshot_at",
            "damage_type_ids", "damage_system", "zones", "matched_effect_refs", "type", "status", "applied",
            "reason", "source_id", "fixed_source_id", "rule_id", "effect_id", "source", "predicate", "value",
            "property", "stat", "sample", "domain", "beneficiaries", "pool", "remaining", "consumed",
            "unit", "before", "after", "position", "frontline", "candidates", "hit_ids", "component_hit_ids",
            "selection_audit_id", "target_order",
            "coverage_key", "calculation_note", "non_overlap_note", "ability_id", "kind", "duration",
            "name_zh", "beneficiary", "instance_id", "evidence_status", "end_reason", "slices", "beneficiaries_scope", "filters",
            "layers", "system_factor", "area_id", "target_id", "scope", "knowledge", "start_boundary", "end_boundary", "expires_at", "cause", "source_refs")
    fields = {key: (item[key], True) for key in keys if key in item}
    fields.update({key: (value, False) for key, value in extra.items()})
    fields.update(json_pointer=(pointer, False),
                  projection=('selected_fields; long strings/collections clipped; original unchanged', False))
    used = 0

    def add(size):
        nonlocal used
        if used + size > maximum:
            raise PlanError('审查结果超过响应限额，请缩小limit或增加过滤条件', 'review_size_limit',
                            {'maximum': maximum})
        used += size

    def measure(value, clipped):
        budget.charge(1)
        if isinstance(value, dict):
            count = min(20, len(value)) if clipped else len(value)
            add(2 + max(0, count - 1))
            for key, child in islice(value.items(), count):
                add(plain_json(key, budget, maximum=maximum - used, clone=False)[1] + 1)
                measure(child, clipped)
        elif isinstance(value, list):
            count = min(16, len(value)) if clipped else len(value)
            add(2 + max(0, count - 1))
            for child in islice(value, count):
                measure(child, clipped)
        else:
            if clipped and isinstance(value, str) and len(value) > 512:
                budget.charge(16)
                value = value[:512] + '…（原文见json_pointer）'
            add(plain_json(value, budget, maximum=maximum - used, clone=False)[1])

    add(2 + max(0, len(fields) - 1))
    for key, (value, clipped) in fields.items():
        add(plain_json(key, budget, maximum=maximum - used, clone=False)[1] + 1)
        measure(value, clipped)
    return used


def _matches(item, filters, lineage, *, intervals=False):
    eid = item.get("event_id", item.get("id"))
    links = lineage.get(eid, {})
    values = {
        "character": (item.get("character"), item.get("actor"), item.get("recipient"), item.get("owner"), item.get("beneficiary"),
                      *(item.get("beneficiaries", []) if isinstance(item.get("beneficiaries"), list) else [])),
        "skill": (item.get("skill"),), "damage_type_id": item.get("damage_type_ids", []),
        "event_id": (eid,), "action_id": (item.get("action_id"), links.get("action_id")),
        "source_id": (item.get("source_id"), item.get("fixed_source_id"), item.get("rule_id")),
        "target_id": (item.get("target_id"),),
    }
    for key, value in filters.items():
        if key in ("start", "end"):
            if intervals:
                boundary = item.get("end" if key == "start" else "start")
                if type(boundary) not in (int, float) or (boundary <= value if key == "start" else boundary >= value):
                    return False
                continue
            t = item.get("t", item.get("start"))
            if type(t) not in (int, float) or (t < value if key == "start" else t >= value):
                return False
        elif value not in values[key]:
            return False
    return True


@verification_scope
def review_calculation(value, request=None, *, _session=None):
    """The legacy public operation still validates independently on every call."""
    parsed = _request(request)
    result, receipt, validation = _unpack(value, _session=_session)
    return _project_review(result, receipt, validation, "/calculated" if "calculated" in value else "",
                           parsed, _lineage(result))


def _candidates(result, view, filters, lineage, budget, sections=None):
    from genshin_app.review_batch_json import prepay
    def scan_item(item):
        if budget is not None:
            prepay(item, budget)
    if view == "damage_explanations":
        from genshin_app.damage_explanation_reporting import explanation_sections
        document = result.get("damage_explanations")
        if not isinstance(document, dict):
            return
        for key, event in document["events"].items():
            scan_item(event)
            source = document["sources"][event["source_ref"]]
            projection = {field: event[field] for field in ("event_id", "t", "character", "skill")}
            projection.update(id=key, action_id=source.get("action_id"), source_id=event["source_ref"],
                              target_id=source.get("target_id"),
                              damage_type_ids=source.get("damage_type_ids", []), damage_system=source.get("damage_system"))
            if _matches(projection, filters, lineage):
                detail = explanation_sections(document, event, sections or ("identity", "outcome"),
                                              budget=budget, maximum_bytes=MAX_RESPONSE_BYTES)
                pointer = key.replace("~", "~0").replace("/", "~1")
                yield projection, f"/result/damage_explanations/events/{pointer}", {
                    "origin": event["outcome"]["origin"], "explanation_ref": key, "explanation": detail}
    elif view in ("hits", "summary"):
        for i, hit in enumerate(result["hits"]):
            scan_item(hit)
            if _matches(hit, filters, lineage):
                yield hit, f"/result/hits/{i}", {"origin": "engine", **lineage.get(hit["id"], {})}
        if view == "summary":
            for i, item in enumerate(result.get("external_damage", {}).get("items", [])):
                scan_item(item)
                if _matches(item, filters, lineage):
                    yield item, f"/result/external_damage/items/{i}", {"origin": "external"}
    else:
        field = {"target_conditions": "target_condition_trace", "mechanics": "mechanics_trace",
                 "sampling": "sampling_trace", "actions": "rotation_check/event_log",
                 "external": "external_damage/items", "buff_states": "buff_state_timeline/states",
                 "resource_counters":"resource_counters/trace", "resource_counter_views":"resource_counter_views/trace",
                 "resource_counter_frames":"resource_counter_views/historical_capture/capture_trace",
                 "resource_reactions":"resource_reactions/operations",
                 "attribute_callbacks":"attribute_callbacks/trace",
                 "resource_threshold_observers":"resource_threshold_observers/trace",
                 "resource_threshold_triggers":"resource_threshold_triggers/trace",
                 "attributes":"attribute_ledger/receipts",
                 "static_inclusions":"static_inclusion_compilations",
                 "special_states":"special_state_timeline/states"}[view]
        selected = result
        for part in field.split("/"):
            selected = selected.get(part, []) if isinstance(selected, dict) else []
        if not isinstance(selected, list):
            _bad("选定流水不是列表")
        reaction_rows = {}
        if view == 'resource_reactions':
            from genshin_app.reaction_resource_reporting import reaction_resource_rows
            if budget is None:
                reaction_rows = iter(reaction_resource_rows(result))
            else:
                from genshin_app.review_reaction_projection import iter_reaction_rows
                reaction_rows = iter_reaction_rows(result, budget, lineage)
        for i, item in enumerate(selected):
            if view != 'resource_reactions':
                scan_item(item)
            if not isinstance(item, dict):
                _bad("流水条目不是对象")
            projection, extra = item, {"origin": "external" if view == "external" else "engine"}
            if view == 'target_conditions':
                from genshin_engine.target_condition_evidence import (
                    resolve_condition_observation,
                )
                projection = resolve_condition_observation(item, result.get("target_state_samples", {}),
                                                           result.get("fixed_effect_sources", []))
                extra.update({key: projection[key] for key in ("target_sample_ref", "target_sample", "predicate", "source_ref")
                              if key in projection})
                remaining_filters = filters
            elif view == 'static_inclusions':
                from genshin_app.static_inclusion_reporting import (
                    static_inclusion_projection,
                )
                projection = static_inclusion_projection(item)
                extra.update({key: _clip(item[key]) for key in ('binding_id', 'declaration',
                    'source_display', 'source_authority', 'compilation_fingerprint', 'issues') if key in item})
                extra['origin'] = 'already_in_given_panel'
                remaining_filters = filters
            elif view == 'attributes':
                query = item.get('query')
                if not isinstance(query, dict) or not isinstance(query.get('frame'), dict):
                    _bad('属性回执缺少实际查询或属性帧')
                frame = query['frame']
                query_values = {'stat':query.get('stat'), 'basis':query.get('basis'),
                                'receipt_id':item.get('receipt_id'), 'frame_id':frame.get('id')}
                if any(filters[k] != query_values[k] for k in _ATTRIBUTE_FILTERS if k in filters):
                    continue
                projection = {key:item[key] for key in ('status','value') if key in item}
                projection.update(id=item.get('receipt_id'), character=query.get('actor'),
                                  stat=query.get('stat'), event_id=frame.get('event_id'))
                if 't_us' in frame:
                    time_us = frame['t_us']
                    if type(time_us) is not int or time_us < 0 or not _finite_number(time_us):
                        _bad('属性流水时间须为非负有限整数微秒')
                    projection['t'] = time_us / 1_000_000
                for key in ('contributions', 'issues'):
                    if key in item and not isinstance(item[key], list):
                        _bad('属性回执贡献与问题须为列表')
                extra.update({key:_clip(item[key]) for key in ('receipt_id','baseline','contract',
                    'qualification_scope','contributions','issues','dependency_depth') if key in item})
                extra.update(basis=_clip(query.get('basis')), purpose=_clip(query.get('purpose')),
                             frame=_clip(frame), source_window=_clip(frame.get('origin')),
                             contribution_count=len(item.get('contributions', [])))
                remaining_filters = {k:v for k,v in filters.items() if k not in _ATTRIBUTE_FILTERS}
            elif view == 'attribute_callbacks':
                projection = dict(item)
                time_us = item.get('time_us')
                if time_us is not None:
                    if type(time_us) is not int or not _finite_number(time_us):
                        _bad('回调流水时间须为有限整数微秒')
                    projection['t'] = time_us / 1_000_000
                if item.get('parent_event_id') is not None:
                    projection['event_id'] = item['parent_event_id']
                extra.update({key: _clip(item[key]) for key in ('parent_event_id', 'parent_run_id', 'time_us',
                    'callback_run_id', 'frame_id', 'batch', 'node_id', 'fingerprint', 'capture_fingerprint') if key in item})
                # "origin" normally labels engine/external results. Preserve
                # the actual source window separately without changing it.
                extra['source_window'] = _clip(item.get('origin'))
                extra['origin'] = 'engine'
                extra['source_authority'] = _clip(result.get('attribute_callbacks', {}).get('source_authority'))
                remaining_filters = filters
            elif view in ('resource_threshold_observers', 'resource_threshold_triggers'):
                from genshin_app.resource_threshold_reporting import (
                    threshold_projection,
                    threshold_time_seconds,
                )
                if budget is not None:
                    prepay(result.get(view, {}).get('source_adoptions', []), budget)
                projection = threshold_projection(item, result.get(view, {}), view)
                time_key = 't_us' if view == 'resource_threshold_observers' else 'time_us'
                if time_key in item:
                    projection['t'] = threshold_time_seconds(item, view)
                extra.update({key: _clip(item[key]) for key in ('observer_id', 'binding_id', 'before', 'after',
                    'crossed', 'threshold_atoms', 'comparison', 'unit_scale', 'quantity_unit', 'pool_id', 'key',
                    'command_kind', 'command_id', 'phase', 'stage', 'resource_receipt_id', 'resource_sequence',
                    'receipt_span', 'authority', 'source_approval', 'execution', 'outcome', 'code', 'message',
                    'source_run_id', 'source_event_id', 'dispatch_run_id', 'sampling_contract',
                    'input_kind', 'counter_id', 'counter_key', 'counter_before_cursor', 'counter_after_cursor',
                    'generation_uid', 'lifecycle_id', 'history_scope', 'resource_receipt_ids',
                    'counter_observation', 'source_origin', time_key) if key in item})
                if 'origin' in item:
                    extra['source_window'] = _clip(item['origin'])
                extra['origin'] = 'engine'
                remaining_filters = filters
            elif view == 'resource_reactions':
                from genshin_app.reaction_resource_reporting import (
                    reaction_resource_projection,
                )
                row = next(reaction_rows, None)
                if row is None or row['path'] != f'/resource_reactions/operations/{i}':
                    _bad('反应资源操作缺少受支持的实际记录')
                projection = reaction_resource_projection(row)
                extra.update({key: _clip(value) for key, value in row.items()})
                extra['origin'] = 'engine'
                remaining_filters = filters
            elif view == 'resource_counter_frames':
                extra.update({key:_clip(item[key]) for key in ('cycle','frame_id','member_count','reading_count',
                    'program_fingerprint','reader_program_fingerprint','context') if key in item})
                remaining_filters = filters
            elif view in ('resource_counters','resource_counter_views'):
                projection = dict(item)
                if 't' not in projection and type(item.get('time_us')) is int:
                    if not _finite_number(item['time_us']):
                        _bad('资源流水时间不能超出有限数值范围')
                    projection['t'] = item['time_us']/1_000_000
                resource_fields = ('counter_id','time_us','requested','resource_accepted','accepted','before','after',
                                   'key','phase','lifecycle_id','resource_receipt_id','receipt_id','source_value',
                                   'single_value','layer_count','combined_value','effect_source_id',
                                   'frame_selection','frame_id','capture_context','gate_qualification')
                extra.update({key:_clip(item[key]) for key in resource_fields if key in item})
                read = item.get('reading')
                if read is not None and not isinstance(read,dict):
                    _bad('资源取样reading须为对象')
                if isinstance(read,dict):
                    extra['reading'] = {key:_clip(read[key]) for key in ('view_id','source_id','counter_source_id','owner',
                        'counter_id','pool_id','key','raw_atoms','unit_scale','numerator','denominator','cap_applied',
                        'transform','counter_cursor','lifecycle','sampling_authority','history_scope','qualification') if key in read}
                source_filter = filters.get('source_id')
                if source_filter and source_filter not in (item.get('source_id'),item.get('rule_id'),item.get('effect_source_id'),
                        (read or {}).get('source_id'),(read or {}).get('counter_source_id')):
                    continue
                remaining_filters = {k:v for k,v in filters.items() if k!='source_id'}
            else:
                remaining_filters = filters
            if _matches(projection, remaining_filters, lineage, intervals=view in ("buff_states", "special_states")):
                yield projection, f"/result/{field}/{i}", extra


def _prepay_summary_key(item, origin, budget):
    """Reserve length inspection, sorting, copies and repeated key hashing.

    Ordinary detached strings have O(1) lengths. Charge that scan before doing
    it, then reserve comparisons at the longest string's four-byte character
    bound. Two n*bit_length(n) comparisons conservatively cover merge sorting;
    twice the string width covers both operands/equality-then-order comparison.
    Tuple/list copies and dictionary key hashing are separate prepaid work.
    """
    values = item.get('damage_type_ids', [])
    count = len(values)
    budget.charge(4 + count)
    longest, type_cost = 1, 0
    for value in values:
        cost = 1 + (4 * len(value) + 255) // 256
        longest = max(longest, cost)
        type_cost += cost
    prefix_cost = sum(1 + (4 * len(value) + 255) // 256
                      for value in (origin, item['character'], item['skill']))
    key_cost = 4 + prefix_cost + type_cost
    budget.charge(4 * count * max(1, count.bit_length()) * longest + 3 * count + 4 * key_cost)
    return key_cost


def _project_review(result, receipt, validation, pointer_prefix, parsed, lineage, budget=None, *,
                    response_maximum=MAX_RESPONSE_BYTES):
    """Private pure projection over an operation-owned, already checked snapshot."""
    from genshin_app.review_batch_json import prepay
    view, offset, limit, filters = parsed[:4]
    sections = parsed[4] if len(parsed) > 4 else None
    candidates = _candidates(result, view, filters, lineage, budget, sections)
    selection_count = 0
    if view == "summary":
        groups = {}
        maximum_key_cost = 1
        for item, _, extra in candidates:
            selection_count += 1
            if budget is not None:
                key_cost = _prepay_summary_key(item, extra['origin'], budget)
                maximum_key_cost = max(maximum_key_cost, key_cost)
            key = (extra["origin"], item["character"], item["skill"], tuple(sorted(item.get("damage_type_ids", []))))
            if key not in groups:
                if budget is not None:
                    budget.charge(32 + sum(len(x) for x in key[:3]) + sum(map(len, key[3])))
                groups[key] = {"origin": key[0], "character": key[1], "skill": key[2],
                    "damage_type_ids": list(key[3]), "event_count": 0, "declared_hit_count": 0, "expected": 0.0,
                    "first_t": item["t"], "last_t": item["t"], "example_event_ids": []}
            group = groups[key]
            group["event_count"] += 1
            group["declared_hit_count"] += item.get("count", 1)
            group["expected"] += item["expected"]
            group["first_t"] = min(group["first_t"], item["t"])
            group["last_t"] = max(group["last_t"], item["t"])
            if len(group["example_event_ids"]) < 3:
                group["example_event_ids"].append(item.get("event_id", item["id"]))
        if budget is not None:
            count = len(groups)
            # Tuple comparison may scan every long prefix and damage-type
            # string; group count alone does not bound comparison work. Pay
            # before sorted creates its list, including subsequent key lookups
            # and construction/slicing of the ordered group-reference list.
            budget.charge((4 * count * max(1, count.bit_length()) + 3 * count) * maximum_key_cost + 2 * count)
        rows = [groups[key] for key in sorted(groups)][offset:offset + limit]
    else:
        rows = []
        row_bytes = 2
        for item, pointer, extra in candidates:
            if offset <= selection_count < offset + limit:
                if budget is not None:
                    size = _row_size(item, pointer_prefix + pointer, extra, budget, response_maximum - row_bytes)
                    row_bytes += size + (1 if rows else 0)
                rows.append(_row(item, pointer, extra=extra))
            selection_count += 1
    total = len(groups) if view == "summary" else selection_count
    page = rows
    if pointer_prefix:
        for row in page:
            if "json_pointer" in row:
                row["json_pointer"] = pointer_prefix + row["json_pointer"]
    if budget is not None:
        for value in (filters, result['summary'], result.get('calculation_totals', result['summary'])):
            prepay(value, budget, maximum=response_maximum)
    output = {
        "contract": "calculation-review-v1", "view": view, "filters": copy.deepcopy(filters),
        "validation": validation,
        "scope": "历史已保存数据；内部一致性不等于游戏完整、无漏项或当前数据库仍一致。",
        "receipt": {key: receipt.get(key) for key in
            ("plan_id", "revision", "input_fingerprint", "source_fingerprint", "result_fingerprint", "calculated_at")},
        "engine_summary": copy.deepcopy(result["summary"]),
        "validation_scope": copy.deepcopy(result.get("validation_scope", {
            "summary": "旧结果未保存统一验证范围；计算接纳不代表游戏机制完整。",
            "game_mechanism_completeness": "not_independently_certified"})),
        "calculation_totals": copy.deepcopy(result.get("calculation_totals", result["summary"])),
        "totals_scope": "完整保存窗口；过滤只影响items及selection_count，不改变上方总量。",
        "status": {"mechanics": result.get("mechanics_status"),
                   "rotation": result.get("rotation_check", {}).get("status"),
                   "interpretation": result.get("rotation_check", {}).get("damage_interpretation", {}).get("kind")},
        "selection_count": selection_count, "total_rows": total, "offset": offset, "limit": limit,
        "next_offset": offset + len(page) if offset + len(page) < total else None,
        "items": page,
    }
    if view == "damage_explanations":
        document = result.get("damage_explanations") or {}
        output["explanation"] = {"contract": document.get("contract"), "status": document.get("status", "missing"),
                                 "coverage": copy.deepcopy(document.get("coverage")),
                                 "sections": list(sections or ("identity", "outcome"))}
    size = (prepay(output, budget, maximum=response_maximum) if budget is not None
            else len(json_bytes(output)))
    if size > MAX_RESPONSE_BYTES:
        raise PlanError("审查结果超过响应限额，请缩小limit或增加过滤条件", "review_size_limit",
                        {"maximum": MAX_RESPONSE_BYTES, "total_rows": total})
    return output
