"""Independent adversarial slices; isolated fake packages and external ledgers."""
# ruff: noqa: F811 -- imported pytest fixture is injected by parameter name.
import copy
import hashlib
import json

import pytest

from tests.test_database_startup_recovery import (
    activation,
    fixed_package,  # noqa: F401
    transaction,
)


def test_recovery_must_check_newer_live_pair_before_rewriting_sidecars(fixed_package, monkeypatch):
    data, _, target, pair = fixed_package
    (data / 'genshin.db').write_bytes(b'new')
    newer_manifest = {**target, 'database_release_id': 'db-later', 'release_sequence': 3}
    newer_pair = {**pair, 'release_pair_id': 'pair-later', 'pair_sequence': 3}
    (data / activation.LIVE_MANIFEST_FILENAME).write_text(json.dumps(newer_manifest), encoding='utf8')
    (data / activation.LIVE_PAIR_MANIFEST_FILENAME).write_text(json.dumps(newer_pair), encoding='utf8')
    journal = {'contract': 'genshin-database-activation-journal-v1', 'operation_id': 'a' * 32,
        'target_digest': activation._target_digest(target, pair, 'a' * 64),
        'before': {'database_sha256': hashlib.sha256(b'old').hexdigest(), 'controls': {
            activation.LIVE_MANIFEST_FILENAME: None, activation.LIVE_RECEIPT_FILENAME: None,
            activation.LIVE_PAIR_MANIFEST_FILENAME: None}}}
    transaction.write_activation_journal(data, journal, 'unknown')
    monkeypatch.setattr(activation, 'abandon_unpublished_database_attempt', lambda *a, **k: None)
    monkeypatch.setattr(activation, 'read_database_identity', lambda *_: {'identity': 'isolated-target-bytes'})
    def rebind(directory, manifest, identity):
        (directory / activation.LIVE_MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding='utf8')
        return {}
    monkeypatch.setattr(activation, 'rebind_database_sidecars', rebind)
    before = (data / activation.LIVE_MANIFEST_FILENAME).read_bytes()
    with pytest.raises(activation.BundledDatabaseActivationError):
        activation.activate_bundled_database(data, consent_source='installer')
    assert (data / activation.LIVE_MANIFEST_FILENAME).read_bytes() == before


def test_research_adoption_can_reference_existing_topology_node(tmp_path):
    from genshin_app.equipment.mechanics import enable_mechanics
    from genshin_app.equipment.schema import new_plan
    from genshin_app.external_research import ExternalResearchService
    from tests.test_agent_execution_materialization import _topology
    from tests.test_external_research_cli import report, request, usage
    plan = enable_mechanics(new_plan())
    plan.setdefault('extensions', {})['event_topology'] = copy.deepcopy(_topology())
    path = tmp_path / 'plan.json'; path.write_text(json.dumps(plan), encoding='utf8')
    service = ExternalResearchService(tmp_path / 'profile', path, 'independent-task')
    entry = service.call('append', request(plan))
    node_id = plan['extensions']['event_topology']['nodes'][0]['node_id']
    answer = service.call('report', report(plan, [usage(entry['research_id'], node_id)]))
    assert answer['status'] == 'complete'


def test_recovery_validates_all_saved_controls_before_restoring_any(fixed_package, monkeypatch):
    data, _, target, pair = fixed_package
    live = {'database_release_id': 'db-current-old', 'release_sequence': 1}
    path = data / activation.LIVE_MANIFEST_FILENAME
    path.write_text(json.dumps(live), encoding='utf8')
    previous = {'database_release_id': 'db-original-old', 'release_sequence': 1}
    journal = {'contract': 'genshin-database-activation-journal-v1', 'operation_id': 'b' * 32,
        'target_digest': activation._target_digest(target, pair, 'a' * 64),
        'before': {'database_sha256': hashlib.sha256(b'old').hexdigest(), 'controls': {
            activation.LIVE_MANIFEST_FILENAME: json.dumps(previous),
            activation.LIVE_RECEIPT_FILENAME: '{interrupted-corrupt-json',
            activation.LIVE_PAIR_MANIFEST_FILENAME: None}}}
    transaction.write_activation_journal(data, journal, 'unknown')
    monkeypatch.setattr(activation, 'abandon_unpublished_database_attempt', lambda *a, **k: None)
    before = path.read_bytes()
    with pytest.raises(transaction.ActivationJournalError) as failure:
        activation.activate_bundled_database(data, consent_source='installer')
    assert failure.value.reason == 'database_activation_journal_invalid'
    assert path.read_bytes() == before


def test_topology_reference_expansion_does_not_accept_ordinary_text(tmp_path):
    from genshin_app.equipment.schema import PlanError, new_plan
    from genshin_app.external_research import ExternalResearchService
    from tests.test_external_research_cli import report, request, usage
    plan = new_plan()
    plan['manual_template']['meta']['name'] = 'text-is-not-a-current-identity'
    path = tmp_path / 'plan.json'; path.write_text(json.dumps(plan), encoding='utf8')
    service = ExternalResearchService(tmp_path / 'profile', path, 'independent-task')
    entry = service.call('append', request(plan))
    before = service._ledger().path.read_bytes()
    with pytest.raises(PlanError) as failure:
        service.call('report', report(plan, [usage(entry['research_id'], plan['manual_template']['meta']['name'])]))
    assert failure.value.reason == 'research_reference_out_of_scope'
    assert service._ledger().path.read_bytes() == before
