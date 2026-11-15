// Production App application port + real WB. No retired AC/Chat/Turn owner.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const R=require('./helpers/gui_runtime.cjs'),clone=value=>JSON.parse(JSON.stringify(value));
const helper={module:{exports:{}},require,__dirname,console};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'gui_whiteboard.test.cjs'),'utf8').split('\ntest(')[0]+'\nmodule.exports={setup};',helper);
function setup(){
 const e=helper.module.exports.setup();e.WB.state.path='board.json';e.WB.state.fileSha='board-sha';
 e.WB.state.savedJson=e.WB.json();e.w.WS={tab:()=>e.page||'whiteboard',reveal(){}};
 for(const file of ['agent-document-adapter.js','candidate-application.js'])R.loadWebFile(e.w,path.join(R.WEB,file));
 return Object.assign(e,{application:e.w.CandidateEditorApplication.create()});
}
function target(e){return {...clone(e.WB.state.board),revision:e.WB.state.board.revision+1,title:'已审核的当前候选',actions:[]};}
function reviewed(e,board=target(e)){return {kind:'whiteboard',board,revision:3,receipt_fingerprint:'a'.repeat(64),issues:[]};}
test('current reviewed draft preserves path/SHA, clears old calculated state and leaves an unsaved formal document',async()=>{
 const e=setup(),saved=e.WB.state.savedJson;
 for(const name of ['result','resultProjection','resultPath','staleResultPath','reusableResult','equipmentReport'])e.WB.state[name]={old:true};
 const base=e.application.capture('whiteboard'),accepted=reviewed(e),actual=await e.application.applyReviewed(accepted,base,'app-exact-conversation');
 assert.deepEqual(clone(e.WB.state.board),accepted.board);assert.equal(e.WB.state.path,'board.json');assert.equal(e.WB.state.fileSha,'board-sha');
 assert.equal(e.WB.state.savedJson,saved);assert.equal(e.WB.dirty(),true);assert.equal(e.WB.state.mode,'actions');
 for(const name of ['result','resultProjection','resultPath','staleResultPath','reusableResult','equipmentReport'])assert.equal(e.WB.state[name],null,name);
 assert.equal(e.application.matches(actual),true);assert.equal(e.application.matches(base),false);
 assert.ok(e.calls.every(row=>!['whiteboard_read_result','whiteboard_save'].includes(row.name)));
});
test('an already-applied base cannot be applied again by the Renderer port',async()=>{
 const e=setup(),base=e.application.capture('whiteboard'),accepted=reviewed(e);
 await e.application.applyReviewed(accepted,base,'app-conversation');const current=e.WB.state.board;
 await assert.rejects(e.application.applyReviewed(accepted,base,'app-conversation'),/正式文档已经变化/);
 assert.equal(e.WB.state.board,current);
});
for(const change of ['path','sha','request','replacement','panel','equipment-draft'])test(`current document CAS rejects a reviewed draft after ${change} changes`,async()=>{
 const e=setup(),base=e.application.capture('whiteboard'),accepted=reviewed(e);
 if(change==='path')e.WB.state.path='other.json';if(change==='sha')e.WB.state.fileSha='external-sha';if(change==='request')e.WB.state.request++;
 if(change==='replacement')e.WB.state.board=clone(e.WB.state.board);if(change==='panel')e.WB.change(board=>board.team[0].panel.atk=999);
 if(change==='equipment-draft')e.WB.state.equipmentDrafts={a:{pending:true}};
 const title=e.WB.state.board.title;
 await assert.rejects(e.application.applyReviewed(accepted,base,'app-conversation'),/正式文档已经变化/);
 assert.equal(e.WB.state.board.title,title);
});
test('DSH history/Inspector navigation is not an App document permission owner',async()=>{
 const e=setup(),base=e.application.capture('whiteboard'),accepted=reviewed(e);
 e.page='result';e.w.selectedDshSession='another-readable-session';
 assert.equal(e.application.matches(base),true);await e.application.applyReviewed(accepted,base,'exact-bound-conversation');
 assert.equal(e.WB.state.board.title,accepted.board.title);
});
test('a mismatched document ID is rejected before any formal mutation',async()=>{
 const e=setup(),base=e.application.capture('whiteboard'),accepted=reviewed(e);accepted.board.board_id='foreign-board';
 await assert.rejects(e.application.applyReviewed(accepted,base,'app-conversation'),/正式文档已经变化/);
 assert.equal(e.application.matches(base),true);
});
for(const file of ['board.json','new-board.json'])test(`saving an applied draft to ${file} gives a new truthful App snapshot without a Chat baseline`,async()=>{
 const e=setup(),base=e.application.capture('whiteboard');await e.application.applyReviewed(reviewed(e),base,'bound-conversation');
 assert.equal(await e.WB.save(file),true);const next=e.application.capture('whiteboard');
 assert.equal(next.path,file);assert.equal(next.sha,'saved');assert.equal(e.application.matches(next),true);
 assert.equal(e.application.matches(base),false);assert.equal(e.w.AC,undefined);
});
for(const change of ['panel','replacement','equipment-draft'])test(`late successful save after ${change} cannot preserve stale App application authority`,async()=>{
 const e=setup(),base=e.application.capture('whiteboard');let finish;e.w.callApi=()=>new Promise(resolve=>finish=resolve);
 const pending=e.WB.save('board.json');
 if(change==='panel')e.WB.change(board=>board.team[0].panel.atk=999);
 if(change==='replacement')e.WB.state.board=clone(e.WB.state.board);
 if(change==='equipment-draft')e.WB.state.equipmentDrafts={a:{pending:true}};
 finish({code:0,data:{path:'board.json',file_sha:'written-sha',pending:[]}});await pending;
 assert.equal(e.application.matches(base),false);
 if(change==='replacement'){
   // Reopening the same saved content is clean, but it is a different formal
   // object. An old in-flight save cannot adopt its SHA or application rights.
   assert.equal(e.WB.dirty(),false);assert.equal(e.WB.state.fileSha,'board-sha');
 }else assert.equal(e.WB.dirty(),true);
});
