"""Role-aware ordinary output protection, shared by GUI, CLI and publishers.

This is not the database installer or a second publication transaction. Trusted
owners still use their own stores. An ordinary output can never become a DB or
profile write by supplying overwrite/CAS flags.
"""
from __future__ import annotations

import contextlib
import functools
import os
import re
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OutputPolicy:
    protected_files: tuple[Path, ...] = ()
    protected_roots: tuple[Path, ...] = ()
    input_paths: tuple[Path, ...] = ()
    owned_journal_files: tuple[Path, ...] = ()


_scope: ContextVar[OutputPolicy | None] = ContextVar("ordinary_output_policy", default=None)


def _key(path):
    # Case folding is also used for not-yet-created Windows destinations.
    return os.path.normcase(str(Path(path).resolve())).casefold()


def same_file(left, right):
    left, right = Path(left), Path(right)
    if _key(left) == _key(right):
        return True
    return left.exists() and right.exists() and left.samefile(right)


def _inside(path, root):
    target, parent = _key(path), _key(root)
    return target == parent or target.startswith(parent.rstrip("/\\") + os.sep)


def _root_collision(path, roots):
    if any(_inside(path, root) for root in roots):
        return True
    path = Path(path)
    if not path.is_file() or path.stat().st_nlink <= 1:
        return False
    identity = (path.stat().st_dev, path.stat().st_ino)
    # Only multiple-link targets require this exceptional identity scan. Normal
    # exports do not recursively enumerate profiles or the portable package.
    for root in roots:
        for directory, children, names in os.walk(root, followlinks=False):
            children[:] = [name for name in children if not (
                (Path(directory) / name).is_symlink() or
                (Path(directory) / name).is_junction())]
            for name in names:
                candidate = Path(directory) / name
                if candidate.is_file():
                    stat = candidate.stat()
                    if (stat.st_dev, stat.st_ino) == identity:
                        return True
    return False


def _owns_journal_path(path, owned):
    if not owned:
        return False
    current = Path(path).resolve()
    for target in owned:
        target = target.resolve()
        if current == target:
            return True
        # The existing CAS publisher creates only these reserved, random
        # transients beside a fixed owner file, never an arbitrary directory.
        if current.parent == target.parent and re.fullmatch(
            r"\." + re.escape(target.name) + r"(?:\.backup)?\.[a-z0-9_]{8}\.tmp", current.name
        ):
            return True
    return False


def database_files(database):
    """Release owners' filenames, not duplicated per output call site."""
    from genshin_app.database_bootstrap import (
        LIVE_MANIFEST_FILENAME,
        LIVE_PAIR_MANIFEST_FILENAME,
        MIGRATION_JOURNAL_FILENAME,
    )
    from genshin_db.release_profile import (
        LIVE_RECEIPT_FILENAME,
        MANIFEST_FILENAME,
        PAIR_MANIFEST_FILENAME,
    )
    database = Path(database).resolve()
    return (database, database.with_name(f".{database.name}.write.lock"),
            database.with_name(f".{database.name}.plan.lock"),
            *(database.with_name(database.name + suffix)
                        for suffix in ("-journal", "-wal", "-shm")),
            *(database.parent / name for name in (
                LIVE_MANIFEST_FILENAME, LIVE_PAIR_MANIFEST_FILENAME,
                LIVE_RECEIPT_FILENAME, MANIFEST_FILENAME,
                PAIR_MANIFEST_FILENAME, MIGRATION_JOURNAL_FILENAME,
                ".database-release.lock", ".release-pair.lock",
            )))


def managed_profile_roots(data_dir):
    from genshin_app.agent_chat.native_dsh_settings import default_dsh_profile_root
    directory = Path(data_dir).resolve()
    return (default_dsh_profile_root(directory), *(directory / name for name in (
        "agent_sessions", "native-dsh-accounting", "native-dsh-research", "native-dsh-workspaces",
        "native_agent_sessions_v2", "native-agent", "native-agent-workspaces",
    )))


