const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {spawn}=require('node:child_process'),readline=require('node:readline');
const ROOT=path.resolve(__dirname,'..'),WEB=path.join(ROOT,'src/genshin_app/gui/web');
const clone=value=>JSON.parse(JSON.stringify(value));
const helpers={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0]+
  '\nmodule.exports={setup,press,control,Element};',helpers);
const {setup,press,control,Element}=helpers.module.exports;
const descendants=node=>[node,...node.children.flatMap(descendants)];
const visibleText=node=>node.tagName==='details'?'':(node.text||'')+node.children.map(visibleText).join(' ');
let bridge,initial,preview;

class RealBridge {
  constructor(){
    this.pending=new Map();this.serial=0;this.stderr='';
    this.directory=path.join(ROOT,'outputs/engine-continuation-20261002',`ec20-gui-review-${Date.now()}-${process.pid}`);
    this.process=spawn(process.env.GENSHIN_TEST_PYTHON||'python',
      ['-X','utf8',path.join(__dirname,'mechanism_gui_review_bridge.py'),this.directory],{cwd:ROOT,windowsHide:true,stdio:['pipe','pipe','pipe']});
    this.process.stderr.setEncoding('utf8');this.process.stderr.on('data',data=>this.stderr+=data);
    readline.createInterface({input:this.process.stdout}).on('line',line=>{
      const response=JSON.parse(line),pending=this.pending.get(response.id);
      if(pending){this.pending.delete(response.id);clearTimeout(pending.timer);pending.resolve(response);}
    });
    this.process.on('error',error=>{for(const item of this.pending.values()){clearTimeout(item.timer);item.reject(error);}this.pending.clear();});
    this.process.on('exit',code=>{for(const item of this.pending.values()){clearTimeout(item.timer);item.reject(new Error(`Bridge exited ${code}: ${this.stderr}`));}this.pending.clear();});
  }
  request(operation,fields={}){
    const id=++this.serial;
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error(`Bridge timeout: ${operation}`));},60000);
      this.pending.set(id,{resolve,reject,timer});this.process.stdin.write(JSON.stringify({id,operation,...clone(fields)})+'\n');
    });
  }
  async data(operation,fields={}){const response=await this.request(operation,fields);assert.equal(response.code,0,response.message);return response.data;}
  close(){this.process.stdin.end();}
}

test.before(async()=>{
  bridge=new RealBridge();initial=await bridge.data('init');preview=await bridge.data('compose',{plan:initial.plan});
  assert.equal(initial.profiles['skill.current-atk-limited-addition'].status,'ready');
  assert.equal(initial.profiles['passive.swirl-elemental-bonus'].status,'ready');
  assert.equal(preview.mechanism_compilations.length,2);
});
test.after(()=>bridge?.close());

function environment(plan=initial.plan){
  const env=setup(),loaded=new Set(['display','rotation','panel','chars','timeline-view','timeline','result','format','data','ui','plans',
    'equipment-data','equipment','effect-editor','checks','mechanics','app']);
  const html=fs.readFileSync(path.join(WEB,'index.html'),'utf8');
  for(const match of html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)){
    const name=match[1].replace(/\.js$/,'');if(loaded.has(name)||name==='dsh-production')continue;
    GUI_TEST_RUNTIME.loadWebFile(env.world, path.join(WEB,match[1]), {filename:match[1]});loaded.add(name);
  }
  assert.ok(env.world.MS,'The shipped index must load the mechanism editor');
  env.PL.state.caps=clone(initial.caps);
  env.world.pywebview.api.compose_equipment_plan=proposed=>bridge.request('compose',{plan:proposed});
  env.world.pywebview.api.query_runtime=request=>bridge.request('query',{request});
  env.PL.replace(clone(plan),'independent-mechanisms.plan.json','isolated-input-sha',true);
  env.RT.syncRevision(); // Match the editor's normal first render before race baselines.
  env.MS=env.world.MS;
  return env;
}

function nativeWithoutRevision(env){const plan=JSON.parse(env.PL.currentInputJson());delete plan.revision;return plan;}
async function legacyAcceptedPlan(){const plan=clone(initial.plan);for(const [index,source] of plan.mechanism_sources.entries())
  source.accepted_fingerprint=(index?'b':'a').repeat(64);
  const report=await bridge.data('compose',{plan});assert.equal(report.state,'pending');return plan;}

