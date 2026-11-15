"""Bounded material traversal shared by replay copies and receipt scans."""
from __future__ import annotations

from json.encoder import encode_basestring
from math import isfinite

_SCALAR_TYPES = (str, int, float, bool, type(None))


def walk_material(value, *, role, limits, reserve_work, fail, clone=True, envelope=False,
                  add_material_bytes=None, observe=None, subtree_metrics=None):
    active, nodes, size = set(), 0, 0
    node_limit, byte_limit = limits[role + '_nodes'], limits[role + '_bytes']
    depth_limit, string_limit = limits['depth'], limits['string_chars']
    string_sizes, field_paths = {}, {}

    def reject(code, message, path, *, dimension=None, attempted=None, limit=None):
        fail(code, message, path=path, dimension=dimension, used=(size if dimension and dimension.endswith('bytes') else nodes),
             attempted=attempted, limit=limit, role=role)

    def size_add(amount, path):
        nonlocal size
        size += amount
        if size > byte_limit:
            reject('verification_size', '核验材料或返回值超过单项字节限制。', path,
                   dimension=role + '_bytes', attempted=size, limit=byte_limit)
        if add_material_bytes:
            add_material_bytes(amount, path)

    def visit(item, depth, path, *, outer=False, metric_path=None):
        nonlocal nodes
        measure = subtree_metrics is not None and metric_path is not None and depth <= 2
        if measure:
            start_nodes, start_size = nodes, size
        reserve_work(1, path)
        nodes += 1
        if nodes > node_limit:
            reject('verification_shape', '核验材料节点超过限制。', path,
                   dimension=role + '_nodes', attempted=nodes, limit=node_limit)
        if depth > depth_limit:
            reject('verification_shape', '核验材料深度超过限制。', path,
                   dimension='depth', attempted=depth, limit=depth_limit)
        kind = type(item)
        if kind in (dict, list) or kind is tuple and (role == 'output' or outer):
            if id(item) in active:
                reject('verification_shape', '核验材料有循环引用。', path)
            if len(item) > node_limit:
                reject('verification_shape', '核验材料容器超过节点限制。', path,
                       dimension=role + '_nodes', attempted=len(item), limit=node_limit)
            active.add(id(item))
            reserve_work((257 + len(item)) // 256, path)
            size_add(2 + max(0, len(item) - 1), path)
            if kind is dict:
                result = {} if clone else None
                for key, child in item.items():
                    if type(key) is not str:
                        reject('verification_shape', '核验材料字段名必须是字符串。', path)
                    segment = field_paths.get(key)
                    if segment is None:
                        segment = '/' + key.replace('~', '~0').replace('/', '~1')
                        if len(field_paths) < 2048 and len(key) < 128:
                            field_paths[key] = segment
                    child_path = (path + segment)[:1024]
                    copied_key = visit(key, depth + 1, child_path + '/@key')
                    size_add(1, child_path)
                    copied = visit(child, depth + 1, child_path,
                                   metric_path=metric_path + segment if measure and depth < 2 else None)
                    if clone:
                        result[copied_key] = copied
            else:
                result = [] if clone else None
                for index, child in enumerate(item):
                    copied = visit(child, depth + 1, path + '/' + str(index),
                                   metric_path=metric_path + '/' + str(index) if measure and depth < 2 else None)
                    if clone:
                        result.append(copied)
                if clone and kind is tuple:
                    result = tuple(result)
            active.remove(id(item))
            if measure:
                subtree_metrics[metric_path] = (size - start_size, nodes - start_nodes)
            return result
        if (kind not in _SCALAR_TYPES
                or kind is float and not isfinite(item)
                or kind is int and item.bit_length() > 4096):
            reject('verification_shape', '核验材料必须是有界有限JSON。', path)
        if kind is str and len(item) > string_limit:
            reject('verification_shape', '核验材料单字符串超过限制。', path,
                   dimension='string_chars', attempted=len(item), limit=string_limit)
        upper = len(item) * 6 + 2 if kind is str else item.bit_length() + 2 if kind is int else 32
        reserve_work((upper + 255) // 256, path)
        try:
            if kind is str:
                width = string_sizes.get(item)
                if width is None:
                    width = len(encode_basestring(item).encode('utf8'))
                    if len(string_sizes) < 4096 and len(item) < 128:
                        string_sizes[item] = width
            elif kind is bool:
                width = 4 if item else 5
            elif item is None:
                width = 4
            else:
                width = len(str(item))
        except (ValueError, UnicodeError, OverflowError):
            reject('verification_shape', '核验材料编码无效。', path)
        size_add(width, path)
        if measure:
            subtree_metrics[metric_path] = (size - start_size, nodes - start_nodes)
        return item

    try:
        detached = visit(value, 0, '/' + role, outer=envelope, metric_path='/' + role)
        return detached, size, nodes
    finally:
        if observe:
            observe(role, size, nodes)
