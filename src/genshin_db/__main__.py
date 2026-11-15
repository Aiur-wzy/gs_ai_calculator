"""兼容入口：`python -m genshin_db ...` 薄转发到统一 CLI（genshin_app）。

保留原因：data-access.md 等已分发文档引用本入口；行为与统一 CLI 完全一致。
"""

import sys


def main(argv: list[str] | None = None) -> int:
    from genshin_app.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
