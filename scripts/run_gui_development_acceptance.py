"""Open the production GUI/DSH composition with an isolated development profile.

This is a source acceptance launcher, not a replacement Agent implementation.
No provider credentials are read unless --allow-provider was explicitly passed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--open", type=Path)
    parser.add_argument("--allow-provider", action="store_true")
    parser.add_argument("--runtime-manifest", type=Path)
    parser.add_argument("--database", type=Path, required=True, help="Explicit reviewed database snapshot; never infer it from game_version.")
    args = parser.parse_args()
    profile = args.profile.resolve()
    if not profile.is_relative_to(ROOT / "build"):
        parser.error("Acceptance profiles must be below this checkout's build directory.")
    profile.mkdir(parents=True, exist_ok=True)
    database = args.database.resolve(strict=True)

    import webview

    from genshin_app.agent_chat.credential_generation import (
        CredentialGenerationController,
    )
    from genshin_app.agent_chat.credential_store import default_credential_store
    from genshin_app.agent_chat.native_dsh_production import (
        create_native_dsh_application,
    )
    from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
    from genshin_app.gui.api import GuiApi
    from genshin_app.gui.launcher import (
        _create_main_window,
        _shutdown_agent_transports,
        install_close_guard,
    )
    from genshin_app.windows_product_registration import set_process_app_id

    class DisabledCredentials:
        available = False
        def read_versioned(self):
            raise RuntimeError("acceptance_credentials_disabled")
        def compare_and_swap(self, **_):
            raise RuntimeError("acceptance_credentials_disabled")

    credentials = CredentialGenerationController(default_credential_store() if args.allow_provider else DisabledCredentials())
    registry = NativeDshWorkspaceRegistry(profile / "workspaces", database_path=database)
    identity = {"mode": "native_agent_development" if args.allow_provider else "development_acceptance",
                "network_mode": "provider_only" if args.allow_provider else "disabled",
                "label": "本轮源码隔离验收"}
    def factory(data_dir):
        return create_native_dsh_application(data_dir, workspaces=registry, credentials=credentials,
            verification_mode="development", runtime_root=ROOT / "agent-runtime", assets_root=ROOT / "frontend",
            manifest_path=args.runtime_manifest,
            node_executable=ROOT / "agent-runtime/node.exe")
    api = GuiApi(profile, database_path=database, app_identity=identity,
                 startup_path=args.open, dsh_workspace_registry=registry, dsh_credentials=credentials,
                 dsh_shell_factory=factory)
    set_process_app_id()
    try:
        window = _create_main_window(webview, api, args.open, identity)
        api._window = window
        install_close_guard(window, api)
        webview.start(storage_path=str(profile / "webview-profile"), private_mode=False)
    finally:
        _shutdown_agent_transports(api)


if __name__ == "__main__":
    main()