// The assertions below use actual reviewed raw material and actual Python compose
// responses. Synthetic response edits are reserved for adversarial GUI boundaries.

test('independent: shipped business island modules load in index order without a browser window',()=>{
  const body=new Element('body'),nodes=new Map(),events=[];
  const world={console,Date,Math,confirm:()=>true,prompt:()=>null,addEventListener:(name,fn)=>events.push([name,fn])};
  world.window=world;world.document={body,createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),
    querySelector:selector=>{if(!nodes.has(selector))nodes.set(selector,new Element());return nodes.get(selector);},
    querySelectorAll:()=>[],getElementById:id=>nodes.get('#'+id)};
  world.pywebview={api:{}};vm.createContext(world);
  const scripts=[...fs.readFileSync(path.join(WEB,'index.html'),'utf8').matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)].map(m=>m[1]);
  assert.equal(new Set(scripts).size,scripts.length);assert.ok(scripts.includes('mechanism-sources.js'));
  // The official DSH client requires a real browser realm, not this business
  // form fake DOM. Its actual IIFE is exercised by production-python-gui.
  for(const script of scripts.filter(name=>name!=='dsh-production.js'))GUI_TEST_RUNTIME.loadWebFile(world, path.join(WEB,script), {filename:script});
  assert.equal(typeof world.MS.edit,'function');assert.equal(typeof world.MS.renderReceipt,'function');
  assert.ok(events.some(([name,fn])=>name==='pywebviewready'&&typeof fn==='function'));
});

test('independent: actor rename updates native references and withdraws only that actor acceptance',async()=>{
  const plan=await legacyAcceptedPlan(),env=environment(plan),b=clone(plan.mechanism_sources[1]);
  env.PL.renameCharacter('a','renamed');
  assert.equal(env.PL.state.plan.mechanism_sources[0].actor,'renamed');
  assert.equal(env.PL.state.plan.mechanism_sources[0].accepted_fingerprint,null);
  assert.deepEqual(clone(env.PL.state.plan.mechanism_sources[1]),b);
});

test('independent: actual ATK-pool and EM-trigger receipts expose Chinese mechanics outside raw details',async()=>{
  const env=environment(),box=new Element();env.MS.renderReceipt(box,preview,env.PL.state.plan);
  const text=visibleText(box);
  assert.match(text,/工程冰角色/);assert.match(text,/工程风角色/);
  assert.match(text,/基础区加算.*提供者当前攻击力/);
  assert.match(text,/每位受益者独立 7 次.*重置次数/);assert.match(text,/持续 15 秒/);
  assert.match(text,/元素反应发生时.*持续 8 秒.*提供者触发时元素精通/);
  assert.doesNotMatch(text,/benefit\.|source\.a|rule_templates|typed-mechanism|NONEXTRA|base_add/);
  const second=new Element();env.MS.preview(second,preview.mechanism_compilations[1],env.PL.state.plan);
  assert.doesNotMatch(visibleText(second),/独立 \d+ 次|共享 \d+ 次/);
  env.MS.edit(0);const before=env.PL.currentInputJson();await press(env.dialog(),'读取当前引用资料');
  assert.match(visibleText(env.dialog()),/基础区加算.*当前攻击力/);
  assert.equal(env.PL.currentInputJson(),before,'Reading the database never injects source material into native input');
});

test('independent: two real sources save only as non-executable knowledge candidates',async()=>{
  const env=environment(),original=nativeWithoutRevision(env);
  for(const index of [0,1]){
    env.MS.edit(index);await press(env.dialog(),'预览本轴机制');
    assert.ok(!descendants(env.dialog()).some(node=>node.tagName==='button'&&node.textContent==='接受本次来源与场景'));
    await press(env.dialog(),'应用机制引用');
    assert.equal(env.PL.state.plan.mechanism_sources[index].accepted_fingerprint,null);
    if(index===0){const partial=await bridge.data('compose',{plan:env.PL.state.plan});
      assert.equal(partial.state,'pending');assert.equal(partial.mechanism_compilations[0].status,'pending');
      assert.ok(partial.mechanism_compilations[0].issues.some(issue=>issue.code==='mechanism_materialization_required'));}
  }
  const final=await bridge.data('compose',{plan:env.PL.state.plan});assert.equal(final.state,'pending');
  assert.ok(final.mechanism_compilations.every(record=>record.execution_authority==='none'&&record.required_confirmation===null));
  assert.deepEqual(nativeWithoutRevision(env),original,'Saving knowledge candidates never copies returned compiled values');
});

