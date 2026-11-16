"""Final-resource acceptance must reject stale browsers independently of source rebuilds."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("web_provenance", Path(__file__).parents[1] / "scripts/verify_production_web_assets.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
verify = _module.verify_production_web_assets


def marker(name, payload):
    return f"/*! {name}:{base64.b64encode(json.dumps(payload).encode()).decode()} */\n".encode()


def reseal_assets(web):
    manifest_path = web / "dsh-production-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for item in manifest["assets"]:
        content = (web / item["name"]).read_bytes()
        item.update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
    manifest_path.write_text(json.dumps(manifest))


@pytest.fixture
def frontend(tmp_path):
    root = tmp_path / "frontend"
    for name in _module.BASE_SOURCE_FILES | _module.PRODUCTION_FILES | {"src/main.ts", "src/design-system/tokens/tokens.generated.css"}:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    repository = Path(__file__).parents[1]
    for name in (_module._LEGACY_STYLE_LIST, _module._LEGACY_GENERATOR):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((repository / name).read_bytes())
    for name in json.loads((tmp_path / _module._LEGACY_STYLE_LIST).read_text()):
        path = tmp_path / _module._LEGACY_WEB / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("a {color:red}")
    return root


@pytest.fixture
def web(tmp_path, frontend):
    web = tmp_path / "web"
    web.mkdir()
    source = _module.source_inputs(frontend)
    children = {}
    bodies = {}
    for scope in ("business", "domain", "foundation"):
        identity = {"contract": "genshin-source-inputs-v1", "scope": scope, "inputs": source}
        bodies[scope] = marker("GENSHIN_SOURCE_INPUTS_V1", identity) + b"void 0;"
        children[scope] = {"bytes": len(bodies[scope]), "sha256": hashlib.sha256(bodies[scope]).hexdigest(), "sourceInputs": identity}
    domain = frontend / "packages/dsh-client-domain/lib/client.js"
    domain.parent.mkdir(parents=True)
    domain.write_bytes(bodies["domain"])
    inputs = {"contract": "genshin-production-inputs-v1", "dshRelease": "test", "acceptedSemanticSha256": "a" * 64,
              "inputs": _module.source_inputs(frontend, production=True), "children": children}
    assets = []
    legacy_identity = _module.legacy_css_inputs(frontend)
    for name in ("dsh-production.js", "dsh-production.css", "legacy-island.css", "dsh-shell.css", "business-ui.js", "business-ui.js.map", "agent-window.html", "json-transport.js", "index.html", "ui.js", "workspace.js", "app.js", "candidate-projection.js", "plans.js", "app-file-actions.js", "result.js", "gui-foundation-v2.js", "gui-foundation-tokens.css", "gui-foundation-manifest.json"):
        body = marker("GENSHIN_PRODUCTION_INPUTS_V1", inputs) + b"void 0;" if name == "dsh-production.js" else bodies["business"] if name == "business-ui.js" else bodies["foundation"] if name == "gui-foundation-v2.js" else b"{}" if name == "gui-foundation-tokens.css" else b"fixture"
        if name == "legacy-island.css":
            body = marker("GENSHIN_LEGACY_CSS_INPUTS_V1", legacy_identity) + b".gs-legacy-island {color:red}"
        (web / name).write_bytes(body)
        assets.append({"name": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
    manifest = {"contract": "genshin-dsh-production-assets-v1", "dshRelease": "test", "acceptedSemanticSha256": "a" * 64, "buildInputs": inputs, "legacyCssInputs": legacy_identity, "assets": assets}
    (web / "dsh-production-manifest.json").write_text(json.dumps(manifest))
    return web


def test_final_copy_accepts_unchanged_generation_and_rejects_same_length_mutation(web, frontend):
    assert verify(web, frontend=frontend)["status"] == "verified"
    script = web / "dsh-production.js"
    script.write_bytes(script.read_bytes().replace(b"void 0", b"void 1"))
    with pytest.raises(ValueError, match="asset_bytes_mismatch"):
        verify(web)


def test_new_sidecar_cannot_make_old_embedded_identity_current(web):
    manifest_path = web / "dsh-production-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["acceptedSemanticSha256"] = "b" * 64
    manifest["buildInputs"]["acceptedSemanticSha256"] = "b" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="compiled_inputs_mismatch"):
        verify(web)


def test_each_packaged_copy_is_checked_without_replacing_it(web, tmp_path):
    import shutil
    destination = tmp_path / "packaged"
    destination.mkdir()
    for path in list(web.iterdir()):
        if path.is_file(): shutil.copy2(path, destination / path.name)
    (destination / "dsh-shell.css").write_text("stale")
    assert verify(web)["status"] == "verified"
    with pytest.raises(ValueError, match="asset_bytes_mismatch:dsh-shell.css"):
        verify(destination)
    assert (destination / "dsh-shell.css").read_text() == "stale"


@pytest.mark.parametrize("name", ["agent-window.html", "json-transport.js", "index.html", "ui.js", "workspace.js", "app.js", "candidate-projection.js", "plans.js"])
def test_detached_entry_and_lossless_wire_are_part_of_final_byte_contract(web, name):
    assert verify(web)["status"] == "verified"
    (web / name).write_bytes(b"changed")
    with pytest.raises(ValueError, match=f"asset_bytes_mismatch:{name}"):
        verify(web)


@pytest.mark.parametrize("name", ["agent-window.html", "json-transport.js", "index.html", "ui.js", "workspace.js", "app.js", "candidate-projection.js", "plans.js"])
def test_detached_entry_and_lossless_wire_cannot_be_omitted_from_manifest(web, name):
    path = web / "dsh-production-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["assets"] = [entry for entry in manifest["assets"] if entry["name"] != name]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="asset_set_invalid"):
        verify(web)


@pytest.mark.parametrize("change", ["added", "removed", "edited", "builder"])
def test_current_source_set_must_match_all_inputs(web, frontend, change):
    if change == "added":
        (frontend / "src/new.ts").write_text("export const newValue = true;")
    elif change == "removed":
        (frontend / "src/main.ts").unlink()
    elif change == "builder":
        (frontend / "vite.business.config.ts").write_text("changed")
    else:
        (frontend / "src/main.ts").write_text("changed")
    with pytest.raises(ValueError, match="source_inputs_mismatch"):
        verify(web, frontend=frontend)


def test_new_test_file_does_not_change_shipped_source_set(web, frontend):
    (frontend / "src/new.test.ts").write_text("test only")
    assert verify(web, frontend=frontend)["status"] == "verified"


def test_business_cannot_be_replaced_and_rehashed_by_new_asset_sidecar(web):
    path = web / "business-ui.js"
    path.write_bytes(path.read_bytes().replace(b"void 0", b"void 1"))
    reseal_assets(web)
    with pytest.raises(ValueError, match="child_bytes_mismatch:business"):
        verify(web)


def test_domain_compiled_copy_must_match_production_generation(web, frontend):
    path = frontend / "packages/dsh-client-domain/lib/client.js"
    path.write_bytes(path.read_bytes().replace(b"void 0", b"void 1"))
    with pytest.raises(ValueError, match="child_bytes_mismatch:domain"):
        verify(web, frontend=frontend)


def test_stale_child_source_cannot_be_blessed_with_fresh_production_inputs(web, frontend):
    (frontend / "src/main.ts").write_text("new source")
    manifest_path = web / "dsh-production-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["buildInputs"]["inputs"] = _module.source_inputs(frontend, production=True)
    (web / "dsh-production.js").write_bytes(marker("GENSHIN_PRODUCTION_INPUTS_V1", manifest["buildInputs"]) + b"void 0;")
    manifest_path.write_text(json.dumps(manifest))
    reseal_assets(web)
    with pytest.raises(ValueError, match="child_source_inputs_mismatch:business"):
        verify(web, frontend=frontend)


def test_node_and_packaging_use_identical_complete_source_inventory(frontend):
    import subprocess
    module_url = (Path(__file__).parents[1] / "frontend/scripts/build-source-provenance.mjs").as_uri()
    production_url = (Path(__file__).parents[1] / "frontend/scripts/production-provenance.mjs").as_uri()
    script = (f"import {{sourceFiles}} from {json.dumps(module_url)};"
              f"import {{PRODUCTION_FILES}} from {json.dumps(production_url)};"
              f"console.log(JSON.stringify(await sourceFiles({json.dumps(str(frontend))},PRODUCTION_FILES)));")
    output = subprocess.check_output(["node", "--input-type=module", "-e", script], text=True)
    assert json.loads(output) == _module.source_inputs(frontend, production=True)


@pytest.mark.parametrize("name", ["app-file-actions.js", "result.js", "gui-foundation-v2.js", "gui-foundation-tokens.css", "gui-foundation-manifest.json"])
def test_modal_and_file_outcome_assets_are_required_and_verified(web, name):
    assert verify(web)["status"] == "verified"
    (web / name).write_bytes(b"stale packaged asset")
    with pytest.raises(ValueError, match=f"asset_bytes_mismatch:{name}"):
        verify(web)


def test_final_package_cannot_reseal_stale_foundation_into_new_sidecar(web):
    path = web / "gui-foundation-v2.js"
    path.write_bytes(path.read_bytes().replace(b"void 0", b"void 1"))
    reseal_assets(web)
    with pytest.raises(ValueError, match="child_bytes_mismatch:foundation"):
        verify(web)


def test_final_package_checks_foundation_source_against_compiled_parent_without_checkout(web):
    manifest_path = web / "dsh-production-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["buildInputs"]["children"]["foundation"]["sourceInputs"]["inputs"]["src/main.ts"] = "b" * 64
    (web / "dsh-production.js").write_bytes(marker("GENSHIN_PRODUCTION_INPUTS_V1", manifest["buildInputs"]) + b"void 0;")
    manifest_path.write_text(json.dumps(manifest))
    reseal_assets(web)
    with pytest.raises(ValueError, match="child_source_inputs_mismatch:foundation"):
        verify(web)


def test_final_package_cannot_reseal_old_foundation_tokens(web):
    (web / "gui-foundation-tokens.css").write_bytes(b"old tokens")
    reseal_assets(web)
    with pytest.raises(ValueError, match="foundation_tokens_source_mismatch"):
        verify(web)


@pytest.mark.parametrize("name", ["whiteboard.css", "workspace.css"])
def test_css_source_edit_without_generation_is_rejected(web, frontend, name):
    path = frontend.parent / _module._LEGACY_WEB / name
    path.write_text("a {color:tan}")
    with pytest.raises(ValueError, match="legacy_css_source_inputs_mismatch"):
        verify(web, frontend=frontend)
    # An immutable packaged copy can still validate its own original generation;
    # only source-mode compares the authoring tree against that generation.
    assert verify(web)["status"] == "verified"


def test_fresh_css_sidecar_cannot_bless_the_old_generated_marker(web, frontend):
    (frontend.parent / _module._LEGACY_WEB / "whiteboard.css").write_text("a {color:tan}")
    path = web / "dsh-production-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["legacyCssInputs"] = _module.legacy_css_inputs(frontend)
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="legacy_css_compiled_inputs_mismatch"):
        verify(web, frontend=frontend)


@pytest.mark.parametrize("change", ["generator", "order", "addition"])
def test_css_generator_and_single_source_list_are_checked(web, frontend, change):
    root = frontend.parent
    if change == "generator":
        path = root / _module._LEGACY_GENERATOR
        path.write_bytes(path.read_bytes() + b"\n// changed\n")
    else:
        path = root / _module._LEGACY_STYLE_LIST
        names = json.loads(path.read_text())
        if change == "order":
            names.reverse()
        else:
            names.append("new-layout.css")
            (root / _module._LEGACY_WEB / names[-1]).write_text("a {color:blue}")
        path.write_text(json.dumps(names))
    with pytest.raises(ValueError, match="legacy_css_source_inputs_mismatch"):
        verify(web, frontend=frontend)


def test_actual_node_css_generation_after_source_edit_is_accepted(web, frontend):
    import subprocess

    (frontend.parent / _module._LEGACY_WEB / "whiteboard.css").write_text("a {color:tan}")
    with pytest.raises(ValueError, match="legacy_css_source_inputs_mismatch"):
        verify(web, frontend=frontend)
    repository = Path(__file__).parents[1]
    module_url = (repository / _module._LEGACY_GENERATOR).as_uri()
    script = (f"import {{buildLegacyCss}} from {json.dumps(module_url)};"
              f"process.stdout.write(await buildLegacyCss({json.dumps(str(frontend.parent))}));")
    body = subprocess.check_output(["node", "--input-type=module", "-e", script])
    (web / "legacy-island.css").write_bytes(body)
    path = web / "dsh-production-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["legacyCssInputs"] = _module._marker(body, "GENSHIN_LEGACY_CSS_INPUTS_V1")
    path.write_text(json.dumps(manifest))
    reseal_assets(web)
    assert verify(web, frontend=frontend)["status"] == "verified"
    assert b"color:tan" in body


@pytest.mark.parametrize("change", ["missing", "duplicate", "traversal", "omit_input"])
def test_css_compiled_identity_is_required_and_cannot_hide_missing_inputs(web, change):
    css = web / "legacy-island.css"
    if change == "missing":
        css.write_bytes(b".gs-legacy-island {color:red}")
    elif change == "duplicate":
        css.write_bytes(css.read_bytes() * 2)
    else:
        path = web / "dsh-production-manifest.json"
        manifest = json.loads(path.read_text())
        identity = manifest["legacyCssInputs"]
        if change == "traversal":
            identity["styles"] = ["../outside.css"]
        else:
            del identity["inputs"][_module._LEGACY_WEB + "whiteboard.css"]
        css.write_bytes(marker("GENSHIN_LEGACY_CSS_INPUTS_V1", identity) + b"a {color:red}")
        path.write_text(json.dumps(manifest))
    reseal_assets(web)
    with pytest.raises(ValueError, match="legacy_css|genshin_legacy_css"):
        verify(web)
