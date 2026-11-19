"""Common probability/count parameters; no probability-state enumeration."""
from dataclasses import dataclass
from fractions import Fraction


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')


def probability(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Fraction)):
        raise ValueError('probability must be an explicit finite number in [0, 1]')  # noqa: TRY004 -- schema failures use ValueError
    try:
        result = value if isinstance(value, Fraction) else Fraction(str(value))
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError('invalid probability') from exc
    if not 0 <= result <= 1:
        raise ValueError('probability must be in [0, 1]')
    return result


@dataclass(frozen=True, slots=True)
class ResourceTrial:
    cost: int
    base_grant: int
    success_grant: int
    success_waives_cost: bool
    chance: Fraction
    capacity: int

    def __post_init__(self):
        for field in ('cost', 'base_grant', 'success_grant'):
            _integer(getattr(self, field), field)
        _integer(self.capacity, 'capacity', 1)
        if type(self.success_waives_cost) is not bool:
            raise ValueError('success_waives_cost must be boolean')
        object.__setattr__(self, 'chance', probability(self.chance))