test('independent: actual DEF holdout renders the descriptor attribute instead of hardcoded ATK',async()=>{
  const plan=clone(initial.plan);plan.mechanism_sources[0].mechanism_id='review.def-addition';
  const env=environment(plan);env.MS.edit(0);await press(env.dialog(),'读取当前引用资料');await press(env.dialog(),'预览本轴机制');
  const text=visibleText(env.dialog());assert.match(text,/基础区加算.*提供者当前防御力/);
  assert.doesNotMatch(text,/提供者当前攻击力/);assert.match(text,/每位受益者独立 7 次/);
  assert.equal(env.PL.state.plan.mechanism_sources[0].accepted_fingerprint,null);
});

test('independent: catalog uses actual identity and selection resets inherited scenario without choosing defaults',async()=>{
  const env=environment();env.MS.edit(0);const before=env.PL.currentInputJson();await press(env.dialog(),'发现受审机制来源');
  const catalog=control(env.dialog(),'目录中的来源'),values=catalog.children.map(n=>n.value);
  assert.ok(values.includes('review.def-addition'));assert.ok(values.includes('skill.current-atk-limited-addition'));
  assert.ok(!values.includes('passive.swirl-elemental-bonus'),'Another character identity is not offered');
  catalog.value='review.def-addition';await catalog.emit('change');
  assert.match(visibleText(env.dialog()),/提供者当前防御力/);
  assert.equal(control(env.dialog(),'目标类型').value,'unknown');assert.equal(control(env.dialog(),'场景声明状态').value,'pending');
  assert.ok(!descendants(env.dialog()).some(node=>node.tagName==='button'&&node.textContent==='接受本次来源与场景'));
  await press(env.dialog(),'应用机制引用');const saved=env.PL.state.plan.mechanism_sources[0];
  assert.equal(saved.mechanism_id,'review.def-addition');assert.equal(saved.accepted_fingerprint,null);
  assert.deepEqual(clone(saved.scenario.values),{});assert.equal(saved.calibration,null);
});

test('independent: unknown scene may be saved only as a knowledge candidate',async()=>{
  const env=environment();env.MS.edit(0);await press(env.dialog(),'读取当前引用资料');
  env.field('所有队员存活','unknown');await press(env.dialog(),'预览本轴机制');
  assert.ok(!descendants(env.dialog()).some(node=>node.tagName==='button'&&node.textContent==='接受本次来源与场景'));
  await press(env.dialog(),'应用机制引用');
  const saved=env.PL.state.plan.mechanism_sources[0];assert.equal(saved.scenario.values.all_party_alive,null);
  assert.equal(saved.accepted_fingerprint,null);const actual=await bridge.data('compose',{plan:env.PL.state.plan});
  assert.equal(actual.state,'pending');assert.ok(actual.mechanism_compilations[0].issues.some(issue=>issue.code!=='mechanism_confirmation_required'));
});

test('independent: fresh Python conflict and missing parent metadata never become accepted',async()=>{
  for(const kind of ['duplicate','metadata']){
    const plan=clone(initial.plan),index=kind==='metadata'?1:0;
    if(kind==='duplicate')plan.mechanism_sources.push({...clone(plan.mechanism_sources[0]),id:'review.duplicate'});
    else plan.attack_metadata_sources=[];
    const response=await bridge.request('compose',{plan});
    if(kind==='metadata'){
      // Removing metadata also withdraws its owned native element placeholder.
      // The real schema correctly rejects this incomplete draft before compilation.
      assert.equal(response.code,1);assert.match(response.message,/元信息|element/);
    }else{
      assert.equal(response.code,0);assert.equal(response.data.state,'pending');
      assert.ok(response.data.mechanism_compilations[index].issues.some(issue=>issue.code!=='mechanism_confirmation_required'));
    }
    const env=environment(plan);env.MS.edit(index);await press(env.dialog(),'预览本轴机制');
    assert.ok(!descendants(env.dialog()).some(node=>node.tagName==='button'&&node.textContent==='接受本次来源与场景'));
    await press(env.dialog(),'应用机制引用');assert.equal(env.PL.state.plan.mechanism_sources[index].accepted_fingerprint,null);
  }
});

