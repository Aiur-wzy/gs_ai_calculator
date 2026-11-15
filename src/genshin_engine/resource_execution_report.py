"""One bounded execution-output envelope; no fresh work budget or authority."""
from __future__ import annotations

import math

from genshin_engine.resource_definition_index import IndexError
from genshin_engine.resource_reaction_program import ReactionResourceError, _canonical

LIMITS = {'nodes': 1_500_000, 'bytes': 8*1024*1024, 'depth': 64,
          'string_chars': 65536, 'integer_bits': 4096}


def fail(code, message):
    raise ReactionResourceError('resource_reaction_report_'+code, message,
                                'resource_reactions.verification', severity='pending')


class _Envelope:
    def __init__(self, runtime):
        # Fixed traversal frames, depth-bounded active set and usage counters.
        # Large scalar canonical scratch is covered by the original prepaid
        # compiler scratch (same 65536 character/64 depth boundary); no nested
        # compiler/control/verification callback executes during this walk.
        try:
            runtime.lifecycle.budget.pay(work=1, storage=16384)
        except IndexError as exc:
            fail('limit', str(exc))
        self.charge = runtime.audit._charge
        self.active = set()
        self.usage = {'nodes': 0, 'bytes': 0, 'max_depth': 0, 'complete': False}
        runtime._execution_report_usage = self.usage

    def bytes(self, amount):
        self.usage['bytes'] += amount
        if self.usage['bytes'] > LIMITS['bytes']:
            fail('limit', '完整执行报告材料累计字节超过限制。')

    def visit(self, item, depth):
        self.charge(1)
        self.usage['nodes'] += 1
        self.usage['max_depth'] = max(depth, self.usage['max_depth'])
        if self.usage['nodes'] > LIMITS['nodes'] or depth > LIMITS['depth']:
            fail('limit', '完整执行报告材料累计节点或深度超过限制。')
        kind = type(item)
        if kind in (dict, list):
            if id(item) in self.active:
                fail('schema', '执行报告材料不能含循环引用。')
            if len(item) > LIMITS['nodes']:
                fail('limit', '执行报告材料容器超过限制。')
            self.active.add(id(item))
            syntax = 2+max(0, len(item)-1)
            self.charge(math.ceil(syntax/128))
            self.bytes(syntax)
            if kind is dict:
                for key, value in item.items():
                    if type(key) is not str:
                        fail('schema', '执行报告材料键必须是字符串。')
                    self.visit(key, depth+1)
                    self.bytes(1)
                    self.visit(value, depth+1)
            else:
                for child in item:
                    self.visit(child, depth+1)
            self.active.remove(id(item))
            return
        if kind is str and len(item) > LIMITS['string_chars']:
            fail('limit', '执行报告材料文本超过限制。')
        if kind is int and item.bit_length() > LIMITS['integer_bits']:
            fail('limit', '执行报告材料整数超过限制。')
        if kind not in (str, int, float, bool, type(None)) or kind is float and not math.isfinite(item):
            fail('schema', '执行报告材料必须是有限 JSON。')
        upper = len(item)*6+2 if kind is str else item.bit_length()+2 if kind is int else 32
        self.charge(math.ceil(upper/128))
        try:
            size = len(_canonical(item))
        except (UnicodeError, ValueError, OverflowError):
            fail('schema', '执行报告材料编码或数值无效。')
        self.bytes(size)


def validate(runtime, materials):
    """Borrow one fixed envelope of original runtime verification inputs."""
    envelope = _Envelope(runtime)
    envelope.visit(materials, 0)
    envelope.usage['complete'] = True
    return envelope.usage
