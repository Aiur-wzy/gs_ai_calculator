const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
// Standalone, opt-in read-only audit; does not run the calculator or a model.
const {createRequire}=require('node:module');
const root=path.resolve(__dirname,'..'),out=path.join(root,'build/r20-next-implementation-20261116/timeline');
process.chdir(root);fs.mkdirSync(out,{recursive:true});
const fromFrontend=createRequire(path.join(root,'frontend/package.json')),fromVite=createRequire(fromFrontend.resolve('vite'));
fromVite('esbuild').buildSync({stdin:{contents:'export * from "./frontend/src/business/timeline/index.ts"; export * from "./frontend/src/business/timeline/display-rows.ts"; export * from "./frontend/src/business/timeline/evidence-index.ts";',resolveDir:root,loader:'ts'},bundle:true,platform:'node',format:'cjs',outfile:path.join(out,'toolkit.cjs')});
const {buildTimelineProjection,groupCoverage,buildDisplayRows,foldSourceDirectories,packDisplayItems,evidenceIndex,modifierEvidence}=require(path.join(out,'toolkit.cjs'));
const paths=[
 'benchmarks/followup/stellar-r18-codex-main-sol61-high-two-round-20261113/delivery',
 'benchmarks/followup/stellar-dsh-cli-review-and-sol61-120s-20261110/sol61-high-120s/delivery-final-gen17',
 'benchmarks/followup/lunar-bloom-r18-sol61-high-120s-20261114/delivery',
];
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const measure=fn=>{const start=performance.now(),value=fn();return {value,ms:performance.now()-start};};
const generations=['generation-95819797d9f84fc09cde085b3cd9c577','generation-04477118cbcf4fd4822316a221d79034','generation-ddc27b3ef40d472e92b02ed1661268da'];
const report=[];
for(const folder of paths){
 const index=JSON.parse(fs.readFileSync(path.join(folder,'index.json'),'utf8')),item=index.artifacts.find(a=>a.role==='calculated');
 assert.equal(index.generation,generations[report.length],'sample generation changed; review the fixture explicitly');
 const bytes=fs.readFileSync(path.join(folder,item.path));assert.equal(bytes.length,item.bytes);assert.equal(hash(bytes),item.sha256);
 const load=measure(()=>JSON.parse(bytes)),calculated=load.value.calculated||load.value;
 const before=hash(JSON.stringify(calculated));
 const project=measure(()=>buildTimelineProjection({plan:calculated.plan,template:calculated.plan.resolved_template||calculated.plan.manual_template,
   result:calculated.result,executionModel:calculated.report?.mechanics_model,executionTemplate:calculated.report?.mechanics_input_template,current:true}));
 const p=project.value,effects=p.views.effects.events.filter(e=>e.kind!=='calculation_sampling');
 // Read-only old default layout oracle (same input, same visibility).
 const old=measure(()=>{const grouped=groupCoverage(effects,p.characters),rows=new Map();
   for(const e of grouped){const source=e.sourceKey||e.character||'global',type=e.kind==='coverage'?'coverage':'points',base=JSON.stringify([source,type]);
     if(!rows.has(base))rows.set(base,[]);const slots=rows.get(base);if(type==='points'){if(!slots.length)slots.push(0);continue;}
     const slot=slots.findIndex(end=>end<=e.start);if(slot<0)slots.push(e.end??e.start);else slots[slot]=e.end??e.start;}
   return [...rows.values()].reduce((sum,row)=>sum+row.length,0);});
 const rows=measure(()=>buildDisplayRows(effects,'effects'));
 const directories=measure(()=>foldSourceDirectories(rows.value));
 const opened=foldSourceDirectories(rows.value,new Set(directories.value.filter(r=>r.directory).map(r=>r.key)));
 assert.equal(directories.value.reduce((n,r)=>n+r.events.length,0),effects.length);
 assert.equal(opened.reduce((n,r)=>n+r.events.length,0),effects.length);
 const pack=measure(()=>rows.value.map(row=>packDisplayItems(row.events,800/p.duration,'effects')));
 assert.equal(rows.value.reduce((n,r)=>n+r.events.length,0),effects.length);
 for(let i=0;i<rows.value.length;i++){const members=new Set(pack.value[i].flatMap(it=>it.events.map(e=>e.id)));
   for(const e of rows.value[i].events)assert(members.has(e.id));}
 const expanded=measure(()=>buildDisplayRows(effects,'effects',new Set(rows.value.map(r=>r.key))));
 const idx=measure(()=>evidenceIndex(p));const hit=p.views.damage.events.find(e=>e.detail?.explanation_ref),record=hit&&p.explanationDocument?.events[hit.detail.explanation_ref];
 const links=measure(()=>record?(record.modifier_refs||[]).map(id=>modifierEvidence(p,p.explanationDocument.modifiers[id],{character:hit.character})):[]);
 assert.equal(hash(JSON.stringify(calculated)),before);
 const hot=measure(()=>{for(let n=0;n<20;n++)evidenceIndex(p);});
 const result={folder,generation:index.generation,bytes:bytes.length,sha256:hash(bytes),parse_ms:load.ms,projection_ms:project.ms,
  original_effect_records:effects.length,legacy_default_rows:old.value,legacy_layout_ms:old.ms,new_default_rows:rows.value.length,new_layout_ms:rows.ms,
  default_directory_rows:directories.value.length,directory_layout_ms:directories.ms,directory_groups:directories.value.filter(r=>r.directory).length,expanded_rows:expanded.value.length,pack_ms:pack.ms,packed_intervals:pack.value.flat().length,evidence_index_ms:idx.ms,
  evidence_20_cached_reads_ms:hot.ms,one_hit_source_links_ms:links.ms,damage_events:p.views.damage.events.filter(e=>e.kind==='damage').length,
  total_damage:calculated.result.summary?.total_damage,dps:calculated.result.summary?.dps,input_and_result_unchanged:true};
 report.push(result);console.log(JSON.stringify(result));
}
fs.writeFileSync(path.join(out,'replay-results.json'),JSON.stringify({contract:'timeline-readonly-replay-audit-v1',recorded_at:new Date().toISOString(),samples:report},null,2)+'\n');
