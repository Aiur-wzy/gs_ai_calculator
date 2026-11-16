"""Verify final browser bytes without rebuilding or substituting source resources."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path

BASE_SOURCE_FILES = {
    "package.json", "pnpm-lock.yaml", "tsconfig.json", "dsh-client-composition.json",
    "vite.business.config.ts", "scripts/build-source-provenance.mjs",
    "scripts/build-dsh-domain-plugin.mjs", "scripts/sync-business-ui.mjs",
    "vite.legacy.config.ts", "scripts/sync-production-foundation.mjs",
}
PRODUCTION_FILES = {
    "dsh-client-accepted-manifest.json", "vite.production.config.ts",
    "scripts/production-provenance.mjs", "scripts/sync-production-dsh.mjs",
    "scripts/write-build-metadata.mjs",
}
_EXCLUDED_DIRS = {"lib", "dist", "node_modules", "__tests__"}
_SOURCE_EXTENSIONS = {".ts", ".tsx", ".js", ".mjs", ".css", ".json"}
_LEGACY_STYLE_LIST = "frontend/scripts/legacy-styles.json"
_LEGACY_GENERATOR = "frontend/scripts/build-legacy-island-css.mjs"
_LEGACY_WEB = "src/genshin_app/gui/web/"


def _legacy_styles(value: object) -> list[str]:
    if (not isinstance(value, list) or not value
            or any(not isinstance(name, str) or re.fullmatch(r"[a-z0-9-]+\.css", name) is None for name in value)
            or len(value) != len(set(value))):
        raise ValueError("dsh_legacy_css_source_list_invalid")
    return value


def legacy_css_inputs(frontend: Path) -> dict:
    """Read the generator's one source list; do not maintain a parallel CSS list."""
    root = Path(frontend).parent

    def read(name: str) -> bytes:
        path = root / name
        if path.is_symlink():
            raise ValueError("dsh_legacy_css_source_symlink")
        return path.read_bytes()

    list_bytes = read(_LEGACY_STYLE_LIST)
    styles = _legacy_styles(json.loads(list_bytes))
    names = {_LEGACY_GENERATOR, _LEGACY_STYLE_LIST, *(_LEGACY_WEB + name for name in styles)}
    inputs = {name: hashlib.sha256(read(name)).hexdigest() for name in sorted(names)}
    if inputs[_LEGACY_STYLE_LIST] != hashlib.sha256(list_bytes).hexdigest():
        raise ValueError("dsh_legacy_css_inputs_changed_during_verification")
    return {"contract": "genshin-legacy-css-inputs-v1", "styles": styles, "inputs": inputs}


def _verify_legacy_css(web: Path, manifest: dict, frontend: Path | None) -> None:
    actual = _marker((web / "legacy-island.css").read_bytes(), "GENSHIN_LEGACY_CSS_INPUTS_V1")
    if not isinstance(actual, dict) or actual.get("contract") != "genshin-legacy-css-inputs-v1":
        raise ValueError("dsh_legacy_css_source_identity_invalid")
    styles = _legacy_styles(actual.get("styles"))
    inputs = actual.get("inputs")
    expected_names = {_LEGACY_GENERATOR, _LEGACY_STYLE_LIST, *(_LEGACY_WEB + name for name in styles)}
    if (not isinstance(inputs, dict) or set(inputs) != expected_names
            or any(not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None for digest in inputs.values())):
        raise ValueError("dsh_legacy_css_source_identity_invalid")
    if actual != manifest.get("legacyCssInputs"):
        raise ValueError("dsh_legacy_css_compiled_inputs_mismatch")
    if frontend is not None and actual != legacy_css_inputs(frontend):
        raise ValueError("dsh_legacy_css_source_inputs_mismatch:rebuild_legacy_css")


def source_inputs(frontend: Path, *, production: bool = False) -> dict[str, str]:
    """Keep the complete input set equivalent to build-source-provenance.mjs."""
    paths = set(BASE_SOURCE_FILES | (PRODUCTION_FILES if production else set()))

    def collect(directory: Path) -> None:
        if directory.is_symlink():
            raise ValueError("dsh_production_source_symlink")
        if not directory.exists():
            return
        for path in directory.iterdir():
            if path.is_symlink():
                raise ValueError("dsh_production_source_symlink")
            if path.is_dir() and path.name not in _EXCLUDED_DIRS:
                collect(path)
            elif path.is_file() and path.suffix in _SOURCE_EXTENSIONS and not re.search(r"\.(test|spec)\.", path.name):
                paths.add(path.relative_to(frontend).as_posix())

    collect(frontend / "src")
    collect(frontend / "packages/dsh-client-domain")
    result = {}
    for name in sorted(paths):
        path = frontend / name
        if path.is_symlink():
            raise ValueError("dsh_production_source_symlink")
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _marker(body: bytes, marker: str) -> dict:
    matches = re.findall(rb"/\*! " + marker.encode() + rb":([A-Za-z0-9+/=]+) \*/", body)
    if len(matches) != 1:
        raise ValueError(f"{marker.lower()}_embedded_identity_missing_or_duplicate")
    return json.loads(base64.b64decode(matches[0], validate=True))