@contextlib.contextmanager
def ordinary_output_scope(*, database_path=None, data_dir=None, input_paths=(),
                          protected_files=(), protected_roots=()):
    parent = _scope.get() or OutputPolicy()
    files = tuple(Path(p) for p in protected_files)
    roots = tuple(Path(p) for p in protected_roots)
    if database_path is not None:
        files += database_files(database_path)
    if data_dir is not None:
        files += database_files(Path(data_dir) / "genshin.db")
        roots += managed_profile_roots(data_dir)
        # Private delivery producers own this store; ordinary Save/Export may
        # read a package from it but may not overwrite that package or its index.
        roots += (Path(data_dir).resolve() / "agent_deliveries",)
    token = _scope.set(OutputPolicy(parent.protected_files + files,
                                   parent.protected_roots + roots,
                                   parent.input_paths + tuple(Path(p) for p in input_paths if p)))
    try:
        yield
    finally:
        _scope.reset(token)


def ordinary_gui_output(method):
    """Apply the already-known GuiPort DB/profile identity for an output call."""
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        with ordinary_output_scope(database_path=self._db_path, data_dir=self.data_dir):
            return method(self, *args, **kwargs)
    return wrapped


def validate_database_outputs(outputs, database_path, *, input_paths=()):
    """Preflight before expensive work in direct (non-GUI/CLI) services."""
    with ordinary_output_scope(database_path=database_path, data_dir=Path(database_path).parent):
        validate_output_paths(outputs, input_paths=input_paths)


def publish_ordinary_files(outputs, database_path, **kwargs):
    """A public file consumer, using the existing publisher and known DB roles."""
    from genshin_app.equipment.files import publish_files
    with ordinary_output_scope(database_path=database_path, data_dir=Path(database_path).parent):
        return publish_files(outputs, **kwargs)


def validate_output_paths(outputs, *, input_paths=(), cas_paths=(),
                          include_side_effects=True, check_inputs=True):
    """Check before preparation and again before each commit.

    CAS paths allow explicitly authored in-place document publication. They
    never exempt managed files. Backups and lock paths cannot overwrite inputs.
    """
    from genshin_app.equipment.schema import PlanError
    from genshin_app.paths import bundled_db_path, default_data_dir, is_frozen

    policy = _scope.get() or OutputPolicy()
    files = policy.protected_files + database_files(bundled_db_path()) + database_files(default_data_dir() / "genshin.db")
    roots = policy.protected_roots
    if is_frozen():
        # _MEIPASS is usually the _internal child, while the sealed manifest
        # protects the complete portable root, including both executables.
        roots += (Path(sys.executable).resolve().parent,)
    inputs = policy.input_paths + tuple(Path(p) for p in input_paths if p)
    for item in inputs:
        if item.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
            files += database_files(item)
    # Cache only inside this check. Re-resolving/stat-ing every known role for
    # every output made multi-file publication quadratic in filesystem calls.
    # Each prepare/commit check starts afresh so this is not a stale authority.
    descriptions = {}
    def describe(value):
        path = Path(value)
        raw_key = str(path.absolute())
        if raw_key not in descriptions:
            resolved = path.resolve()
            try:
                stat = resolved.stat()
                identity = (stat.st_dev, stat.st_ino) if stat.st_ino else None
            except (FileNotFoundError, NotADirectoryError):
                identity = None
            descriptions[raw_key] = (os.path.normcase(str(resolved)).casefold(), identity)
        return descriptions[raw_key]
    managed_roles = [describe(p) for p in files]
    managed_roles += [describe(p.with_name(p.name + ".previous")) for p in files]
    managed_keys = {key for key, _ in managed_roles}
    managed_inodes = {inode for _, inode in managed_roles if inode is not None}
    input_roles = [describe(p) for p in inputs]
    input_keys = {key for key, _ in input_roles}
    input_inodes = {inode for _, inode in input_roles if inode is not None}
    cas_roles = [describe(p) for p in cas_paths]
    cas_keys = {key for key, _ in cas_roles}
    cas_inodes = {inode for _, inode in cas_roles if inode is not None}
    for raw in outputs:
        path = Path(raw)
        touched = [path]
        if include_side_effects:
            touched += [path.with_name(path.name + ".previous"),
                        path.with_name(f".{path.name}.plan.lock")]
        for current in touched:
            key, inode = describe(current)
            managed_file = key in managed_keys or (inode is not None and inode in managed_inodes)
            owned_journal = _owns_journal_path(current, policy.owned_journal_files)
            managed = managed_file or (_root_collision(current, roots) and not owned_journal)
            collision = check_inputs and (key in input_keys or (inode is not None and inode in input_inodes))
            explicit_document = current == path and (key in cas_keys or (inode is not None and inode in cas_inodes))
            if managed or (collision and not explicit_document):
                raise PlanError("普通输出、备份或锁文件不能覆盖输入资料、数据库或受管资料", "file_conflict",
                                {"path": str(current.resolve()), "published_paths": [],
                                 "protected_role": "managed" if managed else "input"})


