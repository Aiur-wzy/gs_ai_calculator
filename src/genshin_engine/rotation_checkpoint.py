"""In-memory scheduler continuation, never a public JSON/gameplay authority.

Copy the checkpoint and its complete execution context together, with one
deepcopy memo. Resuming against a different observer, queue or input is denied.
Only event boundaries may be captured; partially committed events never are.
"""
from dataclasses import dataclass


@dataclass(slots=True)
class RotationCheckpoint:
    bindings: tuple
    state: dict
    consumed: bool = False

    def restore(self, bindings):
        if self.consumed:
            raise ValueError('Rotation continuation has already been consumed')
        if len(bindings)!=len(self.bindings) or any(a is not b for a,b in zip(self.bindings,bindings)):
            raise ValueError('Rotation continuation belongs to a different execution state')
        self.consumed=True
        return self.state
