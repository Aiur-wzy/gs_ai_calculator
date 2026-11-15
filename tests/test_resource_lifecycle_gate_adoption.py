"""App v2 lifecycle declarations must never become unconditional v1 income."""
import copy

from genshin_app.equipment.resource_reactions import prepare_resource_reactions
from tests.test_resource_lifecycle_actual import values
from tests.test_resource_reaction_sources import fixture


def setup_source():
    original, _, pools, reviews = fixture()
    candidate = values()[2]
    for key in ('state_runtime', 'resource_reaction_contract'):
        original[key] = copy.deepcopy(candidate[key])
    return original, copy.deepcopy(original), pools, reviews


def test_version_two_gate_cannot_silently_downgrade_to_unconditional_income():
    original, delivered, pools, reviews = setup_source()
    issues = []
    declaration, adoption = prepare_resource_reactions(
        original, delivered, pools, lambda *v: issues.append(v), characters=['a', 'b'],
        source_reviews=reviews, context_fingerprint='context')
    # Missing lifecycle source evidence may be pending; it must retain v2.
    assert declaration == original['resource_reaction_contract']
    assert adoption['status'] == 'pending'
    assert issues
