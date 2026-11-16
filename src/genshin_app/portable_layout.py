"""Sealed portable path selection; normal App/database admission remains required.

The marker contains no user-controlled path. It only opts a bundled App into
the fixed sibling app/data layout. An installed product is resolved first.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .installation_location import checked_path
from .package_integrity import PackageIntegrityError, _read_package_manifest
from .product_install_contract import FirstInstallError

PORTABLE_MARKER = "portable-layout.json"
PORTABLE_LAYOUT = {"contract": "genshin-portable-layout-v1", "app_directory": "app", "data_directory": "data"}
PORTABLE_MARKER_BYTES = (json.dumps(PORTABLE_LAYOUT, sort_keys=True, separators=(",", ":")) + "\n").encode()


def resolve_portable_data(executable: Path | str) -> Path:
    """Verify fixed routing metadata, not executable or database integrity.

    The caller must still execute complete ordinary startup admission. Missing
    or damaged metadata in a new portable layout must not pick an older profile.
    """
    from genshin_app import version
    from genshin_db.release_profile import (
        DatabaseReleaseError,
        load_release_pair_manifest,
    )

    app = Path(executable).absolute().parent
    marker = app / PORTABLE_MARKER
    if not marker.exists():
        raise FirstInstallError("portable_layout_missing")
    try:
        app = checked_path(app, reason="portable_layout_path_invalid")
        if app.name != "app":
            raise FirstInstallError("portable_layout_app_directory_invalid")
        marker = checked_path(app / PORTABLE_MARKER, reason="portable_layout_path_invalid")
        pair_path = checked_path(app / "_internal/bundled/release-pair-manifest.json",
                                 reason="portable_layout_path_invalid")
        if (not marker.is_file() or marker.stat().st_size > 4096 or marker.stat().st_nlink != 1
                or marker.read_bytes() != PORTABLE_MARKER_BYTES
                or not pair_path.is_file() or pair_path.stat().st_size > 1024 * 1024):
            raise FirstInstallError("portable_layout_invalid")
        _root, raw, members = _read_package_manifest(app)
        pair = load_release_pair_manifest(pair_path)
        if (members.get(PORTABLE_MARKER) != hashlib.sha256(PORTABLE_MARKER_BYTES).hexdigest()
                or hashlib.sha256(raw).hexdigest() != pair["app"]["artifact_manifest"]["sha256"]
                or pair["app"]["app_release_id"] != version.APP_RELEASE_ID
                or pair["database"]["database_release_id"] != version.DATABASE_RELEASE_ID):
            raise FirstInstallError("portable_layout_manifest_mismatch")
        data = checked_path(app.parent / "data", reason="portable_layout_path_invalid")
        if data.exists() and not data.is_dir():
            raise FirstInstallError("portable_layout_data_invalid")
        database = checked_path(data / "genshin.db", reason="portable_layout_path_invalid")
        if database.exists() and (not database.is_file() or database.stat().st_nlink != 1):
            raise FirstInstallError("portable_layout_database_link_unsupported")
        return data
    except FirstInstallError:
        raise
    except (OSError, ValueError, PackageIntegrityError, DatabaseReleaseError) as error:
        raise FirstInstallError("portable_layout_invalid") from error
