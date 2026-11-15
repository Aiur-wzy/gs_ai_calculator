"""Pure actual formula delegation stays independent of the pinned oracle."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_special_reactions import contributor

from genshin_engine import special_reaction_reference as reference
from genshin_engine import special_reactions as actual


@pytest.mark.parametrize("family", ["lunar_electro", "lunar_crystallize"])
def test_weighted_result_matches_full_frozen_reference_and_keeps_dynamic_factor_hook(monkeypatch, family):
    rows = [contributor("a", em=500, crit_rate=.5, crit_dmg=1), contributor("b", em=100)]
    expected = reference.calculate_weighted_special_damage(family, rows)
    assert actual.calculate_weighted_special_damage(family, rows) == expected
    original = actual._factor_for

    def different_factor(kind, **kwargs):
        system, factor = original(kind, **kwargs)
        return system, factor * 2

    monkeypatch.setattr(actual, "_factor_for", different_factor)
    assert reference.calculate_weighted_special_damage(family, rows) == expected
    assert actual.calculate_weighted_special_damage(family, rows)["expected"] == pytest.approx(2 * expected["expected"])
    assert "_formula_services" not in reference.calculate_weighted_special_damage.__code__.co_names


def test_direct_result_and_event_resolution_keep_the_canonical_formula_graph(monkeypatch):
    person = contributor("a", em=500, crit_rate=.5, crit_dmg=1)
    event = {"type": "special_reaction_damage_request", "reaction_base": False,
             "reaction_type": "stellar_superconduct", "multiplier": 3, "layers": 4, "t": 0}
    expected = reference.resolve_special_damage_event(event, [person], scaling=1000)
    assert actual.resolve_special_damage_event(event, [person], scaling=1000) == expected

    def reject(*_args, **_kwargs):
        raise ValueError("actual formula fault")

    monkeypatch.setattr(actual, "_calculate_direct_special_damage_impl", reject)
    assert reference.resolve_special_damage_event(event, [person], scaling=1000) == expected
    with pytest.raises(ValueError, match="actual formula"):
        actual.resolve_special_damage_event(event, [person], scaling=1000)


def test_formula_leaf_imports_no_runtime_app_database_or_gui():
    root = Path(actual.__file__).parent
    tree = ast.parse((root / "special_damage_formulas.py").read_text(encoding="utf-8"))
    project_imports = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and str(node.module).startswith("genshin")
    }
    assert project_imports == set()
