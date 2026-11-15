"""Prepaid plain-JSON traversal for the new review operation only.

Units: one per visited node (including keys), plus one per 256 worst-case
encoded bytes; integer decimal conversion prepays squared digit-bound blocks.
Every repeated alias is visited/charged again. Scalar conversion
and container construction are prepaid. This is not a physical memory bound.
"""
from __future__ import annotations

import json
import math
import threading

from genshin_app.calculation_capacity import CALCULATED_ARTIFACT_BYTES
from genshin_app.equipment.schema import PlanError

SNAPSHOT_BYTES = CALCULATED_ARTIFACT_BYTES
# Frozen gen17 uses 5,466,125 plain-JSON nodes with all 505 explanations.
# Eight million retains useful structural headroom without widening response
# pages or the independent verification/Agent budgets.
SNAPSHOT_NODES = 8_000_000
SNAPSHOT_WORK = 24_000_000
PROJECTION_WORK = 8_000_000
BATCH_BYTES = 2 * 1024 * 1024
REQUEST_BYTES = 64 * 1024
DEPTH = 64


def invalid(message):
    raise PlanError(message, 'invalid_review_input')


class Budget:
    def __init__(self, maximum, role):
        self.maximum, self.role, self.used = maximum, role, 0
        self.owner, self.closed, self.stopped = threading.current_thread(), False, False
        self._encoding_input = None

    def charge(self, amount):
        if self.owner is not threading.current_thread() or self.closed or self.stopped:
            raise PlanError('批量审查操作已结束或线程不符', 'review_batch_scope')
        if amount < 0 or self.used + amount > self.maximum:
            self.stopped = True
            raise PlanError('批量审查累计工作预算不足', 'review_work_limit', {'role': self.role})
        self.used += amount

    def close(self):
        self.closed = True
        self._encoding_input = None


