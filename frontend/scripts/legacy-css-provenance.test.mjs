import assert from "node:assert/strict";
import {mkdir, mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {dirname, join, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import {test} from "node:test";
import {buildLegacyCss, LEGACY_STYLES, legacyCssInputs, readLegacyCssInputs, verifyLegacyCssInputs} from "./build-legacy-island-css.mjs";

const repository = fileURLToPath(new URL("../../", import.meta.url));
const list = "frontend/scripts/legacy-styles.json", generator = "frontend/scripts/build-legacy-island-css.mjs";
const web = "src/genshin_app/gui/web";
async function write(root, name, bytes) { const path=resolve(root,name); await mkdir(dirname(path),{recursive:true}); await writeFile(path,bytes); }
async function fixture(run) {
  const root=await mkdtemp(join(tmpdir(),"genshin-legacy-css-inputs-"));
  try {
    for(const name of [list,generator]) await write(root,name,await readFile(resolve(repository,name)));
    for(const name of LEGACY_STYLES) await write(root,`${web}/${name}`,"a { color:red; }\n");
    await run(root);
  } finally {
    assert.equal(dirname(root),tmpdir()); assert.ok(root.startsWith(join(tmpdir(),"genshin-legacy-css-inputs-")));
    await rm(root,{recursive:true,force:true});
  }
}

test("source-only CSS edit rejects the old compiled island; actual generation repairs it",()=>fixture(async root=>{
  const before=Buffer.from(await buildLegacyCss(root));
  assert.deepEqual(await verifyLegacyCssInputs(root,before),await legacyCssInputs(root));
  await write(root,`${web}/whiteboard.css`,"a { color:tan; }\n");
  await assert.rejects(verifyLegacyCssInputs(root,before),/legacy_css_source_inputs_mismatch/);
  const after=Buffer.from(await buildLegacyCss(root));
  await verifyLegacyCssInputs(root,after);
  assert.notDeepEqual(after,before);
  assert.match(after.toString(),/color:tan/);
}));

test("one declared list controls additions and order, rather than a verifier-maintained CSS copy",()=>fixture(async root=>{
  const old=Buffer.from(await buildLegacyCss(root));
  const styles=[...LEGACY_STYLES].reverse(); styles.push("new-layout.css");
  await write(root,`${web}/new-layout.css`,"button { color:blue; }");
  await write(root,list,JSON.stringify(styles));
  await assert.rejects(verifyLegacyCssInputs(root,old),/source_inputs_mismatch/);
  const fresh=Buffer.from(await buildLegacyCss(root));
  assert.deepEqual((await verifyLegacyCssInputs(root,fresh)).styles,styles);
  assert.match(fresh.toString(),/Scoped from new-layout.css/);
}));

test("changing the generator invalidates the previously generated source identity",()=>fixture(async root=>{
  const old=Buffer.from(await buildLegacyCss(root));
  await write(root,generator,Buffer.concat([await readFile(resolve(root,generator)),Buffer.from("\n// changed generator\n")]));
  await assert.rejects(verifyLegacyCssInputs(root,old),/source_inputs_mismatch/);
  const fresh=Buffer.from(await buildLegacyCss(root));
  await verifyLegacyCssInputs(root,fresh);
}));

test("missing or duplicate compiled identity cannot be supplied by a fresh sidecar",()=>fixture(async root=>{
  assert.throws(()=>readLegacyCssInputs(Buffer.from("a {color:red}")),/identity_missing_or_duplicate/);
  const css=Buffer.from(await buildLegacyCss(root));
  assert.throws(()=>readLegacyCssInputs(Buffer.concat([css,css])),/identity_missing_or_duplicate/);
  const before=readLegacyCssInputs(css);
  await write(root,`${web}/workspace.css`,"a {color:blue}");
  assert.notDeepEqual(before,await legacyCssInputs(root));
  await assert.rejects(verifyLegacyCssInputs(root,css),/source_inputs_mismatch/);
}));

test("source list rejects duplicate entries and path escapes",()=>fixture(async root=>{
  for(const value of [["style.css","style.css"],["../secret.css"],[]]) {
    await write(root,list,JSON.stringify(value));
    await assert.rejects(buildLegacyCss(root),/source_list_invalid/);
  }
}));