@contextlib.contextmanager
def _research_journal_output_scope(args):
    """Exactly one registered task journal and its existing CAS side effects."""
    from genshin_app.external_research import ledger_for_external_task
    inputs = tuple(Path(p) for p in (getattr(args, "plan", None), getattr(args, "input", None)) if p)
    task = getattr(args, "task_id", None)
    owned = ()
    if task is not None:
        store = ledger_for_external_task(args.data_dir, task)
        owned = (store.path, store.path.with_name(store.path.name + ".previous"),
                 store.path.with_name(f".{store.path.name}.plan.lock"))
    with ordinary_output_scope(data_dir=args.data_dir, input_paths=inputs, protected_files=inputs):
        policy = _scope.get()
        token = _scope.set(OutputPolicy(policy.protected_files, policy.protected_roots,
                                       policy.input_paths, owned))
        try:
            yield
        finally:
            _scope.reset(token)


def cli_output_scope(args):
    # Agent/GUI owners maintain their own private stores; their public file
    # consumers establish narrower output scopes. Do not wrap internal DSH or
    # candidate persistence in the permissions of an ordinary document export.
    handler = getattr(getattr(args, "_handler", None), "__name__", "")
    if handler == "_cmd_research":
        # An App-owned journal is not an ordinary document export. Identity
        # must be the registered callable, not a lookalike function name.
        from genshin_app.cli import _cmd_research
        if (getattr(args, "_handler", None) is _cmd_research
                and tuple(getattr(args, "_command_path", ())) in {
                    ("research", name) for name in ("schema", "append", "list", "bind", "report")
                }):
            return _research_journal_output_scope(args)
    if handler == "_cmd_agent_gui_workflow":
        # The runner consumes this file as text and owns separate private
        # stores. Protect only the immutable prompt, never its whole profile.
        prompt = getattr(args, "prompt_file", None)
        inputs = (Path(prompt),) if prompt else ()
        return ordinary_output_scope(input_paths=inputs, protected_files=inputs)
    if handler.startswith("_cmd_agent") or handler == "_cmd_gui":
        return contextlib.nullcontext()
    # Names are inputs in the public argparse contract. An existing audit is a
    # mutable document; its handler must still supply an exact CAS to publish.
    input_names = (
        "plan", "input", "template", "patch", "request_file", "audit",
        "result", "prompt_file", "proposal", "review", "source", "request", "calculated", "task_file",
    )
    # workflow init creates this document; bind edits it through exact SHA/CAS.
    # It is an immutable input only for export, not a forbidden output merely
    # because argparse names the shared option --audit in all three commands.
    if handler == "_cmd_workflow" and getattr(args, "_workflow", None) in {"init", "bind", "workspace"}:
        input_names = tuple(name for name in input_names if name != "audit")
    inplace_handler = handler in {"_cmd_enable_mechanics", "_cmd_assemble_attack", "_cmd_assemble_periodic", "_cmd_equipment_edit"} or (
        handler == "_cmd_equipment" and getattr(args, "_equipment", None) == "compose")
    plan, output = getattr(args, "plan", None), getattr(args, "output", None)
    if inplace_handler and plan and output and Path(plan).resolve() == Path(output).resolve():
        if not getattr(args, "overwrite", False) and handler != "_cmd_equipment_edit":
            from genshin_app.equipment.schema import PlanError
            raise PlanError("原地更新需 --overwrite；也可指定新的输出路径", "file_conflict")
        # Only this authored document role is mutable. A request/patch that
        # aliases it remains independently immutable, even with the same CAS.
        input_names = tuple(name for name in input_names if name != "plan")
    immutable = [Path(value) for name in input_names if (value := getattr(args, name, None))]
    # protected_files cannot be waived by CAS. input_paths additionally carry
    # DB companion roles; the ordinary publisher's editable-document CAS API
    # remains unchanged for callers that explicitly own such a document.
    return ordinary_output_scope(data_dir=args.data_dir, input_paths=immutable, protected_files=immutable)
