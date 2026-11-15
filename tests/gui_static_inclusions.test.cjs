const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),helpers={module:{exports:{}},require,__dirname:path.join(root,'tests'),console};
vm.runInNewContext(fs.readFileSync(path.join(root,'tests/gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup,Element};',helpers);
const {setup:base,Element}=helpers.module.exports;
const all=node=>[node,...node.children.flatMap(all)];
function setup(){const e=base();GUI_TEST_RUNTIME.loadWebFile(e.world, path.join(root,'src/genshin_app/gui/web/static-inclusions.js'));return e;}
function record(){return {id:'static-a',binding_id:'source-a',provider_character_id:'a',status:'ready',source_authority:'fresh_database_admission',compilation_fingerprint:'proof',
  declaration:{delivery:'already_in_given_panel',numeric_application:false,set_id:10007,required_count:2,properties:[{stat:'atk',unit:'ratio',value:.18,source_index:0}]},source_display:{name:'常驻来源',description:'攻击力提高。'},issues:[]};}
test('ratio becomes readable percent without mutating saved ratio or adding controls',()=>{
  const e=setup(),box=new Element('div'),r=record(),saved=JSON.stringify(r);
  e.world.SI.renderResult(box,{static_inclusion_compilations:[r]},e.PL.state.plan.manual_template);
  assert.match(box.textContent,/攻击力：18%（已含）/);assert.match(box.textContent,/本次不重复加算/);
  assert.equal(JSON.stringify(r),saved);assert.equal(all(box).filter(n=>['input','button'].includes(n.tagName)).length,0);
});
test('shape-only or pending declaration cannot masquerade as automatic admission',()=>{
  for(const change of [{status:'shape_ready'},{status:'pending'},{source_authority:'not_granted'},{declaration:{numeric_application:true}}]){
    const e=setup(),box=new Element('div'),r={...record(),...change};
    e.world.SI.renderResult(box,{static_inclusion_compilations:[r]},e.PL.state.plan.manual_template);
    assert.match(box.textContent,/0 项已采用/);assert.match(box.textContent,/尚未自动采用/);assert.doesNotMatch(box.textContent,/本次不重复加算/);
  }
});
test('binary source ratio displays a readable percent and retains its exact stored value',()=>{
  const e=setup(),box=new Element('div'),r=record();r.declaration.properties[0].value=.18000000715255737;
  e.world.SI.renderRecord(box,r,e.PL.state.plan.manual_template);
  assert.match(box.textContent,/攻击力：18%（已含）/);assert.equal(r.declaration.properties[0].value,.18000000715255737);
});
test('provider records remain separate and no duration bar is fabricated',()=>{
  const e=setup(),box=new Element('div'),a=record(),b={...record(),id:'static-b',provider_character_id:'b'};
  e.world.SI.renderResult(box,{static_inclusion_compilations:[a,b]},e.PL.state.plan.manual_template);
  assert.match(box.textContent,/2 项已采用/);assert.match(box.textContent,/常驻属性没有独立触发时点或持续区间/);
});
test('equipment requirement uses exact adoption identity and keeps orphaned records unadopted',()=>{
  const e=setup(),r=record(),req={binding_id:r.binding_id,provider_character_id:r.provider_character_id,resolved_by:{kind:'static_inclusion',id:r.id,compilation_fingerprint:'proof'}},report={static_inclusion_compilations:[r]};
  assert.equal(e.world.SI.forRequirement(report,req),r);
  for(const bad of [{...req,resolved_by:undefined},{...req,resolved_by:{...req.resolved_by,compilation_fingerprint:'old'}},{...req,provider_character_id:'b'},{...req,binding_id:'other'}])assert.equal(e.world.SI.forRequirement(report,bad),null);
  assert.equal(e.world.SI.forRequirement({static_inclusion_compilations:[r,r]},req),null);
});
