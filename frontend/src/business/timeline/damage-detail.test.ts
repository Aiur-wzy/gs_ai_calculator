import {describe,expect,it} from "vitest";
import {renderDamageExplanation,resolveDamageExplanation} from "./damage-detail";

function fixture(origin="engine_formula"){
  const doc:any={contract:"damage-explanation-v1",schema_version:1,status:"complete",
    sources:{s:{action_id:"e1"}},panels:{p:{character:"c",scaling_stat:"atk",inputs:{scaling:2000,multiplier:2,crit_rate_effective:1},baseline:{base_atk:900}}},
    modifiers:{m:{name_zh:"队友基础加算",zone:"base_add",value:1500,origin:"sampled_expression",
      execution:{provider:"b",layers:2},sampling:{stat:"atk",source_value:3000,value:750,sample_t:1,expression:{factor:0.25,max:1000}}}},
    formulas:{f:{expression:"scaling * multiplier + base_add"}},events:{e:{source_ref:"s",panel_ref:"p",formula_ref:"f",modifier_refs:["m"],
      checkpoints:{scaled_base:4000,base_after_flat:5500},zones:{base_add:1500},normalization:{status:"available",value:2.75,
      reference_stat:"atk",reference_value:2000,checkpoint:"base_after_flat",includes:["base_add"],excludes:["crit"]},outcome:{origin,count:1}}}};
  const projection:any={explanationDocument:doc,explanationFingerprint:"proof"};
  const event:any={detail:{explanation_ref:"e",explanation_fingerprint:"proof"}};
  return {doc,projection,event};
}
function render(f=fixture()){
  const box=document.createElement("div");
  const view:any={projection:f.projection,detailSelection:f.event,characterNames:new Map([["b","队友"]])};
  renderDamageExplanation(box,f.event,{view,document,services:{}});
  return {box,view,f};
}
function open(box:HTMLElement,index:number){
  const d=box.querySelectorAll("details")[index];d.open=true;d.dispatchEvent(new Event("toggle"));return d;
}
describe("damage explanation reference and lazy UI",()=>{
  it("resolves shared records without copying or changing them",()=>{
    const f=fixture(),before=JSON.stringify(f.doc),resolved=resolveDamageExplanation(f.projection,f.event.detail);
    expect(resolved.panel).toBe(f.doc.panels.p);expect(resolved.status).toBe("available");
    expect(JSON.stringify(f.doc)).toBe(before);
  });
  it("rejects old fingerprints and missing shared references",()=>{
    const f=fixture();expect(resolveDamageExplanation(f.projection,{...f.event.detail,explanation_fingerprint:"old"}).status).toBe("stale");
    delete f.doc.sources.s;expect(resolveDamageExplanation(f.projection,f.event.detail).status).toBe("invalid");
  });
  it("distinguishes budget omission from corruption",()=>{
    const f=fixture();f.doc.status="partial";delete f.doc.events.e;
    expect(resolveDamageExplanation(f.projection,f.event.detail).status).toBe("invalid");
    delete f.event.detail.explanation_ref;
    expect(resolveDamageExplanation(f.projection,f.event.detail).status).toBe("omitted");
    f.doc.schema_version=2;expect(resolveDamageExplanation(f.projection,f.event.detail).status).toBe("unsupported");
  });
  it("shows consumed inputs and lazily displays the real provider contribution",()=>{
    const {box}=render();expect(box.textContent).toContain("2,000");expect(box.textContent).not.toContain("队友基础加算");
    const formula=open(box,0);expect(formula.textContent).toContain("折算包含");expect(formula.textContent).toContain("275%");
    const mods=open(box,1);expect(mods.textContent).toContain("3,000");expect(mods.textContent).toContain("1,500");
    expect(mods.textContent).toContain("750");expect(mods.textContent).toContain("表达式上限");
    expect(box.textContent).not.toContain("未消费的防御力");
  });
  it("shows the original coefficient, effective coefficient and source from the execution receipt",()=>{
    const f=fixture();
    f.doc.sources.s.reaction_coefficient={base_factor:0.75,factor_multiplier:1.1,effective_factor:0.825,
      provider_id:"b",source:"天赋版本记录",status:"assumed"};
    const formula=open(render(f).box,0);
    for(const text of ["原始反应系数","0.75","生效反应系数","0.825","队友","天赋版本记录","显式假设"]){
      expect(formula.textContent).toContain(text);
    }
  });
  it.each(["fixed_override","external"])("labels %s without inventing a final formula",origin=>{
    const {box}=render(fixture(origin));expect(box.textContent).toContain(origin==="external"?"外部计算补充":"单事件固定值");
    expect(box.textContent).toContain("自动计算基线");
  });
  it("ignores lazy work after selection or result changes",()=>{
    const {box,view}=render();view.projection={};const mods=open(box,1);expect(mods.textContent).not.toContain("队友基础加算");
  });
  it("shows exact Buff links only for adopted benefits and rejects cross-event explanation refs",()=>{
    const f=fixture();f.event.character="a";f.doc.events.e.event_id="hit1";
    expect(resolveDamageExplanation(f.projection,{...f.event.detail,hit_id:"hit2"}).status).toBe("invalid");
    f.doc.modifiers.m.buff_id="buff";f.doc.modifiers.unused={name_zh:"未采用收益",consumed:false,value:42,zone:"def"};
    f.doc.events.e.modifier_refs.push("unused");
    const target={id:"buff-row",start:0,end:3,character:"a",detail:{buff_id:"buff"}};f.projection.views={effects:{events:[target]}};
    const {box,view}=render(f);let received:any;view.navigateEvidence=(...args:any[])=>received=args;
    const mods=open(box,1);const button=mods.querySelector("button")!;button.click();expect(received[0]).toBe(target);expect(received[1]).toBe("effects");
    expect(mods.querySelector("details")?.textContent).toContain("本段未采用的属性");
    received=null;view.projection={};button.click();expect(received).toBeNull();
  });
});
