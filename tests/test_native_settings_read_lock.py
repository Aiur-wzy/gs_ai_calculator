"""Settings reads share atomic replacement locks, including on Windows."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from genshin_app.agent_chat import native_dsh_settings, native_turn_budget
from genshin_db.locking import lock_is_held


@pytest.mark.parametrize("kind", ["dsh", "budget"])
def test_existing_settings_read_uses_the_writer_lock(kind, tmp_path, monkeypatch):
    if kind == "dsh":
        module, reader = native_dsh_settings, "_read"
        load, update = module.load_native_dsh_settings, module.update_native_dsh_settings
        path = module.dsh_settings_path(tmp_path)
        lock = ".native-agent-dsh-settings.lock"
    else:
        module, reader = native_turn_budget, "_read_stored"
        load, update = module.load_native_turn_budget_policy, module.update_native_turn_budget_policy
        path = module.budget_policy_path(tmp_path)
        lock = ".native-agent-budget.lock"
    update(tmp_path, {}, expected_generation=0)
    original = getattr(module, reader)
    observations = []
    def checked_read(*args):
        with ThreadPoolExecutor(max_workers=1) as worker:
            observations.append(worker.submit(lock_is_held, path.with_name(lock)).result())
        return original(*args)
    monkeypatch.setattr(module, reader, checked_read)
    assert load(tmp_path)["generation"] == 1
    assert observations == [True]


def test_default_settings_reads_stay_zero_write(tmp_path):
    untouched = tmp_path / "unused"
    assert native_turn_budget.load_native_turn_budget_policy(untouched)["generation"] == 0
    assert native_dsh_settings.load_native_dsh_settings(untouched)["generation"] == 0
    assert not untouched.exists()
