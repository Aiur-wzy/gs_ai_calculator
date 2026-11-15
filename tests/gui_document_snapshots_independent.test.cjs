const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const {WEB:web,loadWebScripts}=require('./helpers/gui_runtime.cjs');const sources={};
const c=vm.createContext({console,assert});c.window=c;c.addEventListener=()=>{};
c.document={querySelector:()=>({value:'native.plan.json',innerHTML:'',appendChild(){}}),createElement:()=>({})};
c.DS={prose:x=>x};c.setStatus=()=>{};
c.RT={tpl:()=>null,state:{saveRequest:0,acceptedSaveRequest:0,path:'native.plan.json',revision:0,plansDir:'.'},saveTemplate:async()=>true};
for(const name of ['json-transport.js','ui.js','plans.js','whiteboard.js']){
  const text=fs.readFileSync(path.join(web,name),'utf8');sources[name]=crypto.createHash('sha256').update(text).digest('hex');
  loadWebScripts(c,[name],{includeUi:false});
}
vm.runInContext(`RT.tpl=()=>PL.state.plan?.manual_template;PL.renderStatus=()=>{};RT.refreshPlans=()=>{};WB.render=()=>{};window.$=s=>document.querySelector(s);`,c);
const nodeTest=require('node:test');const test=(name,code)=>nodeTest(name,async()=>{await vm.runInContext(`(async()=>{${code}})()`,c);});
(async()=>{
await test('Native plan unchanged snapshot keeps floats but same-value edit is dirty',`
  PL.state.plan=JSONTransport.parse('{"manual_template":{"v":1.0},"resolution":{"v":2.0},"resolved_template":{"v":2.0}}');
  PL.state.draft=null;PL.state.savedJson=PL.currentInputJson();assert(!PL.dirty());
  assert.equal(PL.currentInputJson(),'{"manual_template":{"v":1.0}}');
  PL.state.plan.resolution.v=3;assert(!PL.dirty());
  PL.state.plan.manual_template.v=1;assert(PL.dirty());
`);
await test('Native draft clone retains original tokens and detects explicit same-value write',`
  PL.state.plan=JSONTransport.parse('{"manual_template":{"v":1.0}}');
  PL.state.savedJson=PL.currentInputJson();PL.state.draft=UI.clone(PL.state.plan);
  PL.state.draftInitialJson=PL.inputText(PL.input(PL.state.plan));assert(!PL.draftChanged());
  PL.state.draft.manual_template.v=1;assert(PL.draftChanged());
  assert.equal(PL.currentInputJson(),PL.state.savedJson);PL.state.draft=null;
`);
await test('Native save response does not mark later same-value edit saved',`
  PL.state.plan=JSONTransport.parse('{"manual_template":{"v":1.0}}');
  PL.state.draft=null;PL.state.savedJson=PL.currentInputJson();PL.state.fileSha='old';
  let resolve,submitted;window.callApi=(method,...args)=>{assert.equal(method,'save_plan');submitted=JSONTransport.stringify(args[1]);return new Promise(r=>resolve=r);};
  const saving=RT.saveTemplate();PL.state.plan.manual_template.v=1;
  resolve({code:0,data:{path:'native.plan.json',file_sha:'new'}});await saving;
  assert.equal(submitted,'{"manual_template":{"v":1.0}}');assert(PL.dirty());
  assert.equal(PL.state.savedJson,'{"manual_template":{"v":1.0}}');
`);
await test('Whiteboard snapshot distinguishes equal Number edits and invalidates prior token',`
  WB.state.board=JSONTransport.parse('{"duration":10.0,"actions":[],"team":[]}');
  WB.state.editorSession=null;WB.state.equipmentDrafts={};WB.state.savedJson=WB.json();
  const token=WB.token();assert(!WB.dirty());assert(WB.current(token));
  WB.state.board.duration=10;assert(WB.dirty());assert(!WB.current(token));
`);
await test('Whiteboard save response cannot overwrite later same-value edit snapshot',`
  WB.state.board=JSONTransport.parse('{"duration":10.0,"actions":[],"team":[]}');
  WB.state.savedJson=WB.json();WB.state.path='board.json';WB.state.fileSha='old';
  let resolve,submitted;window.callApi=(method,...args)=>{assert.equal(method,'whiteboard_save');submitted=JSONTransport.stringify(args[1]);return new Promise(r=>resolve=r);};
  const saving=WB.save('board.json');WB.state.board.duration=10;
  resolve({code:0,data:{path:'board.json',file_sha:'new'}});assert.equal(await saving,false);
  assert.equal(submitted,'{"duration":10.0,"actions":[],"team":[]}');
  assert(WB.dirty());assert.equal(WB.state.savedJson,'{"duration":10.0,"actions":[],"team":[]}');
`);
for(const [name,hash] of Object.entries(sources))assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(web,name),'utf8')).digest('hex'),hash);
})();
