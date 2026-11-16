"""Lifetime leases prevent a new App/Host opening during maintenance commit."""
from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from uuid import uuid4

from genshin_db.locking import LockBusyError, file_lock

from .installation_location import ACTIVE_STATES, checked_path, read_registration
from .product_install_contract import REGISTRATION_FILENAME, FirstInstallError

_active = set()
_active_guard = Lock()


@contextmanager
def installation_runtime_lease(root):
    root = checked_path(root)
    directory = checked_path(root / "maintenance/runtime-leases")
    path = directory / (uuid4().hex + ".lock")
    lease = file_lock(path, timeout=0)
    entered = False
    try:
        with file_lock(checked_path(root / "maintenance/installation.lock"), timeout=0):
            if read_registration(root).get("installation_state", "installed") not in ACTIVE_STATES:
                raise FirstInstallError("product_installation_not_active")
            directory.mkdir(parents=True, exist_ok=True)
            lease.__enter__()
            entered = True
            with _active_guard:
                _active.add(path)
        yield
    except LockBusyError as error:
        raise FirstInstallError("product_installation_maintenance_busy") from error
    finally:
        if entered:
            with _active_guard:
                _active.discard(path)
            lease.__exit__(None, None, None)
            # Unique filenames aren't re-used by another process. The OS lock
            # is the authority; a crash may leave an unlocked stale file.
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Maintenance may already be probing this now-unlocked file.
                # Leaving it is safe; the next idle check reclaims the lease.
                pass


def assert_installation_idle(root):
    root = checked_path(root)
    directory = checked_path(root / "maintenance/runtime-leases")
    if not directory.exists():
        return
    with _active_guard:
        if any(path.parent == directory for path in _active):
            raise FirstInstallError("product_installation_running")
    for path in directory.iterdir():
        path = checked_path(path)
        if not path.exists():
            continue
        if not path.is_file() or path.suffix != ".lock":
            raise FirstInstallError("product_installation_lease_invalid")
        try:
            with file_lock(path, timeout=0):
                pass
        except LockBusyError as error:
            raise FirstInstallError("product_installation_running") from error
        path.unlink(missing_ok=True)


@contextmanager
def installation_mutation_lock(root):
    root = checked_path(root)
    if not (root / REGISTRATION_FILENAME).is_file():
        yield
        return
    with file_lock(checked_path(root / "maintenance/installation.lock"), timeout=0):
        assert_installation_idle(root)
        yield
