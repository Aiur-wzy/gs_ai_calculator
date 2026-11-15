"""Verify a portable package against its sealed PACKAGE-FILES.sha256."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from release_supply_chain import SupplyChainError, verify_package_file_manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="独立复算便携包文件集合与 PACKAGE-FILES.sha256",
    )
    parser.add_argument("package_root", help="包含 PACKAGE-FILES.sha256 的便携包根目录")
    args = parser.parse_args(argv)
    try:
        report = verify_package_file_manifest(Path(args.package_root))
    except SupplyChainError as error:
        parser.error(error.code)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
