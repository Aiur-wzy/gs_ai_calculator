// Generation preflight only. These fixtures never install dependencies, start
// DSH, call a provider, or relax the final full release integrity checks.
const test=require('node:test'),assert=require('node:assert/strict');
const {mkdtemp,mkdir,writeFile,access,rm}=require('node:fs/promises');
const {spawnSync}=require('node:child_process');
const {resolve,join,sep}=require('node:path');
const {pathToFileURL}=require('node:url');
const root=resolve(__dirname,'..'),base=join(root,'.codex-tmp');
const source="lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies: {}\n";
const helpers=import(pathToFileURL(join(root,'scripts/generate_dsh_client_shell_manifest.mjs')).href);

async function fixture(t){
  await mkdir(base,{recursive:true});const dir=await mkdtemp(join(base,'manifest-generation-'));
  t.after(async()=>{assert.ok(resolve(dir).startsWith(base+sep+'manifest-generation-'));await rm(dir,{recursive:true,force:true});});
  await mkdir(join(dir,'node_modules/.pnpm'),{recursive:true});
  await writeFile(join(dir,'pnpm-lock.yaml'),source);await writeFile(join(dir,'node_modules/.pnpm/lock.yaml'),source);
  return dir;
}

test('matching real raw lock receipts pass the generation preflight',async t=>{
  const dir=await fixture(t),{assertInstalledPnpmLock}=await helpers;
  assert.equal(await assertInstalledPnpmLock(dir),true);
});

test('missing installed receipt is rejected before creating any candidate',async t=>{
  const dir=await fixture(t),{generateCandidateManifest}=await helpers;
  await rm(join(dir,'node_modules/.pnpm/lock.yaml'));
  const output=join(dir,'candidate.json');
  await assert.rejects(generateCandidateManifest([dir,join(dir,'assets'),process.execPath,output]),{code:'dsh_shell_installation_lock_missing'});
  await assert.rejects(access(output));
});

test('importer drift and semantically harmless whitespace both fail exact receipt matching',async t=>{
  const dir=await fixture(t),{generateCandidateManifest}=await helpers,output=join(dir,'candidate.json');
  for(const installed of [source.replace('dependencies: {}','dependencies: {old: 1}'),source+'\n',source.replaceAll('\n','\r\n')]){
    await writeFile(join(dir,'node_modules/.pnpm/lock.yaml'),installed);
    // There is deliberately no src tree. A removed/reordered preflight would
    // fail differently, after starting the more expensive runtime audit.
    await assert.rejects(generateCandidateManifest([dir,join(dir,'assets'),process.execPath,output]),{code:'supply_chain_pnpm_installation_lock_mismatch'});
    await assert.rejects(access(output));
  }
});

test('actual CLI rejects a stale installation before auditing the empty runtime',async t=>{
  const dir=await fixture(t);await writeFile(join(dir,'node_modules/.pnpm/lock.yaml'),source+'# older installation\n');
  const output=join(dir,'candidate.json');
  const result=spawnSync(process.execPath,[join(root,'scripts/generate_dsh_client_shell_manifest.mjs'),dir,join(dir,'assets'),process.execPath,output],{cwd:root,encoding:'utf8',windowsHide:true});
  assert.notEqual(result.status,0);assert.match(result.stderr,/supply_chain_pnpm_installation_lock_mismatch/);
  assert.doesNotMatch(result.stderr,/runtime_integrity_src_missing/);await assert.rejects(access(output));
});

test('oversized receipt is rejected under the same release metadata capacity',async t=>{
  const dir=await fixture(t),{assertInstalledPnpmLock}=await helpers;
  await writeFile(join(dir,'node_modules/.pnpm/lock.yaml'),Buffer.alloc(4*1024*1024+1,32));
  await assert.rejects(assertInstalledPnpmLock(dir),{code:'supply_chain_metadata_too_large'});
});
