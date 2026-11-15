const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const helpers={module:{exports:{}},require,__dirname,console};
const harness=fs.readFileSync(path.join(__dirname,'gui_hit_parameters_review.test.cjs'),'utf8').split('\ntest(')[0];
vm.runInNewContext(harness+'\nmodule.exports={setup,control,all,clone};',helpers);
const {setup,control,all,clone}=helpers.module.exports;
test('opening and rerendering native plans cannot insert optional metadata or damage maps',()=>{
 const e=setup(),p=clone(e.PL.state.plan);p.equipment_inputs={};delete p.manual_template.meta;delete p.manual_template.characters[0].stats.dmg_bonus;
 const before=JSON.stringify(p);e.PL.replace(p,'fixture.json','sha',true);
 e.RT.renderAll();e.RT.renderAll();assert.equal(JSON.stringify(p),before);
 assert.equal(e.PL.dirty(),false);
});
test('editing absent metadata and adding a damage field still persists explicit user changes',()=>{
 const e=setup(),p=clone(e.PL.state.plan);p.equipment_inputs={};delete p.manual_template.meta;delete p.manual_template.characters[0].stats.dmg_bonus;
 e.PL.replace(p,'fixture.json','sha',true);
 const panel=e.world.document.querySelector('#team-panel'),name=control(panel,'名称');name.value='用户方案';name.emit('change');
 assert.equal(p.manual_template.meta.name,'用户方案');assert.ok(!('dmg_bonus' in p.manual_template.characters[0].stats));
 const add=all(panel).find(n=>n.tagName==='button'&&n.textContent==='+ 添加加成');add.emit('click');
 assert.equal(p.manual_template.characters[0].stats.dmg_bonus.all,0);assert.equal(e.PL.dirty(),true);
});
