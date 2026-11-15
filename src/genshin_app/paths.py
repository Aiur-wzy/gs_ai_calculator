"""运行路径定位（G5）：开发态 vs PyInstaller frozen 态。

- resource_dir：捆绑只读资源（web 前端、预构建库、skill/）。frozen 时在 sys._MEIPASS 下。
- default_data_dir：开发态 ./data；frozen 态 %LOCALAPPDATA%\\GenshinCalc\\data
  （Program Files 安装目录不可写）。
- ensure_data_dir：首启初始化——目录创建 + 预构建 genshin.db 复制（离线可用）。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from uuid import uuid4

from genshin_app.database_transaction import serialized_profile_writer
from genshin_app.version import APP_DIR_NAME


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]  # 仓库根


def bundled_db_path() -> Path:
    """预构建库位置：frozen 在捆绑资源里；开发态即 data/genshin.db 本身。"""
    if is_frozen():
        return resource_dir() / "bundled" / "genshin.db"
    return resource_dir() / "data" / "genshin.db"


def skill_dir() -> Path:
    """skill/ 目录：frozen 时捆绑进 _internal/skill。"""
    return resource_dir() / "skill"


def web_dir() -> Path:
    """GUI 前端资源目录（frozen 时不能用 __file__ 定位，模块在 PYZ 归档内）。"""
    if is_frozen():
        return resource_dir() / "genshin_app" / "gui" / "web"
    return Path(__file__).parent / "gui" / "web"


def default_data_dir() -> Path:
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME / "data"
    return Path("data")


@serialized_profile_writer()
def ensure_data_dir(data_dir: Path | None = None) -> Path:
    """确保数据目录可用：建目录 + 缺失时原子复制预构建库。

    已存在数据库的版本升级不属于普通启动。安装器会在用户同意后
    调用受审的发行迁移器；本函数只处理“完全没有活动库”的首次初始化。
    """
    data_dir = Path(data_dir) if data_dir else default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "build").mkdir(exist_ok=True)
    (data_dir / "exports").mkdir(exist_ok=True)
    (data_dir / "plans").mkdir(exist_ok=True)

    live_db = data_dir / "genshin.db"
    bundled = bundled_db_path()
    if not live_db.exists() and bundled.exists() and bundled.resolve() != live_db.resolve():
        if is_frozen():
            from genshin_app.database_legacy_processes import packaged_peer_processes
            if packaged_peer_processes():
                from genshin_app.database_bootstrap import DatabaseStartupError
                raise DatabaseStartupError("database_legacy_instance_running", "请先关闭其他原神拉表窗口，再重新检查。")
        temporary = live_db.with_name(f".{live_db.name}.{uuid4().hex}.tmp")
        try:
            shutil.copyfile(bundled, temporary)
            # A killed first launch must not leave a truncated file at the
            # active path which all later launches mistake for an installed DB.
            with temporary.open("rb+") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, live_db)
        finally:
            temporary.unlink(missing_ok=True)
    return data_dir
