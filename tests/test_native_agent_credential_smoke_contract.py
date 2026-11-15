"""Test-only credential safety survives retirement of obsolete GUI acceptance."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import ClassVar

import pytest

from tests import credential_smoke_support as acceptance

SENTINEL = "TEST_ONLY_SECRET_8d377d"


class MemoryCredentialStore:
    """Versioned store double with observable physical-record deletion."""

    records: ClassVar[dict[str, tuple[int, str | None]]] = {}
    targets: ClassVar[list[str]] = []
    physical_deletes: ClassVar[list[str]] = []

    def __init__(self, target: str):
        self.target = target
        self.targets.append(target)

    @classmethod
    def reset(cls) -> None:
        cls.records = {}
        cls.targets = []
        cls.physical_deletes = []

    def read_versioned(self) -> tuple[int, str | None]:
        return self.records.get(self.target, (0, None))

    def write(self, secret: str) -> None:
        generation, _old = self.read_versioned()
        self.records[self.target] = (generation + 1, secret)

    def delete(self) -> bool:
        generation, secret = self.read_versioned()
        if secret is None:
            return False
        self.records[self.target] = (generation + 1, None)
        return True


def _physical_delete(store: MemoryCredentialStore) -> None:
    MemoryCredentialStore.physical_deletes.append(store.target)
    MemoryCredentialStore.records.pop(store.target, None)


@pytest.fixture(autouse=True)
def _reset_memory_store():
    MemoryCredentialStore.reset()


def _run_smoke(tmp_path: Path, **overrides):
    profile = tmp_path / "profile"
    project = tmp_path / "project"
    profile.mkdir(exist_ok=True)
    project.mkdir(exist_ok=True)
    arguments = {
        "profile": profile,
        "project_root": project,
        "argv": ("preflight", "--native-node", "C:/controlled/node.exe"),
        "environment": {"SYSTEMROOT": "C:/Windows"},
        "captured_output": ("offline probe passed",),
        "store_factory": MemoryCredentialStore,
        "physical_delete": _physical_delete,
    }
    arguments.update(overrides)
    return acceptance.run_windows_credential_smoke(**arguments)


def test_each_smoke_uses_a_unique_testonly_target_and_physically_removes_it(
    tmp_path,
):
    first = _run_smoke(tmp_path)
    second = _run_smoke(tmp_path)

    assert first == second == {
        "status": "passed",
        "target_scope": "TestOnly/unique",
        "write": True,
        "replace": True,
        "read": True,
        "tombstone": True,
        "physical_cleanup": True,
        "token_scan": True,
    }
    assert len(MemoryCredentialStore.targets) == 2
    assert len(set(MemoryCredentialStore.targets)) == 2
    for target in MemoryCredentialStore.targets:
        assert target.startswith(acceptance.CREDENTIAL_SMOKE_TARGET_PREFIX)
        uuid.UUID(target.removeprefix(acceptance.CREDENTIAL_SMOKE_TARGET_PREFIX))
    assert MemoryCredentialStore.physical_deletes == MemoryCredentialStore.targets
    assert MemoryCredentialStore.records == {}


def test_physical_cleanup_runs_after_an_operational_failure_and_error_is_sanitized(
    tmp_path,
):
    class FailingStore(MemoryCredentialStore):
        def write(self, _secret: str) -> None:
            raise RuntimeError(SENTINEL)

    with pytest.raises(acceptance.CredentialSmokeError) as caught:
        _run_smoke(tmp_path, store_factory=FailingStore)

    assert caught.value.code == "development_acceptance_credential_smoke_failed"
    assert SENTINEL not in str(caught.value)
    assert SENTINEL not in repr(caught.value)
    assert len(MemoryCredentialStore.physical_deletes) == 1
    assert MemoryCredentialStore.records == {}


def test_backend_secret_is_absent_from_the_complete_public_exception_graph(tmp_path):
    class FailingStore(MemoryCredentialStore):
        def write(self, _secret: str) -> None:
            raise RuntimeError(SENTINEL)

    with pytest.raises(acceptance.CredentialSmokeError) as caught:
        _run_smoke(tmp_path, store_factory=FailingStore)

    error = caught.value
    assert error.__context__ is None
    assert error.__cause__ is None
    traceback = error.__traceback__
    while traceback is not None:
        frame_values = tuple(traceback.tb_frame.f_locals.values())
        assert all(SENTINEL not in repr(value) for value in frame_values)
        traceback = traceback.tb_next


@pytest.mark.parametrize(
    ("surface", "expected_code"),
    (
        ("argv", "development_acceptance_token_in_argv"),
        ("environment", "development_acceptance_token_in_environment"),
        ("captured", "development_acceptance_token_in_output"),
        ("profile", "development_acceptance_token_on_disk"),
        ("dump", "development_acceptance_token_on_disk"),
    ),
)
def test_leak_scan_covers_public_and_persistent_surfaces_without_echoing_token(
    tmp_path,
    surface,
    expected_code,
):
    project = tmp_path / "project"
    profile = tmp_path / "profile"
    project.mkdir()
    profile.mkdir()
    argv = ["preflight"]
    environment = {"SYSTEMROOT": "C:/Windows"}
    captured = ["offline"]
    if surface == "argv":
        argv.append(SENTINEL)
    elif surface == "environment":
        environment["UNRELATED"] = SENTINEL
    elif surface == "captured":
        captured.append(SENTINEL)
    elif surface == "profile":
        (profile / "session.jsonl").write_text(SENTINEL, encoding="utf-8")
    elif surface == "dump":
        (project / "crash.dmp").write_bytes(SENTINEL.encode())

    with pytest.raises(acceptance.CredentialSmokeError) as caught:
        acceptance.assert_tokens_absent(
            tokens=(SENTINEL,),
            argv=argv,
            environment=environment,
            captured_output=captured,
            profile=profile,
            project_root=project,
        )

    assert caught.value.code == expected_code
    assert SENTINEL not in str(caught.value)
    assert SENTINEL not in repr(caught.value)


@pytest.mark.parametrize("encoding", ("utf-16-le", "utf-16-be"))
def test_disk_scan_finds_native_wide_string_encodings_without_echo(tmp_path, encoding):
    project = tmp_path / "project"
    profile = tmp_path / "profile"
    project.mkdir()
    profile.mkdir()
    (profile / "native.log").write_bytes(SENTINEL.encode(encoding))

    with pytest.raises(acceptance.CredentialSmokeError) as caught:
        acceptance.assert_tokens_absent(
            tokens=(SENTINEL,),
            argv=("preflight",),
            environment={"SYSTEMROOT": "C:/Windows"},
            captured_output=("offline",),
            profile=profile,
            project_root=project,
        )

    assert caught.value.code == "development_acceptance_token_on_disk"
    assert SENTINEL not in str(caught.value)
