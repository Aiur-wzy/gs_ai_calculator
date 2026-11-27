import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import genshin_runtime.action_capacity_model as model


def pytest_sessionstart(session):
    assert Path(model.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture
def isolated_cli_database_admission(monkeypatch):
    """Isolate command behavior from the production App/DB release gate.

    Synthetic SQLite fixtures intentionally model only the component under
    test; they are not accepted App database releases.  Tests opting into this
    fixture still exercise the real command handler and file boundaries while
    the dedicated startup-gate suite remains responsible for v3 manifest,
    receipt and required-component admission.
    """

    monkeypatch.setattr(
        "genshin_app.cli._require_runtime_database",
        lambda _data_dir: {"can_start": True, "fixture_isolation": True},
    )


@pytest.fixture(scope="session")
def accepted_source_data_dir():
    configured = os.environ.get("GENSHIN_TEST_ACCEPTED_DATA_DIR")
    if configured:
        return Path(configured).resolve(strict=True)
    return Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="session")
def accepted_source_database(accepted_source_data_dir):
    return accepted_source_data_dir / "genshin.db"


@pytest.fixture(scope="session")
def exact_upstream_source(accepted_source_data_dir):
    configured = os.environ.get("GENSHIN_TEST_UPSTREAM_ROOT")
    root = Path(configured) if configured else accepted_source_data_dir / "upstream/repo"

    def read(commit, relative_path, expected):
        source = root / relative_path
        actual = source.read_bytes() if source.is_file() else None
        if actual is not None and hashlib.sha256(actual).hexdigest() == expected:
            return actual
        if configured:
            pytest.fail(f"GENSHIN_TEST_UPSTREAM_ROOT must contain the exact {commit}:{relative_path} (SHA256 {expected})")
        if (root / ".git").exists():
            blob = subprocess.run(["git", "-C", str(root), "show", f"{commit}:{relative_path}"],
                                  capture_output=True, check=False, timeout=15)
            if blob.returncode == 0:
                assert hashlib.sha256(blob.stdout).hexdigest() == expected
                return blob.stdout
        pytest.skip(f"Exact source {commit}:{relative_path} is absent from the default cache. Set GENSHIN_TEST_UPSTREAM_ROOT.")

    return read


@pytest.fixture(scope="session")
def accepted_current_only_cli(tmp_path_factory, accepted_source_data_dir):
    """Real CLI/source profile independent of a pending packaged App pair.

    Source, the accepted DB and its original manifest/receipt are copied once.
    Release validation can supply an independently installed candidate profile.
    No build-py/pair is carried into this repository-mode profile.  Every
    request uses python -m and the complete ordinary startup gate.
    """
    workspace = Path(__file__).resolve().parents[1]
    root = tmp_path_factory.mktemp("accepted-current-only-cli-source")
    shutil.copytree(workspace / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    data = root / "data"
    data.mkdir()

    def sha(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    source_data = accepted_source_data_dir
    accepted_inputs = [source_data / name for name in (
        "genshin.db", "database-live-manifest.json", "database-live-receipt.json",
    )]
    original_shas = {path: sha(path) for path in accepted_inputs}
    for path in accepted_inputs:
        shutil.copyfile(path, data / path.name)
        assert sha(data / path.name) == original_shas[path]
    assert not (root / "build-py").exists()
    assert not (data / "release-pair-live-manifest.json").exists()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(root / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    imported = subprocess.run(
        [sys.executable, "-c", ("import json;from pathlib import Path;import genshin_app;from genshin_app.paths import resource_dir;"
         'print(json.dumps({"module":str(Path(genshin_app.__file__).resolve()),"resources":str(resource_dir().resolve())}))')],
        cwd=root, capture_output=True, encoding="utf-8", env=env, timeout=30, check=True)
    origins = json.loads(imported.stdout)
    assert Path(origins["module"]).is_relative_to(root / "src")
    assert Path(origins["resources"]) == root

    def run(*arguments, data=data):
        result = subprocess.run([sys.executable, "-m", "genshin_app", "--data-dir", str(data), *map(str, arguments)],
                                cwd=root, capture_output=True, encoding="utf-8",
                                env=env, timeout=60, check=False)
        payload = json.loads(result.stdout if result.returncode == 0 else result.stderr)
        assert payload["code"] == (0 if result.returncode == 0 else 1), result
        return result.returncode, payload

    yield run
    assert all(sha(path) == digest for path, digest in original_shas.items())
    assert sha(data / "genshin.db") == original_shas[source_data / "genshin.db"]
