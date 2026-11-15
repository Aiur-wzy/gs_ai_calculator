"""Fixed application-level isolation for the named packaged CLI selfcheck.

Not an OS sandbox or an Agent runtime. Generic Agent work remains in DSH.
The audit hook is installed only in the dedicated child process and cannot be
removed; normal product commands never install it.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

from .agent_chat.native_runtime_command import native_runtime_command

_WINDOWS_KEYS = frozenset({"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT"})


@dataclass
class _CandidateListenerBind:
    socket: socket.socket
    thread_id: int
    consumed: bool = False


class SelfcheckError(RuntimeError):
    def __init__(self, reason, details=None):
        self.reason, self.details = reason, details
        super().__init__(reason)


def inside(path, root):
    try:
        return Path(path).resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False


def clean_environment(root, source=None):
    values = os.environ if source is None else source
    clean = {key: values[key] for key in _WINDOWS_KEYS
             if key in values and isinstance(values[key], str)}
    root = Path(root).resolve()
    clean.update(TEMP=str(root / "temp"), TMP=str(root / "temp"),
                 APPDATA=str(root / "environment" / "roaming"),
                 LOCALAPPDATA=str(root / "environment" / "local"),
                 USERPROFILE=str(root / "environment" / "home"),
                 HOME=str(root / "environment" / "home"),
                 PYTHONUTF8="1", DSH_TELEMETRY_DISABLED="1", NO_COLOR="1")
    return clean


def production_selfcheck_command(*, root, data_dir, runtime_root, assets_root, preload):
    root, data_dir, runtime, assets, preload = map(Path, (root, data_dir, runtime_root, assets_root, preload))
    profile = data_dir / "native_agent_sessions_v2/dsh-production-v1"
    mirror = data_dir / "native_agent_sessions_v2/dsh-file-preview"
    ordinary = native_runtime_command(runtime / "node.exe", runtime / "src/native-dsh-runtime-entry.mjs",
                                      tuple(map(str, (profile, mirror, assets))))
    module = preload.as_uri() + "?" + urlencode({"context": (root / "production-provider-context.json").as_uri()})
    return ordinary, (ordinary[0], "--import", module, *ordinary[1:])


@dataclass
class SelfcheckContext:
    root: Path
    report: Path
    package_root: Path
    executable: Path
    node: Path
    entry: Path
    fresh: bool = True
    counters: dict = field(default_factory=lambda: {"normal": {}, "negative": {}})
    negative: bool = False
    _candidate_listener_scope: _CandidateListenerBind | None = field(default=None, init=False, repr=False)
    _candidate_listener_attempted: bool = field(default=False, init=False, repr=False)
    _candidate_listener_bind_calls: int = field(default=0, init=False, repr=False)
    _candidate_listeners: list = field(default_factory=list, init=False, repr=False)
    _candidate_listener_lock: object = field(default_factory=threading.RLock, init=False, repr=False)

    @classmethod
    def create(cls, root, report, *, package_root, executable, fresh=True):
        requested, requested_report = Path(root), Path(report)
        package_root = Path(package_root).resolve()
        if (not requested.is_absolute() or not requested_report.is_absolute()
                or requested.exists() and fresh or not requested.parent.is_dir()
                or requested.is_symlink() or requested.is_junction()):
            raise SelfcheckError("selfcheck_sandbox_must_be_absolute_fresh_directory")
        resolved = requested.resolve()
        if inside(resolved, package_root) or inside(package_root, resolved):
            raise SelfcheckError("selfcheck_sandbox_conflicts_with_package")
        resolved_report = requested_report.resolve()
        if not inside(resolved_report, resolved) or resolved_report == resolved or resolved_report.exists():
            raise SelfcheckError("selfcheck_report_must_be_new_file_inside_sandbox")
        return cls(resolved, resolved_report, package_root, Path(executable).resolve(),
                   package_root / "_internal" / "agent-runtime" / "node.exe",
                   package_root / "_internal" / "agent-runtime" / "src" / "native-dsh-selfcheck-entry.mjs", fresh=fresh)

    def deny(self, kind):
        group = self.counters["negative" if self.negative else "normal"]
        group[kind] = group.get(kind, 0) + 1
        raise SelfcheckError("selfcheck_" + kind + "_denied")

    @contextlib.contextmanager
    def negative_probe(self):
        previous, self.negative = self.negative, True
        try:
            yield
        finally:
            self.negative = previous

    @contextlib.contextmanager
    def _candidate_listener_bind(self, server):
        """One trusted QA construction, never a network flag or JSON grant."""
        from .agent_chat.native_cli_link import _LoopbackCandidateHTTPServer

        address = server.server_address if type(server) is _LoopbackCandidateHTTPServer else None
        candidate = getattr(server, "socket", None)
        if (type(server) is not _LoopbackCandidateHTTPServer
                or type(candidate) is not socket.socket
                or candidate.family != socket.AF_INET or candidate.type != socket.SOCK_STREAM
                or type(address) is not tuple or len(address) != 2
                or type(address[0]) is not str or address[0] != "127.0.0.1"
                or type(address[1]) is not int or address[1] != 0):
            self.deny("network")
        with self._candidate_listener_lock:
            if self._candidate_listener_scope is not None or self._candidate_listener_attempted:
                self.deny("network")
            permit = _CandidateListenerBind(candidate, threading.get_ident())
            self._candidate_listener_scope = permit
            self._candidate_listener_attempted = True
        try:
            yield
            endpoint = candidate.getsockname()
            if (not permit.consumed or type(endpoint) is not tuple or len(endpoint) != 2
                    or endpoint[0] != "127.0.0.1" or type(endpoint[1]) is not int
                    or not 1 <= endpoint[1] <= 65535 or server.server_address != endpoint):
                raise SelfcheckError("selfcheck_candidate_listener_binding_invalid")
            with self._candidate_listener_lock:
                self._candidate_listeners.append((candidate, endpoint))
        finally:
            with self._candidate_listener_lock:
                if self._candidate_listener_scope is permit:
                    self._candidate_listener_scope = None

    def _consume_candidate_listener_bind(self, arguments):
        with self._candidate_listener_lock:
            permit = self._candidate_listener_scope
            if (type(permit) is not _CandidateListenerBind or permit.consumed
                    or threading.get_ident() != permit.thread_id
                    or type(arguments) is not tuple or len(arguments) != 2
                    or arguments[0] is not permit.socket):
                return False
            address = arguments[1]
            if (type(address) is not tuple or len(address) != 2
                    or type(address[0]) is not str or address[0] != "127.0.0.1"
                    or type(address[1]) is not int or address[1] != 0):
                return False
            permit.consumed = True
            self._candidate_listener_bind_calls += 1
            return True

    def local_candidate_ipc_evidence(self):
        """Observation only; these JSON fields cannot create a bind permit."""
        with self._candidate_listener_lock:
            return {"contract": "genshin-selfcheck-local-candidate-ipc-v1",
                    "authorization_windows": int(self._candidate_listener_attempted),
                    "bind_calls": self._candidate_listener_bind_calls,
                    "active_authorizations": int(self._candidate_listener_scope is not None),
                    "listeners": [{"host": endpoint[0], "port": endpoint[1],
                                   "family": "AF_INET", "socket_type": "SOCK_STREAM",
                                   "closed": candidate.fileno() == -1}
                                  for candidate, endpoint in self._candidate_listeners],
                    "external_network_allowed": False, "client_connect_allowed": False}

    def check_path(self, path, *, write=False):
        if isinstance(path, int):
            return  # descriptor was created through an already audited open
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        if not isinstance(path, (str, os.PathLike)):
            return
        allowed = inside(path, self.root)
        if not write:
            allowed = allowed or inside(path, self.package_root)
            windows = os.environ.get("SYSTEMROOT")
            allowed = allowed or bool(windows and inside(path, windows))
        if not allowed:
            self.deny("outside_write" if write else "outside_read")

    def audit(self, event, arguments):
        if event == "open":
            path, mode, flags = arguments
            # CPython 3.12 on Windows opens DEVNULL as O_RDWR | O_NOINHERIT.
            # This exact standard device has no stored filesystem contents.
            # Never extend the exception to aliases, other devices or flags.
            if (os.name == "nt" and os.devnull == "nul"
                    and type(path) is str and path == os.devnull
                    and mode is None and type(flags) is int
                    and flags == os.O_RDWR | os.O_NOINHERIT):
                return
            write = isinstance(mode, str) and any(flag in mode for flag in "wax+")
            write = write or isinstance(flags, int) and bool(flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            self.check_path(path, write=write)
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.utime", "os.truncate"}:
            self.check_path(arguments[0], write=True)
        elif event in {"os.rename", "os.link", "os.symlink"}:
            self.check_path(arguments[0], write=event == "os.rename")
            self.check_path(arguments[1], write=True)
        elif event in {"os.listdir", "os.scandir", "os.chdir"}:
            self.check_path(arguments[0])
        elif event == "socket.bind":
            if self._consume_candidate_listener_bind(arguments):
                return
            self.deny("network")
        elif event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo",
                       "socket.gethostbyname", "socket.gethostbyaddr"}:
            self.deny("network")
        elif event == "subprocess.Popen":
            executable, argv, cwd, _environment = arguments
            self.check_path(cwd or os.getcwd())
            # Windows audits the already-serialized command line and may
            # report executable=None.  Compare the two complete fixed command
            # vectors using CPython's own serializer, without guessing a
            # binary from a prefix or implementing another Windows parser.
            matches = []
            for index, command in enumerate(self._fixed_process_commands()):
                exact_argv = (type(argv) in {list, tuple} and tuple(argv) == command)
                if os.name == "nt" and type(argv) is str:
                    exact_argv = argv == subprocess.list2cmdline(command)
                exact_executable = (executable is None or type(executable) is str
                                    and executable == command[0])
                expected_cwd = self.node.parent if index == 0 else self.root
                if exact_argv and exact_executable and Path(cwd or os.getcwd()).resolve() == expected_cwd:
                    matches.append(command)
            if len(matches) != 1:
                self.deny("process")
        elif event in {"os.system", "os.startfile", "winreg.OpenKey"}:
            self.deny("external_operation")
        elif event == "ctypes.dlsym" and any(token in str(arguments[-1]).lower() for token in ("credread", "credwrite", "credenumerate", "creddelete")):
            self.deny("credential")
        elif event == "sqlite3.connect":
            name = str(arguments[0])
            if name == ":memory:":
                return
            if name.startswith("file:"):
                parsed = urlsplit(name)
                path = unquote(parsed.path)
                if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
                    path = path[1:]
                self.check_path(path, write="mode=ro" not in parsed.query and "immutable=1" not in parsed.query)
            else:
                self.check_path(name, write=True)

    def _fixed_process_commands(self):
        """Only the named selfcheck child and its exact fresh-readback call."""
        _, production = production_selfcheck_command(root=self.root, data_dir=self.root / "data",
            runtime_root=self.node.parent, assets_root=self.package_root / "_internal/agent-client-assets",
            preload=self.entry)
        return (
            production,
            (str(self.executable), "internal", "verify-frozen-readback",
             "--sandbox-root", str(self.root), "--report", str(self.root / "fresh-readback.json")),
        )

    def prepare(self):
        if self.root.is_symlink() or self.root.is_junction() or self.root.resolve() != self.root:
            raise SelfcheckError("selfcheck_sandbox_identity_changed")
        self.root.mkdir(exist_ok=not self.fresh)
        for relative in ("temp", "environment/local", "environment/roaming", "environment/home"):
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        safe_ambient = _ambient_windows_environment()
        os.environ.clear()
        os.environ.update(clean_environment(self.root, safe_ambient))
        os.chdir(self.root)
        sys.dont_write_bytecode = True
        sys.addaudithook(self.audit)

    def assert_normal_clean(self):
        if any(self.counters["normal"].values()):
            raise SelfcheckError("selfcheck_normal_task_attempted_forbidden_operation", self.counters["normal"])

    def write_report(self, value):
        self.report.parent.mkdir(parents=True, exist_ok=True)
        with self.report.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")


def _ambient_windows_environment():
    # Values are captured before clearing the environment by the entry module.
    return {key: os.environ[key] for key in _WINDOWS_KEYS if key in os.environ}


class DeniedCredentials:
    """Explicit injection: never construct a real Windows credential backend."""
    def __init__(self, context):
        self.context = context

    def __getattr__(self, _name):
        self.context.deny("credential")
