"""RF08 v2 host-side tools: public product interfaces and explicit run-local paths.

Derived from the sealed AYAKA-DUAL-01 proxy; this new copy leaves it unchanged.
No model launch or game answers are provided by this engineering facility.

This is benchmark infrastructure, not an OS sandbox. The model must have no other
filesystem/network/process tools. Hosts must serialize other writers (including the
spreadsheet adapter) with this instance, and must not grant OS access to its roots.

Host construction:
    ControlledTools(exe, data_dir, public_root, output_root, http_base,
                    token=None, public_files=None, audit_path=None)
public_files is an explicit sequence of POSIX paths relative to public_root. When
omitted, only public_root/skill/** is registered, including its complete Skill.
Model paths: public/<registered-name>, outputs/<own-file>, cache/<response-file>.
cache maps to output_root/cache. No raw host paths are accepted by model methods.

Model methods return JSON dictionaries and never raise a product error as success:
    list_files(path='outputs', recursive=False)
    read_text(path, offset=0, limit=16000)
    write_text(path, text, overwrite=False, expected_sha=None)
    app_cli(argv, cache_path=None)  # e.g. ['equipment','validate','--plan','outputs/p.json']
    db_get(path, params=None, cache_path=None)
CLI/HTTP responses retain original stdout/stderr or body, plus parsed envelope.
Only physical paths from the fixed private roots are reversibly mapped in the
model-facing copy; the private JSONL audit preserves exact original responses.
resolve_output_path is HOST ONLY and must never be registered as a model tool.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import stat
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit


class ProxyError(ValueError):
    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
             *(f"LPT{i}" for i in range(10))}
_PAGE = {"version_line", "page", "page_size"}
_CAT = _PAGE | {"catalog_scope", "name", "rarity"}
_HTTP_ROUTES = [
    (r"/v1/meta/(?:version|capabilities)", set(), set()),
    (r"/v1/characters", _PAGE | {"name", "element", "rarity"}, set()),
    (r"/v1/characters/[0-9]+", {"version_line", "sections", "levels"}, set()),
    (r"/v1/skills/[0-9]+", {"version_line", "character_id", "sections", "levels"}, set()),
    (r"/v1/skill-params/[0-9]+", {"version_line", "level"}, set()),
    (r"/v1/search", {"version_line", "q"}, {"q"}),
    (r"/v1/annotations", _PAGE | {"target_type", "target_id", "category", "status"}, set()),
    (r"/v1/weapons", _CAT | {"weapon_type"}, set()),
    (r"/v1/weapons/[0-9]+", {"version_line", "sections", "refinement"}, set()),
    (r"/v1/weapons/[0-9]+/stats", {"version_line", "level", "ascension"}, {"level", "ascension"}),
    (r"/v1/artifact-sets", _CAT, set()),
    (r"/v1/artifact-sets/[0-9]+", {"version_line", "sections", "set_thresholds"}, set()),
    (r"/v1/artifacts", _CAT | {"set_id", "equip_type"}, set()),
    (r"/v1/artifacts/[0-9]+", {"version_line"}, set()),
    (r"/v1/artifacts/[0-9]+/main-stats", {"version_line", "level", "main_prop_id"}, {"level", "main_prop_id"}),
    (r"/v1/artifact-(?:main-props|substats)", _PAGE | {"depot_id", "prop_type"}, {"depot_id"}),
    (r"/v1/equip-affixes/[0-9]+", {"version_line"}, set()),
]
_CLI = {
    ("capabilities",): {},
    ("db", "status"): {},
    ("db", "context"): {"--input": "input", "--output": "output", "--cache-dir": "directory", "--refresh": "flag", "--overwrite": "flag"},
    ("engine", "calc"): {"--patch": "input", "--output": "output"},
    ("engine", "check-rotation"): {"--format": "format", "--output": "output", "--overwrite": "flag"},
    ("equipment", "validate"): {"--plan": "input"},
    ("equipment", "compose"): {"--plan": "input", "--output": "output", "--export-resolved": "output", "--overwrite": "flag"},
    ("equipment", "calc"): {"--plan": "input", "--output": "output", "--overwrite": "flag"},
    ("equipment", "export"): {"--plan": "input", "--output": "output", "--format": "format", "--overwrite": "flag"},
    ("equipment", "workbook"): {"--plan": "input", "--output": "output", "--overwrite": "flag"},
    ("equipment", "enable-mechanics"): {"--plan": "input", "--output": "output", "--overwrite": "flag"},
    ("workflow", "init"): {"--plan": "input", "--audit": "output", "--overwrite": "flag"},
    ("workflow", "bind"): {"--plan": "input", "--audit": "inplace"},
    ("workflow", "export"): {"--plan": "input", "--audit": "input", "--output-dir": "directory", "--overwrite": "flag"},
}
_WORKFLOW_OUTPUTS = ("issues.json", "lookups.jsonl", "web_findings.json", "rotation.md")


def cost_category(operation, request):
    """Mechanical accounting only; responsibility/adoption remain reviewer judgments."""
    if operation == "read_text":
        path = str(request.get("path", ""))
        if path.startswith("outputs/cache/"):
            return "read_cache"
        return "read_" + path.split("/", 1)[0]
    if operation == "app_cli":
        argv = request.get("argv", [])
        if argv[:1] == ["--pretty"]:
            argv = argv[1:]
        command = tuple(argv[:2])
        return {("db", "context"): "local_context_query", ("equipment", "compose"): "plan_compose",
                ("equipment", "calc"): "calculation", ("engine", "calc"): "calculation",
                ("engine", "check-rotation"): "rotation_check", ("equipment", "workbook"): "formula_workbook",
                ("equipment", "export"): "result_export"}.get(command, "workflow" if argv[:1] == ["workflow"] else "product_other")
    return {"db_get": "local_database_query", "write_text": "artifact_write", "list_files": "file_discovery"}.get(operation, "other")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _no_links(path: Path) -> None:
    """Check every existing ancestor without first resolving away a link."""
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ProxyError("proxy_path_denied", "Links and reparse points are not permitted")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ProxyError("proxy_path_denied", "Hard-linked files are not permitted")


def _parts(value: str, *, allow_root: bool = False) -> list[str]:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ProxyError("proxy_path_denied", "Expected a nonempty logical POSIX path")
    if value.startswith(("/", "\\")) or any(c in value for c in '\\:*?"<>|'):
        raise ProxyError("proxy_path_denied", "Host, Windows, URL and device paths are forbidden")
    parts = value.split("/")
    if len(parts) > 64 or any(not p or p in (".", "..") or p.endswith((".", " ")) or
                              any(ord(c) < 32 or ord(c) == 127 for c in p) or
                              p.split(".")[0].upper() in _RESERVED for p in parts):
        raise ProxyError("proxy_path_denied", "Invalid or ambiguous logical path")
    if not allow_root and len(parts) < 2:
        raise ProxyError("proxy_path_denied", "A file below the logical root is required")
    return parts


def _json(text: str):
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def _run_process(argv: list[str], cwd: Path, timeout: float, limit: int) -> dict:
    """Bound capture on disk while polling; shell is never involved."""
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        env = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT"):
            env.pop(key, None)
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stdout,
                                stderr=stderr, shell=False, env=env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + timeout
        reason = None
        try:
            while proc.poll() is None:
                if time.monotonic() > deadline:
                    reason = "proxy_timeout"
                    break
                if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > limit:
                    reason = "proxy_output_limit"
                    break
                time.sleep(0.025)
            if reason:
                proc.kill()
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        size = os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size
        if size > limit:
            reason = reason or "proxy_output_limit"
        stdout.seek(0)
        stderr.seek(0)
        return {"exit_code": proc.returncode,
                "stdout": stdout.read(limit).decode("utf-8", errors="replace"),
                "stderr": stderr.read(limit).decode("utf-8", errors="replace"),
                "capture_truncated": size > limit, "proxy_failure": reason}


class ControlledTools:
    def __init__(self, exe, data_dir, public_root, output_root, http_base, token=None, *,
                 public_files=None, audit_path=None, max_file_bytes=32 * 1024 * 1024,
                 timeout=60.0, max_response_bytes=16 * 1024 * 1024):
        self.exe = Path(exe).absolute()
        self.data_dir = Path(data_dir).absolute()
        self.public_root = Path(public_root).absolute()
        self.output_root = Path(output_root).absolute()
        for path in (self.exe, self.data_dir, self.public_root, self.output_root):
            _no_links(path)
        if not self.exe.is_file() or not self.data_dir.is_dir() or not self.public_root.is_dir():
            raise ValueError("Host must supply an existing EXE, backend data directory and public root")
        for other in (self.data_dir, self.public_root, self.exe):
            if self.output_root.is_relative_to(other) or other.is_relative_to(self.output_root):
                raise ValueError("Run output and frozen/private resources must not overlap")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.audit_path = Path(audit_path).absolute() if audit_path else self.output_root.parent / f".{self.output_root.name}.controlled-tools.jsonl"
        _no_links(self.audit_path)
        if self.audit_path.is_relative_to(self.output_root) or self.audit_path.is_relative_to(self.public_root):
            raise ValueError("Audit must remain outside model-visible roots")
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_file_bytes = int(max_file_bytes)
        self.max_response_bytes = int(max_response_bytes)
        self.timeout = float(timeout)
        if self.max_file_bytes <= 0 or self.max_response_bytes <= 0 or not 0 < self.timeout <= 300:
            raise ValueError("Invalid host resource limits")
        base = urlsplit(http_base)
        if base.scheme != "http" or base.hostname != "127.0.0.1" or not base.port or base.username or base.password or base.path not in ("", "/") or base.query or base.fragment:
            raise ValueError("Backend must be a fixed http://127.0.0.1:<port> origin")
        self._http_host, self._http_port = base.hostname, base.port
        # Reads are unauthenticated: deliberately do not keep or forward token.
        self._lock = threading.RLock()
        self._counter = 0
        if public_files is None:
            skill = self.public_root / "skill"
            if not (skill / "SKILL.md").is_file():
                raise ValueError("Provide public_files or public_root/skill/SKILL.md")
            public_files = [p.relative_to(self.public_root).as_posix() for p in self._walk(skill)]
        self._public = {}
        for name in public_files:
            parts = _parts("public/" + name)
            path = self.public_root.joinpath(*parts[1:])
            _no_links(path)
            if not path.is_file() or path.stat().st_size > self.max_file_bytes:
                raise ValueError("Invalid registered public file")
            data = path.read_bytes()
            data.decode("utf-8-sig")
            self._public["public/" + name] = (path, _sha(data))

    def _walk(self, root: Path):
        _no_links(root)
        for path in sorted(root.iterdir()):
            _no_links(path)
            if path.is_dir():
                yield from self._walk(path)
            elif path.is_file():
                yield path

    def _resolve(self, name, *, write=False, must_exist=False, allow_root=False):
        parts = _parts(name, allow_root=allow_root)
        if parts[0] == "public":
            if write or name not in self._public:
                raise ProxyError("proxy_path_denied", "Public files must be registered and are read-only")
            path, expected = self._public[name]
            _no_links(path)
            if path.stat().st_size > self.max_file_bytes:
                raise ProxyError("proxy_file_limit", "Frozen file exceeds the size limit")
            if _sha(path.read_bytes()) != expected:
                raise ProxyError("proxy_fixture_changed", "A frozen public file changed")
            return path
        if parts[0] not in ("outputs", "cache"):
            raise ProxyError("proxy_path_denied", "Only public, outputs and cache paths are allowed")
        path = self.output_root.joinpath(*((["cache"] if parts[0] == "cache" else []) + parts[1:]))
        _no_links(path)
        if not path.resolve().is_relative_to(self.output_root.resolve()):
            raise ProxyError("proxy_path_denied", "Output escaped its run")
        if must_exist and not path.is_file():
            raise ProxyError("proxy_not_found", "Requested run file does not exist")
        if path.is_file() and path.stat().st_size > self.max_file_bytes:
            raise ProxyError("proxy_file_limit", "Run file exceeds the frozen size limit")
        return path

    def resolve_output_path(self, path, must_exist=False):
        """HOST ONLY: reuse the same path boundary for the spreadsheet adapter."""
        return self._resolve(path, write=True, must_exist=must_exist)

    def _visible(self, value):
        if isinstance(value, dict):
            return {self._visible(k): self._visible(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._visible(v) for v in value]
        if not isinstance(value, str):
            return value
        text = value
        for physical, logical in ((self.output_root, "outputs"), (self.public_root, "public"),
                                  (self.data_dir, "backend://data"), (self.exe.parent, "backend://app")):
            for spelling in sorted({str(physical), str(physical).replace("\\", "/"),
                                    str(physical).replace("\\", "\\\\")}, key=len, reverse=True):
                text = re.sub(re.escape(spelling), lambda match, replacement=logical: replacement, text, flags=re.IGNORECASE if os.name == "nt" else 0)
        return text

    def _call(self, operation, request, function):
        with self._lock:
            self._counter += 1
            started = time.monotonic()
            event = {"call_id": self._counter, "operation": operation, "request": request,
                     "started_at": datetime.now(UTC).isoformat()}
            try:
                result = function(event)
            except ProxyError as exc:
                result = {"ok": False, "proxy_error": {"reason": exc.reason, "message": str(exc)}}
            except TimeoutError as exc:
                result = {"ok": False, "proxy_error": {"reason": "proxy_timeout", "message": str(exc)}}
            except (OSError, UnicodeError, ValueError, TypeError, http.client.HTTPException) as exc:
                result = {"ok": False, "proxy_error": {"reason": "proxy_environment_error", "message": str(exc)}}
            duration = time.monotonic() - started
            event.update(duration_seconds=duration, result=result,
                         accounting={"category": cost_category(operation, request), "tool_wall_seconds": duration,
                                     "tokens": None, "usd": None, "budget_charge": None,
                                     "note": "Token/price/operation budget comes from the independent host run ledger; missing is not zero."})
            _no_links(self.audit_path)
            with self.audit_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            return self._visible(result)

    def list_files(self, path="outputs", recursive=False):
        def perform(event):
            parts = _parts(path, allow_root=True)
            if not isinstance(recursive, bool):
                raise ProxyError("proxy_invalid_parameter", "recursive must be boolean")
            if parts[0] == "public":
                files = [n for n in self._public if n == path or n.startswith(path + "/")]
            else:
                root = self._resolve(path, allow_root=True)
                if not root.is_dir():
                    raise ProxyError("proxy_not_found", "Directory not found")
                entries = list(self._walk(root)) if recursive else list(root.iterdir())
                files = []
                for item in entries:
                    _no_links(item)
                    files.append(path + "/" + item.relative_to(root).as_posix())
            return {"ok": True, "paths": sorted(files)[:1000], "total": len(files), "truncated": len(files) > 1000}
        return self._call("list_files", {"path": path, "recursive": recursive}, perform)

    def read_text(self, path, offset=0, limit=16000):
        def perform(event):
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 128000:
                raise ProxyError("proxy_invalid_parameter", "Invalid character offset or limit (1..128000)")
            p = self._resolve(path, must_exist=True)
            raw = p.read_bytes()
            if len(raw) > self.max_file_bytes:
                raise ProxyError("proxy_file_limit", "File exceeds the frozen size limit")
            text = raw.decode("utf-8-sig")
            end = min(len(text), offset + limit)
            return {"ok": True, "path": path, "sha256": _sha(raw), "text": text[offset:end],
                    "offset": offset, "next_offset": end, "total_chars": len(text), "truncated": end < len(text)}
        return self._call("read_text", {"path": path, "offset": offset, "limit": limit}, perform)

    def _write(self, path, content, overwrite=False, expected_sha=None):
        if not isinstance(overwrite, bool) or expected_sha is not None and (not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)):
            raise ProxyError("proxy_invalid_parameter", "Invalid overwrite or expected SHA")
        if len(content) > self.max_file_bytes:
            raise ProxyError("proxy_file_limit", "File exceeds the frozen size limit")
        p = self._resolve(path, write=True)
        old = _sha(p.read_bytes()) if p.is_file() else None
        if expected_sha is not None and old != expected_sha:
            raise ProxyError("proxy_file_conflict", "Current file SHA differs from expected_sha")
        if p.exists() and not overwrite:
            raise ProxyError("proxy_file_conflict", "Writing an existing file requires overwrite=true")
        p.parent.mkdir(parents=True, exist_ok=True)
        _no_links(p)
        fd, tmp = tempfile.mkstemp(prefix=".proxy-", suffix=".tmp", dir=p.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, p)
        finally:
            Path(tmp).unlink(missing_ok=True)
        return {"ok": True, "path": path, "previous_sha256": old, "sha256": _sha(content), "bytes": len(content)}

    def write_text(self, path, text, overwrite=False, expected_sha=None):
        def perform(event):
            if not isinstance(text, str):
                raise ProxyError("proxy_invalid_parameter", "text must be a string")
            return self._write(path, text.encode("utf-8"), overwrite, expected_sha)
        return self._call("write_text", {"path": path, "text": text, "overwrite": overwrite, "expected_sha": expected_sha}, perform)

    def _cache(self, cache_path, text):
        if cache_path is None:
            return None
        if not isinstance(cache_path, str) or not cache_path.startswith("cache/"):
            raise ProxyError("proxy_path_denied", "Response cache must use cache/<name>")
        return self._write(cache_path, text.encode("utf-8"))

    def _cache_preflight(self, cache_path):
        if cache_path is not None:
            if not isinstance(cache_path, str) or not cache_path.startswith("cache/"):
                raise ProxyError("proxy_path_denied", "Response cache must use cache/<name>")
            if self._resolve(cache_path, write=True).exists():
                raise ProxyError("proxy_file_conflict", "Cache target exists; choose a new name")

    def _argv(self, values):
        if not isinstance(values, list) or not values or len(values) > 40 or any(not isinstance(v, str) for v in values):
            raise ProxyError("proxy_invalid_parameter", "argv must be a short string array")
        args = values[:]
        pretty = args[:1] == ["--pretty"]
        if pretty:
            args.pop(0)
        command = (args[0],) if args and args[0] == "capabilities" else tuple(args[:2])
        if command not in _CLI:
            raise ProxyError("proxy_denied", "Command is not allowed by the frozen benchmark policy")
        remaining = args[len(command):]
        actual = [str(self.exe), "--data-dir", str(self.data_dir)] + (["--pretty"] if pretty else []) + list(command)
        if remaining in (["--help"], ["-h"]):
            return actual + remaining
        options = _CLI[command]
        seen, inputs, outputs, directories, by_flag = set(), [], [], [], {}
        if command in (("engine", "calc"), ("engine", "check-rotation")):
            if not remaining:
                raise ProxyError("proxy_invalid_parameter", "engine calc requires a run template")
            template = self._resolve(remaining.pop(0), write=True, must_exist=True)
            actual.append(str(template))
            inputs.append(template)
        while remaining:
            flag = remaining.pop(0)
            if flag not in options or flag in seen:
                raise ProxyError("proxy_invalid_parameter", "Unknown, duplicate or abbreviated CLI option")
            seen.add(flag)
            kind = options[flag]
            actual.append(flag)
            if kind == "flag":
                continue
            if not remaining:
                raise ProxyError("proxy_invalid_parameter", "CLI option requires a value")
            value = remaining.pop(0)
            if kind == "format":
                allowed_formats = ("json", "xlsx") if command == ("engine", "check-rotation") else ("json", "csv", "xlsx")
                if value not in allowed_formats:
                    raise ProxyError("proxy_invalid_parameter", "format must be json/csv/xlsx")
                actual.append(value)
            else:
                path = self._resolve(value, write=True, must_exist=kind in ("input", "inplace"))
                by_flag[flag] = path
                actual.append(str(path))
                if kind == "directory":
                    if path.exists() and not path.is_dir():
                        raise ProxyError("proxy_invalid_parameter", "Expected an output directory")
                    _no_links(path)
                    # Directory-output commands choose filenames themselves. Check
                    # every existing descendant, including backups and locks,
                    # before letting the product inspect or repair its cache.
                    if path.exists():
                        list(self._walk(path))
                    directories.append(path)
                else:
                    if kind in ("input", "inplace"):
                        inputs.append(path)
                    if kind in ("output", "inplace"):
                        outputs.append(path)
        if command[0] == "equipment" and "--plan" not in seen:
            raise ProxyError("proxy_invalid_parameter", "equipment requires --plan")
        if command == ("equipment", "export") and not {"--format", "--output"} <= seen:
            raise ProxyError("proxy_invalid_parameter", "export requires --format and --output")
        required = {("db", "context"): {"--input"}, ("equipment", "workbook"): {"--plan", "--output"},
                    ("equipment", "enable-mechanics"): {"--plan", "--output"},
                    ("workflow", "init"): {"--plan", "--audit"}, ("workflow", "bind"): {"--plan", "--audit"},
                    ("workflow", "export"): {"--plan", "--audit", "--output-dir"}}.get(command, set())
        if not required <= seen:
            raise ProxyError("proxy_invalid_parameter", "Missing required command options: " + ", ".join(sorted(required-seen)))
        if command == ("workflow", "export"):
            outputs.extend(by_flag["--output-dir"] / name for name in _WORKFLOW_OUTPUTS)
        for p in outputs:
            p.parent.mkdir(parents=True, exist_ok=True)
            for side in (p, p.with_name(p.name + ".previous"), p.with_name("." + p.name + ".plan.lock")):
                _no_links(side)
                if side.exists() and not side.is_file():
                    raise ProxyError("proxy_path_denied", "Publication target, backup and lock must be regular files")
                # In-place publication is an explicit product command. Its backup
                # and lock may never overwrite another input passed to this call.
                if side != p and side in inputs:
                    raise ProxyError("proxy_file_conflict", "Publication sidecar collides with an input")
        # Preserve product overwrite behavior, including engine calc's direct overwrite.
        return actual

    def _snapshot_outputs(self):
        return {p.relative_to(self.output_root).as_posix(): {"bytes": p.stat().st_size,
                "sha256": _sha(p.read_bytes()) if p.stat().st_size <= self.max_file_bytes else None}
                for p in self._walk(self.output_root)}

    def app_cli(self, argv, cache_path=None):
        def perform(event):
            self._cache_preflight(cache_path)
            actual = self._argv(argv)
            event["actual_argv"] = actual
            event["files_before"] = self._snapshot_outputs()
            product_started = time.monotonic()
            result = _run_process(actual, self.output_root, self.timeout, self.max_response_bytes)
            event["product_wall_seconds"] = time.monotonic()-product_started
            event["files_after"] = self._snapshot_outputs()
            if any(v["bytes"] > self.max_file_bytes for v in event["files_after"].values()):
                result["proxy_failure"] = "proxy_file_limit"
            result.update(ok=result["exit_code"] == 0 and not result["proxy_failure"], transport="cli")
            result["envelope"] = _json(result["stdout"] if result["exit_code"] == 0 else result["stderr"])
            if isinstance(result["envelope"], dict) and result["envelope"].get("code") != 0:
                result["ok"] = False
            if result["exit_code"] == 0 and not isinstance(result["envelope"], dict) and actual[-1] not in ("-h", "--help"):
                result.update(ok=False, proxy_failure="proxy_invalid_envelope")
            if result["proxy_failure"]:
                result["proxy_error"] = {"reason": result["proxy_failure"], "message": "APP invocation exceeded a frozen resource limit"}
            if cache_path:
                event["raw_cli_result"] = dict(result)
                cached = self._cache(cache_path, json.dumps(self._visible(result), ensure_ascii=False, allow_nan=False))
                result = {"ok": result["ok"], "transport": "cli", "exit_code": result["exit_code"], "cache": cached,
                          "proxy_error": result.get("proxy_error"), "response_in_cache": True}
            return result
        return self._call("app_cli", {"argv": argv, "cache_path": cache_path}, perform)

    def _query_path(self, path, params):
        if not isinstance(path, str) or not path.startswith("/v1/") or len(path) > 256 or any(c in path for c in "?#%\\"):
            raise ProxyError("proxy_denied", "Expected an exact allowed /v1 route, not a URL")
        route = next((item for item in _HTTP_ROUTES if re.fullmatch(item[0], path)), None)
        if route is None:
            raise ProxyError("proxy_denied", "GET route is not allowed")
        if params is None:
            params = {}
        if not isinstance(params, dict) or set(params) - route[1] or not route[2] <= set(params):
            raise ProxyError("proxy_invalid_parameter", "Unknown or missing query parameter")
        equipment = path.startswith(("/v1/weapon", "/v1/artifact", "/v1/equip-affix"))
        maximum = 2**53 - 1 if equipment else 2**63 - 1
        for part in path.split("/"):
            if part.isdecimal() and not 1 <= int(part) <= maximum:
                raise ProxyError("proxy_invalid_parameter", "Object ID is out of range")
        numeric = {"page", "page_size", "rarity", "character_id", "target_id", "set_id", "level", "ascension", "depot_id", "main_prop_id", "refinement"}
        normalized = {}
        for name, value in params.items():
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ProxyError("proxy_invalid_parameter", "Query values must be strings or integers")
            value = str(value)
            if len(value) > 4096 or any(ord(c) < 32 for c in value):
                raise ProxyError("proxy_invalid_parameter", "Query value is too long or contains controls")
            if name in numeric:
                empty = equipment and name in ("rarity", "set_id") and value == ""
                minimum = 0 if name == "ascension" or name == "level" and path.endswith("/main-stats") else 1
                if not empty and (not re.fullmatch(r"[0-9]+", value) or not minimum <= int(value) <= maximum):
                    raise ProxyError("proxy_invalid_parameter", "Numeric query value is out of range")
                if name == "refinement" and int(value) > 5:
                    raise ProxyError("proxy_invalid_parameter", "refinement must be 1..5")
            if name in ("levels", "set_thresholds"):
                pieces = value.split(",")
                maximum_value = 15 if name == "levels" else 5
                if len(pieces) > maximum_value or len(set(pieces)) != len(pieces) or any(not re.fullmatch(r"[0-9]+", p) or not 1 <= int(p) <= maximum_value for p in pieces):
                    raise ProxyError("proxy_invalid_parameter", "Invalid bounded numeric selector")
            if name == "sections":
                pieces = value.split(",")
                if len(pieces) > 16 or len(set(pieces)) != len(pieces) or any(not re.fullmatch(r"[a-z_]+", p) for p in pieces):
                    raise ProxyError("proxy_invalid_parameter", "Invalid section selector")
            if name == "version_line" and value not in ("release", "beta"):
                raise ProxyError("proxy_invalid_parameter", "Unknown version_line")
            if name == "status" and value != "approved":
                raise ProxyError("proxy_denied", "Only approved annotations are in scope")
            if name == "catalog_scope" and value not in ("codex", "all"):
                raise ProxyError("proxy_invalid_parameter", "Unknown catalog_scope")
            if name == "q" and not value.strip():
                raise ProxyError("proxy_invalid_parameter", "q is required")
            normalized[name] = value
        return path + ("?" + urlencode(normalized) if normalized else "")

    def db_get(self, path, params=None, cache_path=None):
        def perform(event):
            self._cache_preflight(cache_path)
            target = self._query_path(path, params)
            event["actual_url"] = f"http://{self._http_host}:{self._http_port}{target}"
            conn = http.client.HTTPConnection(self._http_host, self._http_port, timeout=self.timeout)
            deadline = time.monotonic() + self.timeout
            try:
                conn.request("GET", target, headers={"Accept": "application/json"})
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProxyError("proxy_timeout", "Database request exceeded its deadline")
                if conn.sock:
                    conn.sock.settimeout(remaining)
                response = conn.getresponse()
                if 300 <= response.status < 400:
                    event["http_status"] = response.status
                    raise ProxyError("proxy_redirect_denied", "Backend redirects are forbidden")
                chunks, size = [], 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ProxyError("proxy_timeout", "Database response exceeded its deadline")
                    sock = getattr(getattr(response.fp, "raw", None), "_sock", None)
                    if sock:
                        sock.settimeout(remaining)
                    chunk = response.read1(min(65536, self.max_response_bytes + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > self.max_response_bytes:
                        raise ProxyError("proxy_output_limit", "Database response exceeds the frozen limit")
                raw = b"".join(chunks)
                body = raw.decode("utf-8")
                envelope = _json(body)
                result = {"ok": 200 <= response.status < 300 and isinstance(envelope, dict) and envelope.get("code") == 0,
                          "transport": "http", "http_status": response.status, "body": body, "envelope": envelope}
                if cache_path:
                    cached = self._cache(cache_path, json.dumps(self._visible(result), ensure_ascii=False, allow_nan=False))
                    event["raw_http_result"] = result
                    result = {"ok": result["ok"], "transport": "http", "http_status": response.status, "cache": cached, "response_in_cache": True}
                return result
            finally:
                conn.close()
        return self._call("db_get", {"path": path, "params": params, "cache_path": cache_path}, perform)
