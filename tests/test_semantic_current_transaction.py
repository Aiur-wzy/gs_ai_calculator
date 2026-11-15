"""Independent current-review transaction and cache boundary tests."""
import copy
import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from genshin_db import semantic_store as store

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_independent_head_fixtures', ROOT / 'tests/test_semantic_store.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


@pytest.fixture
def tiny(tmp_path):
    path = tmp_path / 'head-transaction.db'
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    assert fixtures.install(path)['applied']
    return path


def ro(path): return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)


def current(conn, expected=None, kind='fact', ident='one'):
    result = store.read_current_reviewed_record(conn, 'release', kind, ident, expected)
    assert result['execution_authority'] is False and result['source_status'] == 'not_checked'
    return result


def revoke(path): fixtures.review(path, fixtures.read(path))


def test_begin_before_revoke_but_first_read_after_revoke_observes_revocation(tiny):
    with closing(ro(tiny)) as conn:
        conn.execute('BEGIN')
        revoke(tiny)
        result = current(conn)
        assert result['review_status'] == 'revoked' and result['record'] is None


def test_same_connection_new_transaction_discards_cached_same_revision_approval(tiny):
    with closing(ro(tiny)) as conn:
        conn.execute('BEGIN')
        old = current(conn)
        revoke(tiny)
        assert current(conn) == old
        conn.rollback()
        conn.execute('BEGIN')
        new = current(conn, 1)
        assert new['review_status'] == 'revoked' and new['record'] is None
        assert old['receipt']['revision'] == new['receipt']['revision'] == 1
        assert old['receipt']['payload_fingerprint'] == new['receipt']['payload_fingerprint']
        assert old['receipt']['review_fingerprint'] != new['receipt']['review_fingerprint']


def test_same_connection_observes_new_head_without_using_old_expected_revision(tiny):
    with closing(ro(tiny)) as conn:
        conn.execute('BEGIN')
        old = current(conn, 1)
        assert fixtures.install(tiny, fixtures.bundle([fixtures.fact(revision=2, status='pending')],
                                                      [fixtures.head(revision=2, expected=1)]))['applied']
        assert current(conn, 1) == old
        conn.rollback()
        conn.execute('BEGIN')
        stale = current(conn, 1)
        assert stale['reason'] == 'head_changed' and stale['current_revision'] == 2
        assert stale['record'] is None and stale['receipt'] is None
        latest = current(conn)
        assert latest['review_status'] == 'pending' and latest['record'] is None


def test_removed_and_readded_head_never_reuses_stale_qualification(tiny):
    with closing(ro(tiny)) as conn:
        conn.execute('BEGIN')
        approved = current(conn)
        conn.rollback()
        assert fixtures.install(tiny, fixtures.bundle([], [fixtures.head(revision=None, expected=1)]))['applied']
        conn.execute('BEGIN')
        absent = current(conn, 1)
        assert absent['reason'] == 'head_missing' and absent['record'] is None
        conn.rollback()
        fixtures.review(tiny, fixtures.read(tiny, revision=1))
        assert fixtures.install(tiny, fixtures.bundle([], [fixtures.head(revision=1, expected=None)]))['applied']
        conn.execute('BEGIN')
        revoked = current(conn, 1)
        assert revoked['review_status'] == 'revoked' and revoked['record'] is None
        assert revoked['receipt']['review_fingerprint'] != approved['receipt']['review_fingerprint']


def test_mutating_prior_results_cannot_poison_same_connection_reads(tiny):
    with closing(ro(tiny)) as conn:
        conn.execute('BEGIN')
        a = current(conn)
        original = copy.deepcopy(a)
        a['record']['payload']['claim']['requirement'] = 'malicious-replacement'
        a['record']['review']['status'] = 'approved-from-caller'
        a['receipt']['payload_fingerprint'] = '0' * 64
        assert current(conn) == original


def test_qualified_read_uses_only_selects_and_never_ends_caller_transaction(tiny):
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_PRAGMA}
    attempts = []
    with closing(ro(tiny)) as conn:
        conn.execute('BEGIN')
        def authorize(action, arg1, arg2, database, context):
            attempts.append((action, arg1, arg2))
            return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
        conn.set_authorizer(authorize)
        result = current(conn)
        assert result['status'] == 'ready' and conn.in_transaction and conn.total_changes == 0
        assert all(a[0] in allowed for a in attempts)
        conn.set_authorizer(None)


def test_descriptor_approval_remains_explicitly_nontransitive(tmp_path):
    path = tmp_path / 'descriptor-head.db'
    with closing(sqlite3.connect(path)): pass
    assert fixtures.install(path, fixtures.descriptor_bundle())['applied']
    revoke(path)
    with closing(ro(path)) as conn:
        conn.execute('BEGIN')
        descriptor = current(conn, kind='descriptor', ident='mechanism')
        revoked_fact = current(conn)
        assert descriptor['status'] == 'ready' and descriptor['scope'] == 'current_semantic_record_review'
        assert revoked_fact['review_status'] == 'revoked' and revoked_fact['record'] is None
        assert descriptor['source_status'] == 'not_checked' and not descriptor['execution_authority']


@pytest.mark.parametrize('expected', [None, 1])
def test_audit_access_to_old_approval_never_changes_current_review_admission(tiny, expected):
    assert fixtures.install(tiny, fixtures.bundle([fixtures.fact(revision=2, status='revoked')],
                                                 [fixtures.head(revision=2, expected=1)]))['applied']
    with closing(ro(tiny)) as conn:
        conn.execute('BEGIN')
        old = store.read_record(conn, 'release', 'fact', 'one', 1)
        assert old['review']['status'] == 'approved'
        now = current(conn, expected)
        assert now['record'] is None and now['status'] == 'pending'
        assert now['reason'] == ('head_changed' if expected else 'review_not_approved')
