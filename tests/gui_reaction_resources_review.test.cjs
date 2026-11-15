const GUI_TEST_RUNTIME = require('./helpers/gui_runtime.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),world=vm.createContext({window:{}});
GUI_TEST_RUNTIME.loadWebFile(world, path.join(root,'src/genshin_app/gui/web/reaction-resources.js'));
function sample(){return JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/resource_reaction_reporting.json'),'utf8')).cases['ordinary-swirl-income'];}
for(const keys of [['team','other'],['team','team']])test('each declared key has one unique receipt '+JSON.stringify(keys),()=>{
 const result=sample(),op=result.resource_reactions.operations[0];Object.assign(op,{keys,expected_count:2,actual_end:3});
 result.shared_pools.trace[2]={...result.shared_pools.trace[1],id:3};
 const row=world.window.RR.rows(result)[0];assert.ok(row.issue);assert.equal(row.receipts.length,0);
});
