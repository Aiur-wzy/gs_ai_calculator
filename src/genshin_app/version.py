"""版本与自更新检测（G5）。

APP_VERSION 为版本号唯一来源；UPDATE_MANIFEST_URL 为更新渠道占位
（一期无分发服务器，默认空 = 跳过检测）。manifest 形状：{"version", "url", "notes"}。
"""

from __future__ import annotations

import json
import urllib.request

APP_VERSION = "1.0.1"
UPDATE_MANIFEST_URL = ""  # 更新渠道确定后在此配置
APP_DIR_NAME = "GenshinCalc"

# App and database releases have separate identities. The portable package
# carries its starting pair; a signed stable index may authorize other exact
# combinations. Advancing only the App preserves the DB ID and sequence.
# Repository runs use ``current_only`` and do not retain historical adapters.
#
# These values identify the most recent *accepted project database*, not a
# game-data commit merely discovered upstream.  Do not advance them until a
# complete SQLite candidate and its manifests have passed the release gate.
# Advance the database ID/sequence only when database content or its own
# release contract changes.  App compatibility is declared by PairManifest,
# not by minting a duplicate database release.
APP_RELEASE_ID = "app-1.0.1"
RELEASE_PAIR_ID = "pair-1.0.1-db-7.2.50-r6"
# APP_VERSION is the feature version; each release ID identifies one immutable
# package and may never be reused for a different rebuild.
RELEASE_PAIR_SEQUENCE = 5
# Exact bytes of the last accepted predecessor pair used by the portable
# release build.  This prevents a builder from selecting an arbitrary older
# in-repository pair merely because its sequence and database happen to look
# compatible.  Advance this pin only after accepting a new portable candidate.
ACCEPTED_PREDECESSOR_PAIR_MANIFEST_SHA256 = (
    "ac57fe3185d8f4be52f46be209003a4be310ec5355a2bec093d99cc3c48e4226"
)
DATABASE_PAIR_POLICY = "current_only"
DATABASE_RELEASE_ID = "db-7.2.50-r6"
DATABASE_RELEASE_SEQUENCE = 8
DATABASE_RELEASE_CHANNEL = "stable"
DATABASE_RELEASE_MANIFEST_URL = ""
# Public verification keys are release inputs, not user preferences.  Each
# item is {key_id, algorithm="rsa-pss-sha256", modulus_hex, exponent}.  The
# current development build intentionally has no production feed/key yet, so
# remote installation remains disabled instead of trusting an unsigned URL.
DATABASE_RELEASE_TRUST_KEYS: tuple[dict[str, object], ...] = ()
DATABASE_LOGIC_PROFILE = "genshin-calculation-v1"
DATABASE_DATA_CONTRACT_VERSION = 1

# Native Agent release trust anchors. These values are compiled into the
# Python application and therefore remain independent from the mutable
# ``agent-runtime`` directory that they authenticate.  A DSH/runtime update
# must update the runtime manifest and these pins in the same reviewed change.
NATIVE_AGENT_RUNTIME_GENERATION = "dsh_client_shell"
# Dedicated DSH client-shell release pin. Retired Phase manifests and their
# pins belong to historical release evidence, not executable fallback paths.
# Set after the final runtime + frontend cohort has been reviewed together.
NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256 = (
    "7f63602ad62f25fb349de1690f893201e67eb0ab2fdfd9836cbb2f7d61751e15"
)
NATIVE_AGENT_NODE_VERSION = "24.19.0"
NATIVE_AGENT_NODE_BINARY_SHA256 = (
    "3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237"
)
NATIVE_AGENT_DSH_RELEASE = "dsh-v0.2.2-rc.3"
NATIVE_AGENT_PACKAGE_MANAGER = "pnpm@11.25.0"
NATIVE_AGENT_DEPENDENCY_MODE = "locked-full-production-closure"

_TIMEOUT = 3


def parse_version(text: str) -> tuple[int, ...]:
    """语义化版本 → 可比较元组（'0.2.1' -> (0, 2, 1)），非法段按 0 计。"""
    parts = []
    for seg in text.strip().lstrip("v").split("."):
        num = "".join(ch for ch in seg if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def check_update(current: str = APP_VERSION,
                 manifest_url: str = UPDATE_MANIFEST_URL) -> dict | None:
    """拉取 manifest 比较版本。有新版返回 manifest dict；否则/失败返回 None（静默容错）。"""
    if not manifest_url:
        return None
    try:
        with urllib.request.urlopen(manifest_url, timeout=_TIMEOUT) as resp:
            manifest = json.loads(resp.read().decode("utf-8"))
        if not isinstance(manifest, dict) or "version" not in manifest or "url" not in manifest:
            return None
        if parse_version(str(manifest["version"])) > parse_version(current):
            return manifest
    except Exception:  # noqa: BLE001 - 网络/格式问题一律静默，不阻塞启动
        return None
    return None
