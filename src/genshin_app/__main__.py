"""统一入口：`python -m genshin_app ...`。"""

import sys

from genshin_app.cli import main

if __name__ == "__main__":
    sys.exit(main())
