const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ROOT=path.resolve(__dirname,'..'),WEB=path.join(ROOT,'src/genshin_app/gui/web'),clone=x=>JSON.parse(JSON.stringify(x));
function harness(file,names){const h={module:{exports:{}},require,__dirname,console};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,file),'utf8').split('\ntest(')[0]+`\nmodule.exports={${names}};`,h);return h.module.exports;}
const helpers=harness('gui_hit_parameters_review.test.cjs','setup,press,control');
const captures=harness('gui_snapshot_captures.test.cjs','setup,fill,button');
const raw=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/mechanism_cast_equipment_closure.json'),'utf8'));
const artifact=raw.sources['ExcelBinOutput/ReliquaryExcelConfigData.json'].records[0].record;
const version={version_line:'release',upstream_commit:raw.commit};
function environment(){
  const h=helpers.setup(),w=h.world,p=h.PL.state.plan;
  for(const file of ['action-events','mechanism-sources'])GUI_TEST_RUNTIME.loadWebFile(w, path.join(WEB,file+'.js'));
  p.mechanism_sources=[];p.equipment_inputs.a={character_ref:{db_character_id:10000063,version_line:'release'},
    data_version:clone(version),weapon:null,artifacts:{[artifact.equipType]:{state:'equipped',artifact_id:artifact.id}}};
  h.PL.state.caps.mechanism_source_contract={compiler:'typed-mechanism-app-v1',limits:{max_sources:16},
    default_source:{id:'draft',actor:'',mechanism_id:'',version_line:'release',revision:null,accepted_fingerprint:null,
      scenario:{target_id:null,values:{},status:'pending',source:''},calibration:null}};
  h.RT.syncRevision();const statuses=[];w.setStatus=v=>statuses.push(v);
  const evidence={a:{artifacts:{[artifact.equipType]:{artifact:{id:artifact.id,equip_type:artifact.equipType,set_id:artifact.setId},data_version:clone(version)}}}};
  const reply={code:0,data:{state:'pending',contribution_report:{equipment_sources:evidence}}};
  return {...h,w,p,evidence,reply,statuses};
}
async function open(h){h.w.MS.edit();const field=helpers.control(h.dialog(),'机制提供者');field.value='a';await field.emit('change');}
const options=h=>helpers.control(h.dialog(),'来源数据库身份').children.map(c=>c.value);

test('independent: complete equipment resolves its set by exact saved artifact identity, never item-ID arithmetic',()=>{
  const h=environment(),sets=e=>clone(h.w.MS.scopes(h.p,'a',e)).filter(s=>s.target_type==='artifact_set');
  assert.deepEqual(sets({}),[]);assert.deepEqual(sets(h.evidence).map(s=>s.target_id),[15007]);
  for(const key of ['id','equip_type','upstream_commit']){const changed=clone(h.evidence),row=changed.a.artifacts[artifact.equipType];
    if(key==='upstream_commit')row.data_version[key]='f'.repeat(40);else row.artifact[key]=key==='id'?artifact.id+1:'wrong-slot';
    assert.deepEqual(sets(changed),[],key);}
});
test('independent: explicit fresh equipment read exposes catalog scope without writing source or server plan',async()=>{
  const h=environment();await open(h);const before=JSON.stringify(h.p);
  assert.ok(!options(h).includes('release:artifact_set:15007'));
  h.w.callApi=async(method,p)=>{assert.equal(method,'compose_equipment_plan');p.manual_template.characters[0].stats.atk=999;
    return{...clone(h.reply),data:{...clone(h.reply.data),plan:p}};};
  await helpers.press(h.dialog(),'读取已装备圣遗物的套装身份');
  assert.ok(options(h).includes('release:artifact_set:15007'));assert.equal(JSON.stringify(h.p),before);
});
for(const change of ['artifact','close','caps'])test(`independent: late equipped-source response ignored after ${change}`,async()=>{
  const h=environment();await open(h);let finish;h.w.callApi=()=>new Promise(r=>finish=()=>r(clone(h.reply)));
  const job=helpers.press(h.dialog(),'读取已装备圣遗物的套装身份');
  if(change==='artifact')h.p.equipment_inputs.a.artifacts[artifact.equipType].artifact_id++;
  if(change==='caps')h.PL.state.caps.mechanism_source_contract.compiler='different-contract';
  if(change==='close')await helpers.press(h.dialog(),'放弃并关闭');
  const before=JSON.stringify(h.p);finish();await job;assert.equal(JSON.stringify(h.p),before);
  assert.ok(!options(h).includes('release:artifact_set:15007'));
});
test('independent: capture editor cannot commit a reply from a replaced capability contract',async()=>{
  const h=captures.setup(),d=captures.fill(h),before=clone(h.p);let finish;
  h.w.callApi=(_,p)=>new Promise(resolve=>finish=()=>resolve({code:0,data:{state:'pending',plan:clone(p)}}));
  const job=captures.button(d,'应用取样草稿').emit('click');
  h.PL.state.caps.mechanics_contract.snapshot_captures.contract='unknown-new-contract';
  finish();await job;assert.deepEqual(clone(h.p),before);
});