test('independent: remove one native source preserves unrelated source and every other input',async()=>{
  const env=environment(await legacyAcceptedPlan()),expected=nativeWithoutRevision(env);expected.mechanism_sources.splice(0,1);
  env.MS.edit(0);await press(env.dialog(),'移除机制引用');assert.deepEqual(nativeWithoutRevision(env),expected);
  const fresh=await bridge.data('compose',{plan:env.PL.state.plan});assert.equal(fresh.mechanism_compilations.length,1);
});

test('independent: server-derived model/template/acceptance mutations never enter the native draft',async()=>{
  const env=environment(),expected=nativeWithoutRevision(env);env.MS.edit(0);
  env.world.pywebview.api.compose_equipment_plan=async plan=>{
    const reply=await bridge.request('compose',{plan});reply.data.plan.manual_template.characters[0].stats.atk=999999;
    reply.data.plan.mechanism_sources[0].accepted_fingerprint='f'.repeat(64);
    reply.data.plan.mechanics_model.rules.push({id:'injected.rule'});return reply;
  };
  await press(env.dialog(),'应用机制引用');assert.deepEqual(nativeWithoutRevision(env),expected);
});

for(const change of ['actor','source_epoch','caps','file_sha','path','replacement','draft','close'])
test(`independent: old preview cannot survive ${change}`,async()=>{
  const env=environment();env.MS.edit(0);const calls=[];
  env.world.pywebview.api.compose_equipment_plan=plan=>new Promise(resolve=>calls.push({plan:clone(plan),resolve}));
  const pending=press(env.dialog(),'预览本轴机制');assert.equal(calls.length,1);
  if(change==='actor')env.field('机制提供者','b');
  if(change==='source_epoch')env.PL.state.sourceEpoch++;
  if(change==='caps')env.PL.state.caps.mechanism_source_contract.compiler='changed-interface';
  if(change==='file_sha')env.PL.state.fileSha='new-file-sha';
  if(change==='path')env.RT.state.path='new-file-path';
  if(change==='replacement')env.PL.replace(clone(initial.plan),'replacement.json','replacement-sha',true);
  if(change==='draft')env.PL.state.draft=clone(initial.plan);
  if(change==='close')press(env.dialog(),'放弃并关闭');
  const expected=env.PL.currentInputJson(),answer=await bridge.request('compose',{plan:calls[0].plan});calls[0].resolve(answer);await pending;
  assert.equal(env.PL.currentInputJson(),expected);assert.doesNotMatch(visibleText(env.dialog()),/提供者当前攻击力/);
  assert.equal(env.PL.state.plan.mechanism_sources[0].accepted_fingerprint,null);
});

test('independent: reversed actual previews show the newest source qualification only',async()=>{
  const env=environment();env.MS.edit(0);const calls=[];
  env.world.pywebview.api.compose_equipment_plan=plan=>new Promise(resolve=>calls.push({plan:clone(plan),resolve}));
  const first=press(env.dialog(),'预览本轴机制');env.field('机制提供者','b');const second=press(env.dialog(),'预览本轴机制');
  assert.equal(calls.length,2);const replies=await Promise.all(calls.map(call=>bridge.request('compose',{plan:call.plan})));
  assert.ok(replies[1].data.mechanism_compilations[0].issues.some(i=>i.code!=='mechanism_confirmation_required'));
  calls[1].resolve(replies[1]);await second;const newer=env.dialog().textContent;calls[0].resolve(replies[0]);await first;
  assert.equal(env.dialog().textContent,newer);assert.ok(!descendants(env.dialog()).some(node=>node.tagName==='button'&&node.textContent==='接受本次来源与场景'));
  assert.equal(env.PL.state.plan.mechanism_sources[0].accepted_fingerprint,null);
});

