"""Pyro source adoption requires its own exact node and three-element receipt."""
import copy
import json
from pathlib import Path

import pytest

# ruff: noqa: F811
from genshin_db.runtime_catalog import RuntimeConfigError, fingerprint
from genshin_db.runtime_queries import query_runtime
from tests.test_runtime_reaction_damage import (  # noqa: F401
    SOURCE,
    change,
    fake_repo,
    reaction_db,
    stub_fetcher,
)


@pytest.fixture
def pyro_db(reaction_db, fake_repo):
    node = json.loads((Path(__file__).parent / 'fixtures/runtime_reaction_swirl_pyro.json').read_text('utf8'))
    change(reaction_db, fake_repo, SOURCE, lambda rows: rows.append(node))
    return reaction_db


def test_fire_projects_its_own_ordinary_origin_not_an_ice_or_water_alias(pyro_db):
    r = query_runtime(pyro_db, {'action': 'reaction_damage_profile', 'element': 'pyro'})
    assert r['selection'] == {'reaction_type': 'swirl', 'element': 'pyro', 'branch': 'ordinary', 'scope': 'origin_target'}
    assert r['projection']['damage_sequence'] == [1, 1] + [0] * 8
    assert r['projection']['reset_cycle_seconds_rounded'] == .5
    assert r['execution']['sequence_status'] == 'ready'
    assert r['execution']['ability_execution'] == 'pending'
    assert r['mapping_evidence']['recipe_id'] == 'ordinary-swirl-pyro-origin-source-224708ed-v2'
    assert '/Swirl_Fire/' in r['sources']['origin_attack']['json_pointer']
    assert '_Alter' not in r['sources']['origin_attack']['json_pointer']
    assert r['sources']['ability']['node_sha256'] == '72be132c1b06615b7066934620f6eceed84899b402800968196715613533d41e'
    assert r['projection']['factor']['raw'] == pytest.approx(.6)


def test_missing_fire_node_cannot_borrow_existing_two_sequences(reaction_db):
    with pytest.raises(RuntimeConfigError) as exc:
        query_runtime(reaction_db, {'action': 'reaction_damage_profile', 'element': 'pyro'})
    assert exc.value.reason == 'not_found'


@pytest.mark.parametrize('mutation', ['changed', 'duplicate'])
def test_unreviewed_or_ambiguous_fire_source_is_refused(pyro_db, fake_repo, mutation):
    def apply(rows):
        if mutation == 'changed':
            rows[-1]['Default']['unreviewed'] = True
        else:
            rows.append(copy.deepcopy(rows[-1]))
    change(pyro_db, fake_repo, SOURCE, apply)
    with pytest.raises(RuntimeConfigError) as exc:
        query_runtime(pyro_db, {'action': 'reaction_damage_profile', 'element': 'pyro'})
    assert exc.value.reason == ('unsupported_source' if mutation == 'changed' else 'ambiguous_reference')


def test_pyro_recipe_query_does_not_mutate_legacy_recipe_identity(pyro_db):
    before = {e: query_runtime(pyro_db, {'action': 'reaction_damage_profile', 'element': e}) for e in ('hydro', 'cryo')}
    query_runtime(pyro_db, {'action': 'reaction_damage_profile', 'element': 'pyro'})
    after = {e: query_runtime(pyro_db, {'action': 'reaction_damage_profile', 'element': e}) for e in before}
    assert fingerprint(before) == fingerprint(after)
    assert all(r['mapping_evidence']['recipe_id'] == 'ordinary-swirl-origin-source-224708ed-v2' for r in after.values())
