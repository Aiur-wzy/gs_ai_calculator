import assert from "node:assert/strict";
import {mkdtemp, mkdir, readFile, writeFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, dirname, resolve} from "node:path";
import {test} from "node:test";
import {createRequire} from "node:module";
import {CHILD_PATHS, PRODUCTION_FILES, inputBanner, productionInputs, productionProvenancePlugin, readEmbeddedInputs, verifyEmbeddedInputs} from "./production-provenance.mjs";
import {BASE_SOURCE_FILES, sourceInputs, sourceBanner, sourceProvenancePlugin, verifySourceInputs} from "./build-source-provenance.mjs";

async function write(path, value) { await mkdir(dirname(path), {recursive:true}); await writeFile(path,value); }
async function rebuildChildren(root) {
  for (const [scope,path] of Object.entries(CHILD_PATHS)) await write(resolve(root,path), `${sourceBanner(await sourceInputs(root,scope))}\nwindow.${scope}=true;`);
}

async function fixture(run) {
  const temporary = await mkdtemp(join(tmpdir(), "genshin-production-provenance-"));
  const root = join(temporary, "frontend");
  try {
    for(const path of [...BASE_SOURCE_FILES, ...PRODUCTION_FILES]) await write(join(root,path), "{}");
    await write(join(root, "dsh-client-accepted-manifest.json"), JSON.stringify({semanticSha256: "a".repeat(64), dshRelease: "test"}));
    await write(join(root,"src/main.ts"), "export const answer = 42;\n");
    await write(join(root,"src/design-system/tokens/tokens.generated.css"), ":root{--token:1}");
    await write(resolve(root,"../src/genshin_app/gui/web/gui-foundation-tokens.css"), ":root{--token:1}");
    await rebuildChildren(root);
    await run(root);
  } finally {
    assert.equal(dirname(temporary), tmpdir());
    assert.ok(temporary.startsWith(join(tmpdir(), "genshin-production-provenance-")));
    await rm(temporary, {recursive: true, force: true});
  }
}

test("sync rejects an old browser even when a fresh sidecar would advertise the current manifest", () => fixture(async root => {
  const before = await productionInputs(root);
  const browser = Buffer.from(`${inputBanner(before)}\nwindow.realEntry = true;`);
  verifyEmbeddedInputs(browser, before);
  await writeFile(join(root, "dsh-client-accepted-manifest.json"), JSON.stringify({semanticSha256: "b".repeat(64), dshRelease: "test"}));
  assert.throws(() => verifyEmbeddedInputs(browser, {...before, acceptedSemanticSha256: "b".repeat(64)}), /compiled_inputs_mismatch/);
  const after = await productionInputs(root);
  assert.equal(before.inputs["dsh-client-accepted-manifest.json"].length, after.inputs["dsh-client-accepted-manifest.json"].length);
  assert.throws(() => verifyEmbeddedInputs(browser, after), /compiled_inputs_mismatch/);
}));

test("compiler rejects concurrent input mutation and stamps the actual successful generation", () => fixture(async root => {
  const plugin = productionProvenancePlugin(root);
  await plugin.buildStart();
  await writeFile(join(root, "dsh-client-accepted-manifest.json"), JSON.stringify({semanticSha256:"b".repeat(64),dshRelease:"test"}));
  const bundle = {entry: {type: "chunk", isEntry: true, code: "void 0;"}};
  await assert.rejects(plugin.generateBundle({}, bundle), /inputs_changed_during_build/);
  await plugin.buildStart();
  const options = plugin.outputOptions({banner:"/* preexisting */"});
  const banner = await options.banner(bundle.entry);
  await plugin.generateBundle({}, bundle);
  assert.equal(bundle.entry.code,"void 0;");
  assert.ok(banner.endsWith("/* preexisting */"));
  assert.deepEqual(readEmbeddedInputs(Buffer.from(banner)), await productionInputs(root));
}));

test("missing and duplicate compiled identity cannot be replaced by sidecar labels", () => {
  assert.throws(() => readEmbeddedInputs(Buffer.from("void 0;")), /missing_or_duplicate/);
  const identity = inputBanner({});
  assert.throws(() => readEmbeddedInputs(Buffer.from(identity + identity)), /missing_or_duplicate/);
});

for(const stale of ["business","domain","foundation"]) test(`fresh production build rejects stale ${stale} child after source changes`, () => fixture(async root => {
  const old = await readFile(resolve(root,CHILD_PATHS[stale]));
  await write(join(root,"src/main.ts"),"export const answer = 43;\n");
  await rebuildChildren(root);
  await write(resolve(root,CHILD_PATHS[stale]),old);
  await assert.rejects(productionInputs(root),new RegExp(`child_source_inputs_mismatch:${stale}`));
}));

test("new source files and builder changes invalidate children, test files do not", () => fixture(async root => {
  const before=await productionInputs(root);
  await write(join(root,"src/ignored.test.ts"),"test only");
  assert.deepEqual(await productionInputs(root),before);
  await write(join(root,"src/new-module.ts"),"export const added = 1;");
  await assert.rejects(productionInputs(root),/child_source_inputs_mismatch/);
  await rebuildChildren(root);
  const after=await productionInputs(root);
  assert.notDeepEqual(after.inputs,before.inputs);
  await write(join(root,"vite.business.config.ts"),"changed build config");
  await assert.rejects(productionInputs(root),/child_source_inputs_mismatch/);
}));

test("Rollup banner preserves authored mapping lines and existing domain wrapper", () => fixture(async root => {
  const require=createRequire(import.meta.url), {rollup}=createRequire(require.resolve("vite/package.json"))("rollup");
  const build=await rollup({input:join(root,"src/main.ts"),plugins:[sourceProvenancePlugin(root,"business")]});
  try {
    const result=await build.generate({format:"es",sourcemap:true,banner:"/* wrapper */"});
    const chunk=result.output.find(item=>item.type==="chunk");
    await verifySourceInputs(root,"business",Buffer.from(chunk.code));
    assert.ok(chunk.code.split("\n")[1].includes("/* wrapper */"));
    const generatedLine=chunk.code.split("\n").findIndex(line=>line.includes("const answer = 42"));
    const mappings=chunk.map.mappings.split(";");
    assert.ok(generatedLine>=2);
    assert.ok(mappings.slice(0,generatedLine).every(line=>line===""));
    assert.notEqual(mappings[generatedLine],"");
    assert.deepEqual(chunk.map.sourcesContent,["export const answer = 42;\n"]);
  } finally { await build.close(); }
}));

test("fresh browser rejects stale Foundation tokens before copying production", () => fixture(async root => {
  await write(resolve(root,"../src/genshin_app/gui/web/gui-foundation-tokens.css"), ":root{--token:old}");
  await assert.rejects(productionInputs(root),/foundation_tokens_source_mismatch/);
}));