test('independent: forged legacy confirmation cannot turn a knowledge candidate into authority',async()=>{
  for(const mutation of ['fingerprint','actor','missing_record']){
    const env=environment();env.MS.edit(0);await press(env.dialog(),'预览本轴机制');
    env.world.pywebview.api.compose_equipment_plan=async plan=>{
      const reply=await bridge.request('compose',{plan}),row=reply.data.mechanism_compilations[0];
      if(mutation==='fingerprint'){row.required_confirmation={accepted_fingerprint:'f'.repeat(64)};row.acceptance_fingerprint='f'.repeat(64);}
      if(mutation==='actor')row.actor='b';
      if(mutation==='missing_record')reply.data.mechanism_compilations.splice(0,1);
      return reply;
    };
    const before=env.PL.currentInputJson();await press(env.dialog(),'应用机制引用');
    env.world.pywebview.api.compose_equipment_plan=plan=>bridge.request('compose',{plan});
    assert.equal(env.PL.currentInputJson(),before,mutation);
  }
});

test('independent: dialog cancellation and failed removal are atomic on a legacy accepted plan',async()=>{
  const env=environment(await legacyAcceptedPlan()),before=env.PL.currentInputJson();env.MS.edit(0);
  env.field('施放到生效（秒）',.123);press(env.dialog(),'放弃并关闭');assert.equal(env.PL.currentInputJson(),before);
  env.MS.edit(0);env.world.pywebview.api.compose_equipment_plan=async()=>({code:1,message:'独立故障注入：保存前拒绝'});
  await press(env.dialog(),'移除机制引用');assert.equal(env.PL.currentInputJson(),before);
});

test('independent: source-detail response arriving after actor change cannot restore the previous profile',async()=>{
  const env=environment();env.MS.edit(0);let request,finish;
  env.world.pywebview.api.query_runtime=value=>{request=clone(value);return new Promise(resolve=>finish=resolve);};
  const pending=press(env.dialog(),'读取当前引用资料');env.field('机制提供者','b');
  finish(await bridge.request('query',{request}));await pending;
  assert.doesNotMatch(visibleText(env.dialog()),/提供者当前攻击力/);
  assert.equal(control(env.dialog(),'机制提供者').value,'b');
  assert.equal(env.PL.state.plan.mechanism_sources[0].actor,'a');
});

test('independent: wrong catalog subject and wrong selected profile are rejected before being offered',async()=>{
  for(const kind of ['catalog','profile']){
    const env=environment();env.MS.edit(0);env.world.pywebview.api.query_runtime=async request=>{
      const reply=await bridge.request('query',{request});
      if(kind==='catalog')reply.data.items[0].subject.target_id=999;
      else reply.data.selection.mechanism_id='not.the.selected.source';
      return reply;
    };
    await press(env.dialog(),kind==='catalog'?'发现受审机制来源':'读取当前引用资料');
    assert.match(env.dialog().textContent,/不一致/);
    if(kind==='catalog')assert.ok(!descendants(env.dialog()).some(n=>n.attributes['aria-label']==='目录中的来源'));
    else assert.doesNotMatch(visibleText(env.dialog()),/提供者当前攻击力/);
  }
});

test('independent: checking and action receipt entry points include the generic mechanism UI',()=>{
  const env=environment();let editCalls=0;env.MS.edit=()=>editCalls++;
  env.world.RC.shown=true;env.world.RC.render();
  const roots=[env.world.document.querySelector('#check-model')];
  // Locate the button in the actual checks render tree rather than calling MS.render directly.
  const source=fs.readFileSync(path.join(WEB,'checks.js'),'utf8');
  for(const match of source.matchAll(/\$\("(#[^"]+)"\)/g))roots.push(env.world.document.querySelector(match[1]));
  const entry=roots.flatMap(descendants).find(n=>n.tagName==='button'&&n.textContent==='添加通用机制来源');
  assert.ok(entry,'The existing check page must expose the editor');entry.emit('click');assert.equal(editCalls,1);
  const box=new Element();env.world.AE.renderReceipt(box,{mechanism_compilations:preview.mechanism_compilations},env.PL.state.plan);
  assert.match(visibleText(box),/提供者当前攻击力/);assert.match(visibleText(box),/提供者触发时元素精通/);
});
