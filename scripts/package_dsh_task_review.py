"""Sync the two DSH task-review modules into the standalone plugin package."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify packaged files without changing them")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    package = root / "plugins" / "dsh-task-review"
    info = json.loads((package / "package.json").read_text(encoding="utf-8"))
    sources = ["dsh-task-review.mjs", "dsh-cli-task-review-plugin.mjs"]
    generated = {name: (root / "agent-runtime" / "src" / name).read_bytes() for name in sources}
    if f"TASK_REVIEW_VERSION = '{info['version']}'".encode() not in generated[sources[0]]:
        parser.error("task review package and adapter versions differ")
    hashes = {name: hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()
              for name, content in {**generated, "index.mjs": (package / "index.mjs").read_bytes()}.items()}
    compatibility = {"version": info["version"], "dsh": info["engines"]["dsh"].split(" || "),
                     "node": info["engines"]["node"], "module_sha256_lf": hashes}
    generated["compatibility.json"] = (json.dumps(compatibility, indent=2) + "\n").encode()
    for name, content in generated.items():
        path = package / name
        if args.check:
            if not path.exists() or path.read_bytes().replace(b"\r\n", b"\n") != content.replace(b"\r\n", b"\n"):
                parser.error(f"packaged {name} differs; rerun this script without --check")
        else:
            path.write_bytes(content.replace(b"\r\n", b"\n"))


if __name__ == "__main__":
    main()
