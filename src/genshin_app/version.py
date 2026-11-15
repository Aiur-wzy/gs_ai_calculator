"""版本与自更新检测（G5）。

APP_VERSION 为版本号唯一来源；UPDATE_MANIFEST_URL 为更新渠道占位
（一期无分发服务器，默认空 = 跳过检测）。manifest 形状：{"version", "url", "notes"}。
"""

from __future__ import annotations

import json
import urllib.request

APP_VERSION = "0.46.0"
UPDATE_MANIFEST_URL = ""  # 更新渠道确定后在此配置
APP_DIR_NAME = "GenshinCalcStable"

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
APP_RELEASE_ID = "app-0.46.0-r20"
RELEASE_PAIR_ID = "pair-0.46.0-r20-db-7.2.50-r6"
# APP_VERSION is the feature version; each release ID identifies one immutable
# package and may never be reused for a different rebuild.
RELEASE_PAIR_SEQUENCE = 2
# Exact bytes of the last accepted predecessor pair used by the portable
# release build.  This prevents a builder from selecting an arbitrary older
# in-repository pair merely because its sequence and database happen to look
# compatible.  Advance this pin only after accepting a new portable candidate.
ACCEPTED_PREDECESSOR_PAIR_MANIFEST_SHA256 = "5e69574f542a85ffb535820d8232600a5d593b827d99391ec21d6dba4a6c4057"
DATABASE_PAIR_POLICY = "exact"
DATABASE_RELEASE_ID = "db-7.2.50-r6"
DATABASE_RELEASE_SEQUENCE = 8
DATABASE_RELEASE_CHANNEL = "stable"
DATABASE_RELEASE_MANIFEST_URL = "https://aiur-wzy.github.io/gs_ai_calculator/latest.json"
# Public verification keys are release inputs, not user preferences.  Each
# item is {key_id, algorithm="rsa-pss-sha256", modulus_hex, exponent}.  The
# Stable builds verify releases with the owner's offline public key.
DATABASE_RELEASE_TRUST_KEYS: tuple[dict[str, object], ...] = (
    {'key_id': '8330b1e85d36355db432ce6fdfaf54ba3faad529724dba275d03c77c8440f1df', 'algorithm': 'rsa-pss-sha256', 'modulus_hex': 'eb3802468297c736e4051ef50c3c4b72e2cae9632349ffdf3529d3f9467a74e2a48f763d21d88b6d91f017dae50642d72d895fa52c20c1814531f77030b8c6860c363ffef9fada3dab01bed048e4b91f6357bf53347e278346495f4be08d1391efc03684b8ddfca823193bd5107f1543ecd38cfade1dc23968f724eafa1f89247b56c3d3ae4245b75798684ca9f706a8ef594bc3b7d6319584881ea4c17ee43957da68e0ea79f19478594d265794d5bf5da57d932be5324dd2370e49669a5aecce9c382e813ce5f5fad9c08ca59fc4d3f3f63851ef5ffb5322bb0a2d11b709dcc9ea15cba1881cbf5693b7d167ff66a5a181d951f0a7724f77abad88fa817da59f8e5c849bf99a9794b5db9e99aa1b42652a451957233332ba7d4bbd44c202109a73c17996542412a6a98e35a41fa05ed65587d1a1afd27f08bf4e8480a9b98a3390759bb6f7c3a1fde1b674476b12156ca4a33957565ca4bc36d47f49d724160fec01b034e0c9889b82027afa5237455998625af9c429d8a019fcd9da7bbb47', 'exponent': 65537},
)
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
    "9c5d48d9e8d3b221c5be4c52a786e74cb45050853484633b90b8d4c2c57327d1"
)
NATIVE_AGENT_NODE_VERSION = "24.19.0"
NATIVE_AGENT_NODE_BINARY_SHA256 = (
    "3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237"
)
NATIVE_AGENT_DSH_RELEASE = "dsh-v0.2.1-rc.5"
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
