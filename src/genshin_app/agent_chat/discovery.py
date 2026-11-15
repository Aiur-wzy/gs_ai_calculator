"""Bounded, read-only discovery of registered Agent CLI installations.

Names and package metadata are discovery hints, never execution authority.  No
candidate (including Codex) is run during discovery; the transport independently
checks its pinned binary again before connecting.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

MAX_DIRECTORIES = 1024
MAX_DIRECTORY_ENTRIES = 30000
MAX_MANIFEST_BYTES = 256 * 1024
MAX_BINARY_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    commands: tuple[str, ...]
    npm_packages: tuple[str, ...] = ()


PROVIDERS = (
    Provider("codex", "Codex", ("codex",), ("@openai/codex",)),
    Provider("claude", "Claude Code", ("claude",), ("@anthropic-ai/claude-code",)),
    Provider("gemini", "Gemini CLI", ("gemini",), ("@google/gemini-cli",)),
    Provider("opencode", "OpenCode", ("opencode",), ("@opencode/cli", "opencode-ai")),
    Provider("cursor", "Cursor Agent", ("agent", "cursor-agent")),
    Provider("aider", "Aider", ("aider",)),
    Provider("goose", "Goose", ("goose",)),
    Provider("amp", "Amp", ("amp",), ("@ampcode/cli", "@sourcegraph/amp")),
    Provider("copilot", "GitHub Copilot CLI", ("copilot",), ("@github/copilot",)),
    Provider("cline", "Cline CLI", ("cline",), ("cline",)),
    Provider("qwen", "Qwen Code", ("qwen",), ("@qwen-code/qwen-code",)),
    Provider("droid", "Droid", ("droid",), ("droid",)),
    Provider("codearts", "CodeArts Doer CLI", ("codearts",)),
    Provider("qoder", "Qoder CLI", ("qoder",), ("@qoder-ai/qodercli",)),
    Provider("kiro", "Kiro CLI", ("kiro-cli",)),
)


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _remote(value: str) -> bool:
    # Device paths can address network or special files too; this scanner only
    # needs ordinary local paths.  Do not stat a UNC entry before rejecting it.
    return value.startswith(("\\\\", "//"))


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _common_directories(env: Mapping[str, str]) -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []

    def add(variable: str, suffix: str, label: str) -> None:
        if env.get(variable):
            result.append((Path(env[variable]) / suffix, label))

    for suffix in (".local/bin", "bin", ".bun/bin", ".cargo/bin", "scoop/shims",
                   ".factory/bin", ".amp/bin", ".opencode/bin",
                   ".local/share/opencode/bin", ".codeartsdoer/installers"):
        add("USERPROFILE", suffix, "用户常见 CLI 安装目录")
    add("HOME", ".local/bin", "用户常见 CLI 安装目录")
    add("APPDATA", "npm", "用户 npm 安装目录")
    add("LOCALAPPDATA", "pnpm", "用户 pnpm 安装目录")
    add("LOCALAPPDATA", "cursor-agent", "Cursor Agent 官方安装目录")
    add("LOCALAPPDATA", "Microsoft/WinGet/Links", "WinGet 命令入口目录")
    add("PROGRAMFILES", "Kiro-Cli", "Kiro CLI 官方安装目录")
    add("PROGRAMFILES", "nodejs", "系统 Node/npm 安装目录")
    for variable, suffix in (("PNPM_HOME", ""), ("NPM_CONFIG_PREFIX", ""),
                             ("BUN_INSTALL", "bin"), ("CARGO_HOME", "bin"),
                             ("SCOOP", "shims"), ("NVM_SYMLINK", "")):
        add(variable, suffix, "已配置的 CLI 安装目录")
    return result


class _Scanner:
    def __init__(self, env: Mapping[str, str], windows: bool, cwd: Path,
                 codex_version: str, codex_sha256: str,
                 digest_file: Callable[[Path], str]):
        self.env, self.windows, self.cwd = env, windows, cwd
        self.codex_version, self.codex_sha256 = codex_version, codex_sha256
        self.digest_file = digest_file
        self.issues: list[dict] = []
        self.path_directories: list[str] = []
        self.scanned_directories: list[str] = []
        self.directories: dict[str, dict] = {}
        self.candidates: dict[str, dict] = {}
        self.manifests: dict[str, dict | None] = {}
        self.extensions = {"", ".exe", ".cmd", ".bat", ".ps1"} if windows else {"", ".sh"}
        if windows:
            for extension in env.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";"):
                if re.fullmatch(r"\.[a-zA-Z0-9_]{1,16}", extension):
                    self.extensions.add(extension.lower())
                elif extension:
                    self.issue("PATHEXT", "忽略了格式无效的命令扩展名。")

    def key(self, path: Path | str) -> str:
        value = str(path)
        return value.casefold() if self.windows else value

    def issue(self, path: Path | str, message: str) -> None:
        value = {"path": str(path), "message": message}
        if value not in self.issues:
            self.issues.append(value)

    def normalize(self, value: Path | str) -> Path | None:
        raw = str(value).strip().strip('"')
        if not raw or "\x00" in raw or _remote(raw):
            self.issue(raw, "跳过空值、无效路径或网络/设备路径；扫描仅访问本机普通路径。")
            return None
        try:
            path = Path(raw)
            if not path.is_absolute():
                path = self.cwd / path
            resolved = path.resolve()
            if _remote(str(resolved)):
                self.issue(path, "跳过指向网络或设备的路径。")
                return None
            return resolved
        except (OSError, ValueError, RuntimeError):
            self.issue(raw, "无法规范化路径。")
            return None

    def add_directory(self, value: Path | str, source: str, *, path_entry: bool = False) -> None:
        path = self.normalize(value)
        if path is None:
            return
        key = self.key(path)
        if path_entry and not any(self.key(x) == key for x in self.path_directories):
            self.path_directories.append(str(path))
        if key not in self.directories:
            if len(self.directories) >= MAX_DIRECTORIES:
                self.issue(path, "目录数量超过扫描上限；本次扫描未覆盖全部声明范围。")
                return
            self.directories[key] = {"path": path, "sources": [], "required": path_entry}
        item = self.directories[key]
        item["required"] |= path_entry
        if source not in item["sources"]:
            item["sources"].append(source)

    def children(self, value: Path, pattern: str, suffix: str, source: str) -> None:
        path = self.normalize(value)
        if path is None:
            return
        try:
            if not path.exists():
                return
            with os.scandir(path) as entries:
                for index, entry in enumerate(entries):
                    if index >= MAX_DIRECTORY_ENTRIES:
                        self.issue(path, "目录项数量超过扫描上限。")
                        break
                    if re.fullmatch(pattern, entry.name, re.IGNORECASE) and entry.is_dir(follow_symlinks=False):
                        self.add_directory(Path(entry.path) / suffix, source)
        except (OSError, ValueError):
            self.issue(path, "无法读取已知安装目录。")

    def expand_known_directories(self) -> None:
        for variable, suffix, pattern, child_suffix, label in (
            ("LOCALAPPDATA", "OpenAI/Codex/bin", r"[0-9a-f]{8,64}", "", "Codex 桌面应用附带 CLI"),
            ("LOCALAPPDATA", "Programs/Python", r"Python\d+[a-z0-9_-]*", "Scripts", "Python 用户命令目录"),
            ("APPDATA", "Python", r"Python\d+[a-z0-9_-]*", "Scripts", "Python 用户命令目录"),
            ("NVM_HOME", "", r"v?\d+\.\d+\.\d+", "", "NVM Node/npm 安装目录"),
        ):
            if self.env.get(variable):
                self.children(Path(self.env[variable]) / suffix, pattern, child_suffix, label)

    def manifest(self, module_root: Path, package: str, provider: Provider) -> dict | None:
        location = module_root / package / "package.json"
        cache_key = self.key(location)
        if cache_key in self.manifests:
            return self.manifests[cache_key]
        self.manifests[cache_key] = None
        try:
            if not location.exists():
                return None
            root = module_root.resolve()
            target = location.resolve()
            if not _inside(target, root) or _remote(str(target)):
                self.issue(location, "包元数据指向安装范围外；未读取。")
                return None
            info = target.stat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1
                    or info.st_size > MAX_MANIFEST_BYTES):
                self.issue(location, "包元数据不是可安全读取的普通小文件。")
                return None
            with target.open("rb") as stream:
                raw = stream.read(MAX_MANIFEST_BYTES + 1)
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or data.get("name") != package:
                self.issue(location, "包名称与注册目录不符；未采信元数据。")
                return None
            package_root = target.parent
            version = data.get("version")
            if not isinstance(version, str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+_-]{0,127}", version):
                version = None
            bins = data.get("bin", {})
            if isinstance(bins, str):
                bins = {provider.commands[0]: bins}
            if not isinstance(bins, dict):
                self.issue(location, "包命令声明格式无效。")
                return None
            targets = []
            for command in provider.commands:
                relative = bins.get(command)
                if relative is None:
                    continue
                if (not isinstance(relative, str) or len(relative) > 2048
                        or "\x00" in relative or _remote(relative)
                        or Path(relative).is_absolute() or re.match(r"^[A-Za-z]:", relative)):
                    self.issue(location, "包命令目标路径无效；未采信。")
                    return None
                candidate = (package_root / relative).resolve()
                if not _inside(candidate, package_root) or not _inside(candidate, root):
                    self.issue(location, "包命令目标越出本包范围；未读取。")
                    return None
                if candidate.is_file():
                    targets.append(candidate)
            native = None
            if provider.id == "codex":
                for relative in (
                    "node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe",
                    "vendor/x86_64-pc-windows-msvc/bin/codex.exe",
                ):
                    candidate = (package_root / relative).resolve()
                    if not _inside(candidate, root):
                        self.issue(location, "已知原生程序布局越出安装范围；未读取。")
                        continue
                    if candidate.is_file():
                        native = candidate
                        break
            result = {"root": package_root, "version": version, "native": native,
                      "targets": targets, "manifest": location}
            if not targets and native is None:
                self.issue(location, "已知包缺少可定位的命令入口或原生程序。")
            self.manifests[cache_key] = result
            return result
        except (OSError, ValueError, RuntimeError, UnicodeError, RecursionError):
            self.issue(location, "无法读取或解析包元数据。")
            return None

    def add_candidate(self, provider: Provider, path: Path, entries: Sequence[Path],
                      sources: Sequence[str], package: dict | None = None) -> None:
        resolved = self.normalize(path)
        if resolved is None:
            return
        identity = self.key(resolved if package is None or package["native"] else package["root"])
        key = provider.id + ":" + identity
        if key in self.candidates:
            item = self.candidates[key]
        else:
            item = {"id": hashlib.sha256(key.encode()).hexdigest()[:20], "path": str(resolved),
                    "provider": provider.name, "provider_id": provider.id,
                    "version": package["version"] if package else None, "sha256": None,
                    "supported": False, "entry_paths": [], "sources": [],
                    "entry_note": "关联入口只表示相同文件或已知安装布局；脚本未执行、未核验。连接只使用上方通过核验的原生程序。",
                    "status": "unadapted" if package else "unverified",
                    "reason": "已读取已知包元数据；该 CLI 尚未适配受控连接。" if package else
                    "命令名称与已知 Agent 匹配，尚未核验其身份和连接协议。",
                    "kind": "npm_wrapper" if package else "script"}
            if package:
                item["version_source"] = "package_metadata"
            try:
                info = resolved.stat()
                if not stat.S_ISREG(info.st_mode):
                    self.issue(path, "命令入口不是普通文件；已跳过。")
                    return
                header = b""
                if resolved.suffix.lower() == ".exe":
                    with resolved.open("rb") as stream:
                        header = stream.read(2)
                native = resolved.suffix.lower() == ".exe" and header == b"MZ"
                if native:
                    item["kind"] = "native"
                if provider.id == "codex" and native:
                    if info.st_size > MAX_BINARY_BYTES:
                        self.issue(path, "原生程序超过指纹读取上限；未核验。")
                    else:
                        item["sha256"] = self.digest_file(resolved)
                        if item["sha256"] == self.codex_sha256:
                            item["version"] = self.codex_version
                            item["version_source"] = "binary_fingerprint"
                            item["supported"] = self.windows
                            item["status"] = "supported" if self.windows else "unadapted"
                            item["reason"] = ("文件指纹与已验证的 Codex 版本一致，可进行受控连接。"
                                              if self.windows else "文件指纹已核验；当前平台暂不支持受控连接。")
                        else:
                            item["status"] = "unverified"
                            item["reason"] = "发现 Codex 候选，但文件指纹不在已验证范围；不能连接。"
                elif not package and provider.id in {"cursor", "codearts"}:
                    item["reason"] = "命令名称可能与编辑器或其他程序重名，仅列为候选，身份尚未核验。"
            except (OSError, ValueError, RuntimeError):
                item["status"] = "unreadable"
                item["reason"] = "命令入口无法读取，不能核验或连接。"
                self.issue(path, "命令入口无法读取。")
            self.candidates[key] = item
        for entry in (*entries, resolved):
            value = str(entry)
            if not any(self.key(x) == self.key(value) for x in item["entry_paths"]):
                item["entry_paths"].append(value)
        for source in sources:
            if source not in item["sources"]:
                item["sources"].append(source)

    def scan_directory(self, record: dict) -> None:
        directory = record["path"]
        matched: dict[str, list[Path]] = {p.id: [] for p in PROVIDERS}
        names = {command + extension: provider for provider in PROVIDERS
                 for command in provider.commands for extension in self.extensions}
        try:
            if not directory.exists():
                if record["required"]:
                    self.issue(directory, "PATH 目录不存在。")
                return
            with os.scandir(directory) as entries:
                self.scanned_directories.append(str(directory))
                for index, entry in enumerate(entries):
                    if index >= MAX_DIRECTORY_ENTRIES:
                        self.issue(directory, "目录项数量超过扫描上限；未扫描剩余条目。")
                        break
                    name = entry.name.lower() if self.windows else entry.name
                    provider = names.get(name)
                    if provider:
                        matched[provider.id].append(Path(entry.path))
        except (OSError, ValueError):
            self.issue(directory, "无法读取命令目录。")
            return
        for provider in PROVIDERS:
            paths = sorted(matched[provider.id], key=lambda p: self.key(p))
            packages = []
            # These are fixed npm layouts, not recursive discovery or arbitrary
            # manifest-directed paths. Read only registered package names.
            for module_root in (directory / "node_modules", directory.parent / "lib/node_modules"):
                for name in provider.npm_packages:
                    package = self.manifest(module_root, name, provider)
                    if package and (package["targets"] or package["native"]):
                        packages.append(package)
            if len(packages) == 1:
                package = packages[0]
                # Proximity alone cannot show what an opaque shim actually
                # launches. Keep it independent; only identical resolved file
                # identities are merged. Never bless a script with the native
                # package binary's fingerprint.
                target = package["native"] or package["targets"][0]
                self.add_candidate(provider, target, package["targets"], record["sources"], package)
                for path in paths:
                    self.add_candidate(provider, path, [path], record["sources"])
            elif packages:
                # Do not guess which historical/current package an opaque
                # wrapper invokes. Keep distinct package installs and entries.
                for package in packages:
                    target = package["native"] or package["targets"][0]
                    self.add_candidate(provider, target, package["targets"], record["sources"], package)
                for path in paths:
                    self.add_candidate(provider, path, [path], record["sources"])
                self.issue(directory, "同产品存在多个包安装，包装器归属不能仅凭名称确定。")
            else:
                for path in paths:
                    self.add_candidate(provider, path, [path], record["sources"])


def discover_agents(*, codex_version: str, codex_sha256: str,
                    env: Mapping[str, str] | None = None, windows: bool | None = None,
                    cwd: Path | None = None,
                    common_directories: Sequence[tuple[Path, str]] | None = None,
                    digest_file: Callable[[Path], str] = _digest) -> dict:
    """Inspect a finite registered scope without subprocesses or credentials."""
    is_windows = os.name == "nt" if windows is None else windows
    environment = {(key.upper() if is_windows else key): value
                   for key, value in (os.environ if env is None else env).items()}
    scanner = _Scanner(environment, is_windows, cwd or Path.cwd(), codex_version,
                       codex_sha256, digest_file)
    for index, directory in enumerate(environment.get("PATH", "").split(";" if is_windows else os.pathsep)):
        if directory.strip():
            scanner.add_directory(directory, f"PATH 第 {index + 1} 项", path_entry=True)
    for directory, source in (_common_directories(environment) if common_directories is None else common_directories):
        scanner.add_directory(directory, source)
    if common_directories is None:
        scanner.expand_known_directories()
    for record in scanner.directories.values():
        scanner.scan_directory(record)
    return {"candidates": list(scanner.candidates.values()), "supported_version": codex_version,
            "scan": {"path_directories": scanner.path_directories,
                     "scanned_directories": scanner.scanned_directories,
                     "issues": scanner.issues, "complete": not scanner.issues,
                     "scope": "已注册的 Agent 命令、当前进程 PATH 与常见安装目录；不识别任意重命名程序。"},
            "providers": [{"id": p.id, "name": p.name, "commands": list(p.commands),
                           "npm_packages": list(p.npm_packages), "adapter_available": p.id == "codex"}
                          for p in PROVIDERS]}