def _validate_source_identity(identity: dict, scope: str) -> None:
    if (identity.get("contract") != "genshin-source-inputs-v1" or identity.get("scope") != scope
            or not isinstance(identity.get("inputs"), dict) or not identity["inputs"]):
        raise ValueError(f"dsh_child_source_identity_invalid:{scope}")
    _validate_paths(identity["inputs"], production=False)


def _validate_paths(inputs: dict, *, production: bool) -> None:
    allowed = BASE_SOURCE_FILES | (PRODUCTION_FILES if production else set())
    if not allowed.issubset(inputs):
        raise ValueError("dsh_production_input_set_incomplete")
    for name, digest in inputs.items():
        if (not isinstance(name, str) or "\\" in name or ":" in name
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or not (name in allowed or name.startswith(("src/", "packages/dsh-client-domain/")))
                or not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None):
            raise ValueError("dsh_production_input_path_invalid")


def _verify_child(body: bytes, description: dict, scope: str) -> None:
    if len(body) != description.get("bytes") or hashlib.sha256(body).hexdigest() != description.get("sha256"):
        raise ValueError(f"dsh_production_child_bytes_mismatch:{scope}")
    actual = _marker(body, "GENSHIN_SOURCE_INPUTS_V1")
    _validate_source_identity(actual, scope)
    if actual != description.get("sourceInputs"):
        raise ValueError(f"dsh_production_child_source_inputs_mismatch:{scope}")


def verify_production_web_assets(web: Path, *, frontend: Path | None = None) -> dict:
    web = Path(web)
    manifest = json.loads((web / "dsh-production-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("contract") != "genshin-dsh-production-assets-v1":
        raise ValueError("dsh_production_asset_manifest_invalid")
    assets = manifest.get("assets", [])
    names = [item.get("name") for item in assets]
    required = {"dsh-production.js", "dsh-production.css", "legacy-island.css", "dsh-shell.css", "business-ui.js", "business-ui.js.map", "agent-window.html", "json-transport.js", "index.html", "ui.js", "workspace.js", "app.js", "candidate-projection.js", "plans.js", "app-file-actions.js", "result.js", "gui-foundation-v2.js", "gui-foundation-tokens.css", "gui-foundation-manifest.json"}
    if len(names) != len(set(names)) or set(names) != required:
        raise ValueError("dsh_production_asset_set_invalid")
    for item in assets:
        content = (web / item["name"]).read_bytes()
        if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"dsh_production_asset_bytes_mismatch:{item['name']}")
    _verify_legacy_css(web, manifest, frontend)
    embedded = _marker((web / "dsh-production.js").read_bytes(), "GENSHIN_PRODUCTION_INPUTS_V1")
    if (embedded != manifest.get("buildInputs")
            or embedded.get("contract") != "genshin-production-inputs-v1"
            or embedded.get("acceptedSemanticSha256") != manifest.get("acceptedSemanticSha256")
            or embedded.get("dshRelease") != manifest.get("dshRelease")):
        raise ValueError("dsh_production_compiled_inputs_mismatch")
    _validate_paths(embedded.get("inputs", {}), production=True)
    children = embedded.get("children", {})
    if set(children) != {"business", "domain", "foundation"}:
        raise ValueError("dsh_production_child_set_invalid")
    source_identity = {name: digest for name, digest in embedded["inputs"].items() if name not in PRODUCTION_FILES}
    for scope, child in children.items():
        _validate_source_identity(child.get("sourceInputs", {}), scope)
        if child["sourceInputs"]["inputs"] != source_identity:
            raise ValueError(f"dsh_production_child_source_inputs_mismatch:{scope}")
    if hashlib.sha256((web / "gui-foundation-tokens.css").read_bytes()).hexdigest() != source_identity.get("src/design-system/tokens/tokens.generated.css"):
        raise ValueError("dsh_foundation_tokens_source_mismatch")
    _verify_child((web / "business-ui.js").read_bytes(), children["business"], "business")
    _verify_child((web / "gui-foundation-v2.js").read_bytes(), children["foundation"], "foundation")
    if frontend is not None:
        frontend = Path(frontend)
        expected = source_inputs(frontend, production=True)
        if expected != embedded["inputs"]:
            changed = sorted(set(expected) ^ set(embedded["inputs"]) | {
                name for name in expected.keys() & embedded["inputs"].keys() if expected[name] != embedded["inputs"][name]
            })
            raise ValueError(f"dsh_production_source_inputs_mismatch:{','.join(changed)}")
        child_expected = source_inputs(frontend)
        for scope, child in children.items():
            if child["sourceInputs"]["inputs"] != child_expected:
                raise ValueError(f"dsh_production_child_source_inputs_mismatch:{scope}")
        _verify_child((frontend / "packages/dsh-client-domain/lib/client.js").read_bytes(), children["domain"], "domain")
    return {"contract": "genshin-production-web-verification-v1", "status": "verified",
            "buildInputs": embedded, "assets": assets}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("web", type=Path)
    parser.add_argument("--frontend", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_production_web_assets(args.web, frontend=args.frontend), indent=2))
