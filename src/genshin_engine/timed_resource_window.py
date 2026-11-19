"""Pure periodic resource transitions, using integer engine ticks.

No character identities, game constants, damage execution, or I/O belong here.
The runtime owner commits transitions and their observations transactionally.
"""
import math
from dataclasses import dataclass, replace

from genshin_engine.particle_quantities import as_number, quantity


def resource_quantity(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def add_quantity(left, right):
    return as_number(quantity(left) + quantity(right))


@dataclass(frozen=True, slots=True)
class ResourceWindowPolicy:
    interval: int
    lifetime: int
    capacity: int

    def __post_init__(self):
        if any(type(x) is not int or x <= 0 for x in
               (self.interval, self.lifetime, self.capacity)):
            raise ValueError('resource interval, lifetime and capacity must be positive integers')


@dataclass(frozen=True, slots=True)
class ResourceWindowState:
    time: int = 0
    count: int | float = 0
    expires_at: int | None = None
    next_generation: int | None = None
    generation_until: int | None = None


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    time: int
    operation: str
    amount: int | float
    count: int | float
    expires_at: int | None


def _validate(state, policy):
    if (type(state.time) is not int or state.time < 0
            or not resource_quantity(state.count) or not 0 <= state.count <= policy.capacity
            or (state.expires_at is None) != (state.count == 0)):
        raise ValueError('invalid resource state')
    if state.expires_at is not None and (type(state.expires_at) is not int or state.expires_at <= state.time):
        raise ValueError('resource expiry must be in the future')
    if (state.next_generation is None) != (state.generation_until is None):
        raise ValueError('incomplete resource generation clock')
    if state.next_generation is not None and (
            type(state.next_generation) is not int or type(state.generation_until) is not int
            or not state.time < state.next_generation <= state.generation_until):
        raise ValueError('invalid resource generation clock')


def _grant(state, policy, time, amount):
    accepted = min(amount, add_quantity(policy.capacity, -state.count))
    count = add_quantity(state.count, accepted)
    # An actual creation refreshes the shared lifetime, including an overflow.
    state = replace(state, count=count,
                    expires_at=time + policy.lifetime if count else None)
    rows = [ResourceObservation(time, 'created', amount, count, state.expires_at)]
    if amount > accepted:
        rows.append(ResourceObservation(time, 'overflow', add_quantity(amount, -accepted), count, state.expires_at))
    return state, rows


def advance_resource_window(state, policy, time):
    """Advance chronologically; expiry precedes generation at equal ticks.

    A generation at the inclusive window endpoint executes once. Expiration
    and generation are separate observations even when they share a tick.
    """
    _validate(state, policy)
    if type(time) is not int or time < state.time:
        raise ValueError('resource time must be monotonic integer ticks')
    rows = []
    while True:
        due = [x for x in (state.expires_at, state.next_generation) if x is not None and x <= time]
        if not due:
            break
        tick = min(due)
        if state.expires_at == tick:
            rows.append(ResourceObservation(tick, 'expired', state.count, 0, None))
            state = replace(state, count=0, expires_at=None)
        if state.next_generation == tick:
            state, created = _grant(state, policy, tick, 1)
            rows.extend(created)
            upcoming = tick + policy.interval
            state = replace(state, next_generation=upcoming if upcoming <= state.generation_until else None,
                            generation_until=state.generation_until if upcoming <= state.generation_until else None)
    return replace(state, time=time), tuple(rows)


def trigger_resource_window(state, policy, time, duration):
    """Refresh only the window; preserve the existing next generation."""
    if type(duration) is not int or duration < policy.interval:
        raise ValueError('generation duration must include at least one interval')
    state, rows = advance_resource_window(state, policy, time)
    endpoint = max(time + duration, state.generation_until or 0)
    state = replace(state, next_generation=state.next_generation or time + policy.interval,
                    generation_until=endpoint)
    return state, rows


def grant_resource(state, policy, time, amount):
    if not resource_quantity(amount) or amount <= 0:
        raise ValueError('resource grant must be a finite positive quantity')
    state, rows = advance_resource_window(state, policy, time)
    state, created = _grant(state, policy, time, amount)
    return state, rows + tuple(created)


def consume_resource(state, policy, time, amount):
    if not resource_quantity(amount) or amount <= 0:
        raise ValueError('resource consumption must be a finite positive quantity')
    state, rows = advance_resource_window(state, policy, time)
    if amount > state.count:
        raise ValueError(f'insufficient resource: required={amount}, available={state.count}')
    count = add_quantity(state.count, -amount)
    state = replace(state, count=count, expires_at=state.expires_at if count else None)
    return state, rows + (ResourceObservation(time, 'consumed', amount, count, state.expires_at),)
