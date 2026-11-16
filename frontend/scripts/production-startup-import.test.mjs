/** Real production index/DSH/editor -> real Python GuiApi startup-file import.
 * No native window or App EXE, model prompt, saved credential or external HTTP.
 * An optional immutable r10 app.js provides the independently repeatable negative.
 * GENSHIN_STARTUP_NATIVE_PLAN selects an actual native input; unset keeps the r10 fixture.
 */
import assert from "node:assert/strict";
import {test} from "node:test";
import {spawn, execFileSync} from "node:child_process";
import {createInterface} from "node:readline";
import {createHash, randomUUID, webcrypto} from "node:crypto";
import {readFile, writeFile, mkdir, mkdtemp} from "node:fs/promises";
import {join, resolve, sep, basename} from "node:path";
import {fileURLToPath} from "node:url";
import {JSDOM, VirtualConsole, ResourceLoader} from "jsdom";
import {pinnedTestNode} from "./pinned-test-node.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const web = resolve(process.env.GENSHIN_PRODUCTION_WEB_DIR || join(root, "src/genshin_app/gui/web"));
const node = pinnedTestNode();
const oldApp = process.env.GENSHIN_STARTUP_BASELINE_APP;
const baseline = Boolean(oldApp);
const reportRoot = resolve(process.env.GENSHIN_STARTUP_REPORT_ROOT || join(root, "build/r10-startup-import-diagnosis"));
const nativePath = resolve(process.env.GENSHIN_STARTUP_NATIVE_PLAN || join(root, "benchmarks/followup/r10-stellar-sol61-high-20261107/outputs/delivery-r9/generation-c74a7afacfc5401681bf68d345a10227/native.plan.json"));
const nativeInput = JSON.parse(await readFile(nativePath, "utf8"));
assert.equal(nativeInput.kind, "genshin-plan", "The selected startup fixture must be a native plan");
const expectedNative = {
  plan_id: nativeInput.plan_id,
  character_count: nativeInput.rotation_model.characters.length,
  action_ids: nativeInput.rotation_model.actions.map(action => action.id),
};
assert.ok(typeof expectedNative.plan_id === "string" && expectedNative.plan_id.length > 0, "The selected native plan needs its actual identity");
const actualDb = join(root, "data/genshin.db");
const timeout = 360_000;
const hash = async path => createHash("sha256").update(await readFile(path)).digest("hex");
async function until(check, message) {
  const end = Date.now() + timeout;
  while (Date.now() <= end) {
    if (check()) return;
    await new Promise(done => setTimeout(done, 10));
  }
  throw new Error(typeof message === "function" ? message() : message);
}
function bridgeProcess(args) {
  const child = spawn("python", ["-m", "tests.dsh_gui_projection_host", ...args], {
    cwd: root, windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
    env: {...process.env, PYTHONPATH: join(root, "src") + (process.platform === "win32" ? ";" : ":") + root,
      PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8", GENSHIN_NATIVE_NODE: node,
      DEEPSEEK_API_KEY: "", OPENAI_API_KEY: ""},
  });
  const pending = new Map(), calls = [];
  let stderr = "", closed = false;
  child.stderr.on("data", bytes => { stderr += String(bytes); });
  const exited = new Promise(done => child.once("exit", code => {
    closed = true;
    for (const row of pending.values()) row.reject(new Error(`Python GUI bridge exited ${code}: ${stderr.slice(-1600)}`));
    pending.clear(); done(code);
  }));
  createInterface({input: child.stdout}).on("line", line => {
    let row;
    try { row = JSON.parse(line); } catch { stderr += `\nUnexpected bridge output: ${line}`; return; }
    const entry = pending.get(row.id);
    if (!entry) return;
    pending.delete(row.id);
    entry.call.ok = row.ok;
    entry.call.code = row.ok ? row.value?.code ?? null : row.code;
    if (row.ok) entry.resolve(row.value); else entry.reject(new Error(row.code));
  });
  return {
    calls, get stderr() { return stderr; },
    request(method, ...args) {
      return new Promise((done, reject) => {
        if (closed) { reject(new Error("Python GUI bridge closed")); return; }
        const id = randomUUID(), call = {method, domain_method: method === "document_json_call" ? args[0] : null};
        calls.push(call); pending.set(id, {resolve: done, reject, call});
        child.stdin.write(JSON.stringify({id, method, args}) + "\n");
      });
    },
    async close() {
      if (!closed) { await this.request("fixture_shutdown"); child.stdin.end(); }
      await exited;
    },
  };
}
class LocalAssets extends ResourceLoader {
  fetch(url) {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.hostname !== "genshin-shell.invalid") throw new Error("External browser resource blocked");
    const path = resolve(web, decodeURIComponent(parsed.pathname).slice(1));
    if (!path.startsWith(resolve(web) + sep)) throw new Error("Browser resource escaped production root");
    return readFile(oldApp && basename(path) === "app.js" ? resolve(oldApp) : path);
  }
}
async function mount(bridge, errors, events) {
  const console = new VirtualConsole();
  console.on("jsdomError", error => errors.push(error));
  console.on("error", (...values) => errors.push(new Error(values.map(value => value?.stack || String(value)).join(" "))));
  const dom = new JSDOM(await readFile(join(web, "index.html"), "utf8"), {
    url: "https://genshin-shell.invalid/index.html", resources: new LocalAssets(),
    runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: console,
    beforeParse(w) {
      Object.assign(w, {TextEncoder, TextDecoder, Request, Response,
        ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
        matchMedia: () => ({matches: false, addEventListener() {}, removeEventListener() {}})});
      Object.defineProperty(w.crypto, "subtle", {value: webcrypto.subtle});
      Object.defineProperty(w.document, "fonts", {value: {addEventListener() {}, removeEventListener() {}, ready: Promise.resolve()}});
      w.Range.prototype.getBoundingClientRect = () => ({x: 0, y: 0, left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0});
      w.Range.prototype.getClientRects = () => [];
      w.HTMLElement.prototype.scrollIntoView = () => {};
      w.HTMLCanvasElement.prototype.getContext = () => null;
      w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
      w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event("close")); };
      for (const event of ["genshin-open-editor", "genshin-editor-visible"])
        w.document.addEventListener(event, row => events.push({type: event, page: row.detail?.page}));
      w.pywebview = {api: new Proxy({}, {get(_target, method) {
        if (typeof method !== "string" || method === "then") return undefined;
        return async (...args) => w.JSON.parse(JSON.stringify(await bridge.request(method, ...JSON.parse(JSON.stringify(args)))));
      }})};
    },
  });
  await until(() => dom.window.GenshinDshClient && dom.window.callApi, "Production scripts did not load");
  dom.window.dispatchEvent(new dom.window.Event("pywebviewready"));
  await until(() => dom.window.GenshinEditorBridge, () => `Business initialization failed: ${errors.map(row => row.message).join("\n")}\n${bridge.stderr}`);
  await dom.window.GenshinDshClient.ready;
  return dom;
}

