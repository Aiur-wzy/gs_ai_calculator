"""Bounded exact JSON differences; no source payload dump or normalization."""
from __future__ import annotations


def bounded_difference(actual, expected, path='/', *, max_nodes=4096, max_differences=8):
    rows, visited = [], 0
    def preview(value):
        if isinstance(value, (dict, list)):
            return {'type': type(value).__name__, 'length': len(value)}
        if isinstance(value, str) and len(value) > 160:
            return value[:160] + '…'
        return value
    def token(value):
        return str(value).replace('~', '~0').replace('/', '~1')
    def visit(a, b, at):
        nonlocal visited
        if len(rows) >= max_differences or visited >= max_nodes:
            return
        visited += 1
        if type(a) is not type(b):
            rows.append({'path': at, 'kind': 'type', 'expected': preview(b), 'actual': preview(a)})
        elif isinstance(a, dict):
            # Walk borrowed keys without allocating an unbounded union/sort.
            def keys():
                yield from a
                for key in b:
                    if key not in a:
                        yield key
            for key in keys():
                if len(rows) >= max_differences or visited >= max_nodes:
                    break
                child = at.rstrip('/') + '/' + token(key)
                if key not in a or key not in b:
                    rows.append({'path': child, 'kind': 'missing' if key not in a else 'unexpected',
                        'expected': preview(b[key]) if key in b else {'absent': True},
                        'actual': preview(a[key]) if key in a else {'absent': True}})
                else:
                    visit(a[key], b[key], child)
        elif isinstance(a, (list, tuple)):
            if len(a) != len(b):
                rows.append({'path': at, 'kind': 'length', 'expected': len(b), 'actual': len(a)})
            for index in range(min(len(a), len(b))):
                visit(a[index], b[index], at.rstrip('/') + '/' + str(index))
                if len(rows) >= max_differences or visited >= max_nodes:
                    break
        elif a != b:
            rows.append({'path': at, 'kind': 'value', 'expected': preview(b), 'actual': preview(a)})
    visit(actual, expected, path)
    return {'differences': rows, 'nodes_visited': visited,
            'truncated': visited >= max_nodes or len(rows) >= max_differences}
