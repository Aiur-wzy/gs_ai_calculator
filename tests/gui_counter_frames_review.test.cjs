const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),world={window:{}};
vm.runInNewContext(fs.readFileSync(path.join(root,'src/genshin_app/gui/web/counter-frames.js'),'utf8'),world);
for(const field of ['event_run_id','instance_id'])test(`gate ${field} mismatch cannot display passed`,()=>{
 const row={run_id:'0:actual-hit',instance_id:'actual-instance',gate_qualification:{kind:'shared_pool',consumer_id:'c',event_run_id:'0:actual-hit',instance_id:'actual-instance'}};
 row.gate_qualification[field]='other-occurrence-or-instance';
 assert.doesNotMatch(world.window.CF.gate(row),/已通过/);
});