for (const kind of ["native", "whiteboard", "none"]) {
  test(`production startup ${kind}: ${baseline ? "reproduces r10 hidden import" : "reveals the real imported editor"}`, {timeout: timeout + 60_000}, async () => {
    await mkdir(reportRoot, {recursive: true});
    const temp = await mkdtemp(join(reportRoot, `joint-${baseline ? "baseline" : "fixed"}-${kind}-`));
    const setup = JSON.parse(execFileSync("python", ["-m", "tests.dsh_gui_projection_setup", temp], {
      cwd: root, encoding: "utf8", windowsHide: true,
      env: {...process.env, PYTHONPATH: join(root, "src") + (process.platform === "win32" ? ";" : ":") + root},
    }));
    const input = kind === "native" ? nativePath : kind === "whiteboard" ? setup.input : null;
    const database = kind === "native" ? actualDb : setup.database;
    const before = {input: input && await hash(input), database: await hash(database)};
    const args = ["--root", join(temp, "runtime"), "--db", database, "--script", setup.script,
      ...(input ? ["--startup", input] : [])];
    const bridge = bridgeProcess(args), errors = [], events = [];
    let dom, passed = false, summary;
    try {
      dom = await mount(bridge, errors, events);
      const w = dom.window, editor = w.document.getElementById("genshin-legacy-editor");
      if (kind === "native") await until(() => w.PL.state.path === input || w.RT.state.path === input, "Real native startup input was not loaded");
      if (kind === "whiteboard") await until(() => w.WB.state.path === input, "Real whiteboard startup input was not loaded");
      if (kind !== "none" && !baseline) await until(() => !editor.hidden && editor.closest("#dsh-app-root"), "Imported input is still hidden/parked outside the actual DSH workspace");
      await new Promise(done => setTimeout(done, 40));
      if (kind === "native") {
        assert.equal(w.PL.state.plan.kind, "genshin-plan");
        assert.equal(w.PL.state.plan.plan_id, expectedNative.plan_id);
        assert.equal(w.PL.state.plan.rotation_model.characters.length, expectedNative.character_count);
        assert.deepEqual(Array.from(w.PL.state.plan.rotation_model.actions, action => action.id), expectedNative.action_ids);
        assert.equal(w.RS.state.result, null, "Startup import must not silently recalculate");
      }
      if (kind === "whiteboard") {
        assert.equal(w.WB.state.board.team.length, 1);
        assert.equal(w.WB.state.board.actions.length, 1);
        assert.equal(w.WB.state.result, null);
      }
      const expectedPage = kind === "native" ? "rotation" : "whiteboard";
      if (kind === "none") {
        assert.equal(w.WB.state.board, null); assert.equal(w.PL.state.plan, null);
        assert.equal(editor.hidden, true); assert.equal(events.length, 0);
      } else if (baseline) {
        assert.equal(editor.hidden, true, "Immutable r10 reproducer unexpectedly became visible");
        assert.equal(editor.parentElement.id, "genshin-editor-parking");
        assert.equal(events.length, 0, "r10 old-tab import must demonstrate the missing DSH navigation");
      } else {
        assert.equal(editor.hidden, false); assert.ok(editor.closest("#dsh-app-root"));
        assert.equal(w.WS.tab(), expectedPage);
        assert.ok(events.some(row => row.type === "genshin-open-editor" && row.page === expectedPage));
        assert.ok(events.some(row => row.type === "genshin-editor-visible" && row.page === expectedPage));
      }
      // Repeated readiness must neither consume nor reload the startup input.
      w.dispatchEvent(new w.Event("pywebviewready"));
      await new Promise(done => setTimeout(done, 20));
      assert.equal(bridge.calls.filter(row => row.method === "get_startup_file").length, 1);
      const methods = bridge.calls.map(row => row.domain_method || row.method);
      assert.equal(methods.filter(row => /^(new_|save_|.*calc|.*compose)/.test(row)).length, 0, "Opening must not create, save or calculate");
      assert.equal(methods.filter(row => /^dsh_client_(credential_save|credential_delete|bind_document)$/.test(row)).length, 0);
      assert.equal(errors.filter(row => /Uncaught|slot entry crashed/.test(row.message)).length, 0, errors.map(row => row.message).join("\n"));
      assert.deepEqual({input: input && await hash(input), database: await hash(database)}, before);
      summary = {kind, mode: baseline ? "immutable_r10_counterexample" : "fixed_production_projection", passed: true,
        ...(kind === "native" ? {expected_native: expectedNative, input_bytes: (await readFile(input)).length} : {}),
        input, database, sha256: before, editor_hidden: editor.hidden, editor_parent: editor.parentElement.className,
        selected_page: w.WS.tab(), events, startup_claims: 1, GUI_started: false, model_prompted: false,
        real_GuiApi: true, real_DSH_production_bundle: true, application_script: oldApp || join(web, "app.js"),
        application_script_sha256: await hash(oldApp || join(web, "app.js")), methods};
      passed = true;
    } finally {
      if (dom) { await dom.window.GenshinDshClient?.dispose(); dom.window.close(); }
      await bridge.close();
      await writeFile(join(temp, "report.json"), JSON.stringify(summary || {kind, passed, errors: errors.map(row => row.message), events, bridge_stderr: bridge.stderr, calls: bridge.calls}, null, 2) + "\n");
      console.info(`Startup ${kind} ${baseline ? "counterexample" : "projection"} evidence: ${join(temp, "report.json")}`);
    }
  });
}
