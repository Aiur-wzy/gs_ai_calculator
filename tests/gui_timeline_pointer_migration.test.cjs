// Independent, test-only old pointer implementation versus the actual bundle.
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'..');
function setup(modern){
  class Element extends EventTarget{constructor(){super();this.attrs={};this.capture=null;}
    setAttribute(k,v){this.attrs[k]=v;}removeAttribute(k){delete this.attrs[k];}
    setPointerCapture(id){this.capture=id;}hasPointerCapture(id){return this.capture===id;}
    releasePointerCapture(){this.capture=null;}}
  const events=new EventTarget(),element=new Element(),svg=new Element(),visual=new Element(),wrap={scrollLeft:0};
  let template={id:'first'},active=null;
  const trace={select:[],commit:[],status:[],paints:0},RT={pxPerSec:20,tpl:()=>template,
    duration:()=>30,round2:v=>Math.round(v*100)/100,select:(kind,index)=>trace.select.push([kind,index]),
    renderAll:()=>trace.paints++},world={window:events,RT,$:id=>id==='#timeline'?svg:wrap,
      setStatus:message=>trace.status.push(message),console};
  vm.createContext(world);
  let controller,unbind;
  if(modern){vm.runInContext(fs.readFileSync(path.join(root,'src/genshin_app/gui/web/business-ui.js'),'utf8'),world);
    controller=world.GenshinBusinessUI.createTimelinePointerController({events,template:()=>template,
      select:s=>RT.select(s.kind,s.index),geometry:()=>({svg,viewport:wrap,duration:30,pixelsPerSecond:20}),
      round:RT.round2,status:world.setStatus,repaint:RT.renderAll,active:next=>active=next});
    unbind=controller.bind(element,4,d=>trace.commit.push(d),visual,s=>`时间 ${s}`,{kind:'hit',index:2});
  }else{
    const old=fs.readFileSync(path.join(__dirname,'reference/gui-timeline-modern-v1.js'),'utf8');
    vm.runInContext(old.slice(old.indexOf('RT.enableDrag =')),world);
    RT.enableDrag(element,4,d=>trace.commit.push(d),visual,s=>`时间 ${s}`,{kind:'hit',index:2});
  }
  const fire=(target,type,x=80,id=1,button=0)=>{const e=new Event(type,{cancelable:true});
    Object.assign(e,{clientX:x,pointerId:id,button});target.dispatchEvent(e);};
  return{trace,visual,wrap,svg,element,events,fire,unbind,controller,
    changeTemplate:()=>template={id:'next'},cancel:()=>modern?active?.():RT.cancelDrag?.(),
    snapshot:()=>JSON.parse(JSON.stringify({trace,attrs:visual.attrs,capture:svg.capture}))};
}
for(const mode of ['up','cancel','lost-capture','template-changed','scroll','foreign-pointer'])test(`G4 pointer old/new oracle: ${mode}`,()=>{
  const run=modern=>{const h=setup(modern);h.fire(h.element,'pointerdown');
    if(mode==='scroll')h.wrap.scrollLeft=40;
    h.fire(h.events,'pointermove',120);if(mode==='template-changed')h.changeTemplate();
    if(mode==='cancel')h.cancel();else if(mode==='lost-capture')h.fire(h.svg,'lostpointercapture',120);
    else{if(mode==='foreign-pointer')h.fire(h.events,'pointerup',200,99);h.fire(h.events,'pointerup',120);}
    h.fire(h.events,'pointerup',160);return h.snapshot();};
  assert.deepEqual(run(true),run(false));
});
test('G4 pointer binding teardown only cancels its own active gesture',()=>{
  const h=setup(true),extra=new EventTarget();
  const off=h.controller.bind(extra,0,()=>{},h.visual,null,{kind:'hit',index:3});
  h.fire(h.element,'pointerdown');off();h.fire(h.events,'pointerup',120);
  assert.deepEqual(h.trace.commit,[2]);assert.equal(h.trace.paints,1);
  h.fire(h.element,'pointerdown');h.unbind();h.fire(h.events,'pointerup',120);
  assert.deepEqual(h.trace.commit,[2]);assert.equal(h.visual.attrs.transform,undefined);
  h.controller.dispose();
});