def plain_json(value, budget, *, maximum=SNAPSHOT_BYTES, nodes=SNAPSHOT_NODES, clone=True):
    """Return (detached value, canonical UTF-8 size), without arbitrary protocols.

    Strings are immutable and shared, not re-encoded into huge temporaries.
    Concurrent mutation while copying is not an atomic-snapshot guarantee.
    """
    active, count, size = set(), 0, 0

    def add(n):
        nonlocal size
        if size + n > maximum:
            raise PlanError('批量审查JSON超过资料或响应字节上限', 'review_size_limit',
                            {'role': budget.role, 'maximum': maximum})
        size += n

    def walk(item, depth):
        nonlocal count
        budget.charge(1)
        count += 1
        if depth > DEPTH or count > nodes:
            invalid('批量审查JSON节点或嵌套超过上限')
        kind = type(item)
        if kind in (dict, list):
            if id(item) in active or len(item) > nodes - count:
                invalid('批量审查JSON循环或容器超过节点上限')
            budget.charge((len(item) + 257) // 256)
            add(2 + max(0, len(item) - 1))
            active.add(id(item))
            out = {} if kind is dict else []
            if kind is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        invalid('JSON字段名必须是普通字符串')
                    copied_key = walk(key, depth + 1)
                    add(1)
                    copied = walk(child, depth + 1)
                    if clone:
                        out[copied_key] = copied
            else:
                for child in item:
                    copied = walk(child, depth + 1)
                    if clone:
                        out.append(copied)
            active.remove(id(item))
            return out if clone else item
        if kind is str:
            # Raw length is a lower bound; reject before any conversion or scan.
            if len(item) + 2 > maximum - size:
                add(len(item) + 2)
            budget.charge((6 * len(item) + 257) // 256)
            length = 2
            for char in item:
                code = ord(char)
                if 0xD800 <= code <= 0xDFFF:
                    invalid('JSON字符串包含不合法Unicode代理码位')
                length += (2 if char in '\\"\b\f\n\r\t' else 6 if code < 32 else
                           1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4)
            add(length)
        elif kind is int:
            # Decimal conversion is bounded before str(), including huge ints.
            bits = item.bit_length()
            digits_upper = (bits * 30103) // 100000 + 2
            digits_lower = max(1, ((bits - 1) * 30102) // 100000)
            if digits_lower > maximum - size:
                add(digits_lower)
            budget.charge((digits_upper * digits_upper + 255) // 256)
            try:
                converted = str(item)
            except ValueError as exc:
                raise PlanError('JSON整数超出本地十进制转换限制', 'invalid_review_input') from exc
            add(len(converted))
        elif kind is float:
            budget.charge(1)
            if not math.isfinite(item):
                invalid('JSON数值必须有限')
            add(len(json.dumps(item, allow_nan=False)))
        elif kind is bool:
            budget.charge(1)
            add(4 if item else 5)
        elif item is None:
            budget.charge(1)
            add(4)
        else:
            invalid('批量审查只接受普通dict/list/str/int/float/bool/null')
        return item

    try:
        result = walk(value, 0)
    except RuntimeError as exc:
        invalid(f'复制期间JSON容器变化：{type(exc).__name__}')
    return result, size


def prepay(value, budget, *, maximum=SNAPSHOT_BYTES):
    """Pay bounded traversal plus the forthcoming scan/copy/encoding workload."""
    before = budget.used
    _, size = plain_json(value, budget, maximum=maximum, clone=False)
    budget.charge(budget.used - before)
    return size


def encode(value, budget, *, maximum, pretty=False, compact=True):
    """Preflight exact canonical/CLI formatting bytes before allocating text.

    Newline printed by the CLI is counted by its caller. Dict sorting is paid
    separately from JSON traversal; no field supplied by a document is trusted.
    """
    # Only the private batch builder can leave this one-use reservation. Its
    # detached output is never exposed before the trusted finalizer consumes it.
    # This is encoding work, not a validation token or cross-input approval.
    reserved = budget._encoding_input
    budget._encoding_input = None
    if reserved is not None and reserved[0] is value:
        budget.charge(0)
        size = reserved[1]
        if size > maximum:
            raise PlanError('批量响应超过字节上限', 'review_size_limit', {'maximum': maximum})
    else:
        size = prepay(value, budget, maximum=maximum)

    def formatting(item, depth):
        nonlocal size
        budget.charge(1)
        kind = type(item)
        if kind not in (dict, list):
            return
        count = len(item)
        if kind is dict:
            budget.charge(count * max(1, count.bit_length()))
        extra = 0
        if pretty and count:
            extra += count + 1 + 2 * (depth + 1) * count + 2 * depth
        if not compact or pretty:
            extra += count if kind is dict else 0
            if not pretty:
                extra += max(0, count - 1)
        budget.charge((extra + 255) // 256)
        if size + extra > maximum:
            raise PlanError('批量响应含最终外壳超过字节上限', 'review_size_limit', {'maximum': maximum})
        size += extra
        for child in item.values() if kind is dict else item:
            formatting(child, depth + 1)

    formatting(value, 0)
    kwargs = {'ensure_ascii': False, 'sort_keys': compact, 'allow_nan': False}
    if pretty:
        kwargs['indent'] = 2
    elif compact:
        kwargs['separators'] = (',', ':')
    raw = json.dumps(value, **kwargs).encode('utf-8')
    if len(raw) != size:
        raise PlanError('批量审查编码长度预检不一致', 'review_encoding_mismatch')
    return raw


def embed_json(fields, key, encoded, budget, *, maximum):
    """Private wrapper for an already encoded body; metadata is fully checked.

    Copying bytes is charged by 256-byte blocks before allocating the joined
    buffer. The existing body is never decoded/re-encoded or treated as approval.
    """
    if type(encoded) is not bytes or type(fields) is not dict or key in fields:
        invalid('内部JSON包装参数无效')
    prepay(fields, budget, maximum=maximum)
    count = len(fields) + 1
    budget.charge(count * max(1, count.bit_length()))
    keys = sorted([*fields, key])
    chunks, size = [b'{'], 2 + max(0, len(keys) - 1)
    if size + len(encoded) > maximum:
        raise PlanError('批量响应含外壳超过字节上限', 'review_size_limit', {'maximum': maximum})
    for index, name in enumerate(keys):
        field_key = encode(name, budget, maximum=maximum)
        field_value = encoded if name == key else encode(fields[name], budget, maximum=maximum)
        size += len(field_key) + 1 + len(field_value)
        if size > maximum:
            raise PlanError('批量响应含外壳超过字节上限', 'review_size_limit', {'maximum': maximum})
        if index:
            chunks.append(b',')
        chunks.extend((field_key, b':', field_value))
    chunks.append(b'}')
    budget.charge((size + 255) // 256)
    return b''.join(chunks)


def read_json(path, maximum):
    from genshin_app.equipment.files import read_input_bytes
    raw = read_input_bytes(path, maximum_bytes=maximum)
    if len(raw) > maximum:
        raise PlanError('批量审查输入文件超过字节上限', 'review_size_limit', {'maximum': maximum})

    def pairs(values):
        result = {}
        for key, item in values:
            if key in result:
                invalid('JSON含重复字段')
            result[key] = item
        return result

    try:
        return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs,
                          parse_constant=lambda _: invalid('JSON数值必须有限'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PlanError('无法读取批量审查JSON', 'invalid_review_input') from exc
