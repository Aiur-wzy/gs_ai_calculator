"""Exact source-only DOM verifier role for the offline production selfcheck.

The portable Host qualification has no development DOM dependencies. This
helper is used only by source QA; its preload is generated inside that QA root.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlencode

from .frozen_selfcheck_isolation import SelfcheckError

_ENV_KEYS = frozenset({"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT", "COMSPEC"})
_OPTIONS = frozenset({"cwd", "env", "stdin", "stdout", "stderr", "creationflags", "text", "encoding"})


def prepare_source_frontend_role(*, root, runtime_root, package_root):
    root, runtime, package = (Path(value).resolve() for value in (root, runtime_root, package_root))
    entry = package / "frontend/scripts/app-document-projection.mjs"
    report = root / "frontend-node-isolation.json"
    marker = root / "frontend-provider-context.json"
    preload = root / "frontend-offline-preload.mjs"
    context = {"contract": "genshin-source-frontend-offline-v1", "sandbox_root": str(root),
               "package_root": str(package), "main_entry": str(entry), "report": str(report)}
    with marker.open("x", encoding="utf-8") as stream:
        json.dump(context, stream, ensure_ascii=False)
    template = """import fs from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { installSelfcheckGuards } from GUARDS_URL;
const marker = new URL(import.meta.url).searchParams.get('context');
if (marker === null) throw new Error('selfcheck_frontend_context_required');
const context = JSON.parse(fs.readFileSync(fileURLToPath(marker), 'utf8'));
const root = resolve(context.sandbox_root);
if (JSON.stringify(context) !== JSON.stringify(CONTEXT_VALUE)
    || context.contract !== 'genshin-source-frontend-offline-v1' || process.argv.length !== 2
    || resolve(process.argv[1]) !== resolve(context.main_entry)
    || resolve(dirname(fileURLToPath(marker))) !== root
    || resolve(context.report) !== resolve(root, 'frontend-node-isolation.json'))
  throw new Error('selfcheck_frontend_context_invalid');
const guards = installSelfcheckGuards({ sandboxRoot: root, packageRoot: context.package_root });
const persist = exitObserved => fs.writeFileSync(context.report, JSON.stringify({
  contract: 'genshin-source-frontend-node-isolation-v1', counters: guards.counts,
  denied: guards.denied, main_entry: process.argv[1], process_exit_observed: exitObserved,
  executed_forbidden_operations: 0,
}));
globalThis.fetch = async () => guards.denyNetwork();
guards.negativeProbe(() => guards.denyNetwork());
persist(false);
process.once('exit', () => { persist(true); guards.assertClean(); });
"""
    with preload.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(template.replace("GUARDS_URL", json.dumps(
            (runtime / "src/native-dsh-selfcheck-guards.mjs").as_uri()))
            .replace("CONTEXT_VALUE", json.dumps(context, ensure_ascii=False)))
    ordinary = (str(runtime / "node.exe"), str(entry))
    module = preload.as_uri() + "?" + urlencode({"context": marker.as_uri()})
    return {"ordinary": ordinary, "actual": (ordinary[0], "--import", module, ordinary[1]),
            "cwd": package, "report": report}


def source_frontend_options_match(role, arguments, args, options):
    try:
        correct_cwd = Path(options.get("cwd", "")).resolve() == role["cwd"]
    except (TypeError, ValueError, OSError):
        return False
    if (args or type(arguments) not in (list, tuple) or tuple(arguments) != role["ordinary"]
            or set(options) != _OPTIONS or not correct_cwd
            or options.get("text") is not True or options.get("encoding") != "utf-8"
            or options.get("creationflags") != getattr(subprocess, "CREATE_NO_WINDOW", 0)
            or any(options.get(key) != subprocess.PIPE for key in ("stdin", "stdout", "stderr"))):
        return False
    environment = options.get("env")
    return (isinstance(environment, Mapping)
            and all(type(key) is str and type(value) is str and key.upper() in _ENV_KEYS
                    for key, value in environment.items()))


def verify_source_frontend_role(role):
    try:
        value = json.loads(role["report"].read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SelfcheckError("selfcheck_frontend_isolation_missing") from error
    if (not isinstance(value, dict) or value.get("contract") != "genshin-source-frontend-node-isolation-v1"
            or value.get("main_entry") != role["ordinary"][1]
            or value.get("process_exit_observed") is not True
            or value.get("executed_forbidden_operations") != 0
            or not isinstance(value.get("counters"), dict)
            or any(not isinstance(value["counters"].get(group), dict)
                   or any(type(count) is not int or count < 0 for count in value["counters"][group].values())
                   for group in ("normal", "negative"))
            or value.get("denied") != []
            or any(value["counters"]["normal"].values())):
        raise SelfcheckError("selfcheck_frontend_isolation_invalid")
    return value
