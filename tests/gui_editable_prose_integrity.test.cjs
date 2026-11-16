const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=process.cwd();
const charsPath=path.join(root,'src/genshin_app/gui/web/chars.js');
function fixture(template){
  const nodes=[],copy=structuredClone(template),panel={appendChild(){},innerHTML:''};
  const element=tag=>{const node={tag,style:{},attributes:{},children:[],listeners:{},value:'',appendChild(child){this.children.push(child);return child;},append(...children){this.children.push(...children);},addEventListener(name,callback){this.listeners[name]=callback;},setAttribute(key,value){this.attributes[key]=String(value);}};nodes.push(node);return node;};
  const sandbox={console,document:{createElement:element},RT:{tpl:()=>copy,state:{enums:{elements:['cryo']}},renderAll(){},charColor:()=> '#fff',charIds:()=>copy.characters.map(c=>c.id)},$:()=>panel,setStatus(){}};sandbox.window=sandbox;
  const context=vm.createContext(sandbox);
  for(const file of ['display.js','panel.js'])vm.runInContext(fs.readFileSync(path.join(root,'src/genshin_app/gui/web',file),'utf8'),context,{filename:file});
  vm.runInContext(fs.readFileSync(charsPath,'utf8'),context,{filename:charsPath});
  vm.runInContext('RT.renderTeamPanel()',context);
  return {copy,nodes,context,field:label=>nodes.find(n=>n.attributes['aria-label']===label)};
}
function template(){return {meta:{name:'中文方案 App test',assumptions:['第一条正常说明','使用 App 保留原始 buff ID；\\n 不等于换行']},enemy:{level:100,resistance:{}},characters:[{id:'test_actor',name:'测试角色 C6',description:'App 自定义说明 unknown_source',level:90,stats:{atk:100}}],hits:[],buffs:[],switches:[]};}
test('editable plan name and assumptions preserve literal source strings',()=>{const input=template(),f=fixture(input);assert.equal(f.field('名称').value,input.meta.name);assert.equal(f.nodes.find(n=>n.tag==='textarea').value,input.meta.assumptions.join('\n'));});
test('editing one assumption cannot write a localization placeholder into untouched lines',()=>{const input=template(),f=fixture(input),e=f.nodes.find(n=>n.tag==='textarea');e.value=e.value.replace('第一条正常说明','只修改第一条');e.listeners.change();assert.deepEqual(Array.from(f.copy.meta.assumptions),['只修改第一条',input.meta.assumptions[1]]);});
test('editable character name and description use original data, not aliases or fallback text',()=>{const input=template(),f=fixture(input);assert.equal(f.field('角色 1 中文名称').value,input.characters[0].name);assert.equal(f.field('角色说明').value,input.characters[0].description);const e=f.field('角色说明');e.value+=' 新说明';e.listeners.change();assert.equal(f.copy.characters[0].description,input.characters[0].description+' 新说明');});
test('absent optional text remains empty instead of being synthesized into editable data',()=>{const input=template();delete input.meta.name;delete input.meta.assumptions;delete input.characters[0].name;delete input.characters[0].description;const f=fixture(input);assert.equal(f.field('名称').value,'');assert.equal(f.nodes.find(n=>n.tag==='textarea').value,'');assert.equal(f.field('角色 1 中文名称').value,'');assert.equal(f.field('角色说明').value,'');});
test('read-only localization retains its existing fallback policy',()=>{const f=fixture(template());assert.equal(vm.runInContext("DS.prose('原文 App')",f.context),'中文说明待补（可展开查看原文）');});

