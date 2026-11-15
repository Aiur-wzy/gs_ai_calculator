"""Tiny EC23 raw sources; catalog approval is explicitly test-only."""
import copy
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import pytest

from genshin_db.runtime_queries import get_runtime_status
from genshin_db.semantic_store import import_bundle
from genshin_runtime.mechanism_semantics import fingerprint
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_object_index import build, write

# ruff: noqa: F811
ROOT = Path(__file__).resolve().parents[1]
DEEPWOOD = 'artifact.skill-burst-contact-dendro-res-shred'
IRON = tuple('weapon.elemental-contact-dmg.r' + str(r) for r in range(1, 6))
IDS = (DEEPWOOD, *IRON)


def contact_seed():
    return json.loads((ROOT / 'src/genshin_db/resources/mechanisms/reviewed-mechanisms-v4.json').read_text('utf8'))


def install_contact_raw(repo):
    """Append exact raw nodes, keeping all unrelated fixture source rows."""
    evidence = json.loads((ROOT / 'tests/fixtures/mechanism_contact_nodes.json').read_text('utf8'))
    groups = defaultdict(list)
    for node in evidence['nodes']:
        groups[node['source_path']].append(node)
    for path, nodes in groups.items():
        file = repo / path
        data = json.loads(file.read_text('utf8')) if file.exists() else {} if nodes[0]['definition_kind'] == 'talent_config' else []
        for node in nodes:
            if node['definition_kind'] == 'talent_config':
                data[node['pointer'][1:]] = node['raw']
            elif node['definition_kind'] == 'ability':
                data.append({'Default': node['raw']})
            else:
                data.append(node['raw'])
        write(repo, path, data)
    return repo


@pytest.fixture
def contact_repo(fake_repo):
    return install_contact_raw(fake_repo)


def fixture_contact_catalog(db, *, approve=False):
    seed = contact_seed()
    state = get_runtime_status(db)
    by_id = {}
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        for descriptor in seed['descriptors']:
            binding = descriptor['payload']['source_binding']
            for node in binding['nodes']:
                rows = conn.execute("SELECT * FROM runtime_definition WHERE version_line='release' AND source_path=? AND definition_kind=? AND node_sha256=?",
                                    (node['source_path'], node['definition_kind'], node['node_sha256'])).fetchall()
                assert len(rows) == 1, node
                node['json_pointer'] = rows[0]['json_pointer']
                node['source_sha256'] = conn.execute("SELECT source_sha256 FROM runtime_source WHERE version_line='release' AND source_path=?", (node['source_path'],)).fetchone()[0]
                item = next(e for e in binding['evidence'] if e['id'] == node['id'])
                item.update(locator=node['source_path'] + '#' + node['json_pointer'], sha256=node['node_sha256'])
            binding['upstream_commit'] = state['upstream_commit']
            binding['normalization_fingerprint'] = fingerprint(state['normalization'])
            by_id[descriptor['id']] = binding
    for record in [*seed['facts'], *seed['descriptors']]:
        ident = next(i for i in IDS if record['id'] == i or record['id'].startswith(i + '.'))
        binding = by_id[ident]
        record['evidence'] = copy.deepcopy(binding['evidence'])
        record['applicability'] = {'version_line': 'release', 'upstream_commit': state['upstream_commit'], 'normalization_fingerprint': fingerprint(state['normalization'])}
        if approve:
            record['review'] = {'status': 'approved', 'reviewer': 'isolated-contact-fixture-only', 'reviewed_at': '2026-10-03T00:00:00+00:00'}
    return seed


@pytest.fixture
def contact_db(tmp_path, contact_repo):
    db = tmp_path / 'contact-sources.db'
    build(db, contact_repo, upstream_commit='342084292ba4c3c6ec2bc34c045de4de72ae8d53')
    return db


@pytest.fixture
def approved_contact_db(contact_db):
    assert import_bundle(contact_db, fixture_contact_catalog(contact_db, approve=True), dry_run=False)['applied']
    return contact_db
