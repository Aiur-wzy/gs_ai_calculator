import {readFileSync} from "node:fs";
import {dirname, resolve} from "node:path";
import {fileURLToPath} from "node:url";
import {runInNewContext} from "node:vm";
import {describe, expect, it} from "vitest";
import {createMechanicsEditor} from "./index";
import type {MechanicsEditorPorts} from "./contracts";

const frozen=readFileSync(resolve(dirname(fileURLToPath(import.meta.url)),
  "../../../../tests/references/gui-mechanics-editor-v1.js"),"utf8");

const copy = <T,>(value:T):T => JSON.parse(JSON.stringify(value));
function fixture() {
  const el=(tag:string,text?:unknown,cls?:string)=>{const n=document.createElement(tag);if(text!=null)n.textContent=String(text);if(cls)n.className=cls;return n;};
  const control=(tag:string,value:unknown,set:(value:any)=>void)=>{const n=el(tag) as HTMLInputElement;n.value=String(value??"");n.addEventListener("change",()=>set(n.value));return n;};
  const UI={el, clone:copy,
    button:(label:string,run:()=>void)=>{const n=el("button",label);n.addEventListener("click",run);return n;},
    field:(label:string,child:Node)=>{const n=el("label",label);n.append(child);return n;},
    input:(value:unknown,set:(value:any)=>void)=>control("input",value,set),
    area:(value:unknown,set:(value:any)=>void)=>control("textarea",value,set),
    check:(label:string,value:boolean,set:(value:boolean)=>void)=>{const n=el("label",label),c=el("input") as HTMLInputElement;c.type="checkbox";c.checked=value;c.addEventListener("change",()=>set(c.checked));n.append(c);return n;},
    select:(rows:any[],value:unknown,set:(value:any)=>void)=>{const n=control("select",value,set);for(const row of rows){const o=el("option",row.label) as HTMLOptionElement;o.value=String(row.value);n.append(o);}n.value=String(value);return n;},
    details:(label:string,value:unknown)=>{const n=el("details");n.append(el("summary",label),el("pre",JSON.stringify(value)));return n;},
    error:(value:unknown)=>el("p",JSON.stringify(value)),
  };
  const template={characters:[{id:"a",name:"角色甲"}],hits:[{id:"hit",character:"a"}]};
  const ports:MechanicsEditorPorts={UI,
    PL:{state:{plan:{mechanics_model:{}}}},RT:{tpl:()=>template},
    RC:{caps:{action_types:[]}},RS:{state:{snapshot:{template}},activeSnapshot(){return this.state.snapshot;}},
    EF:{registry:()=>({benefit_types:[{id:"custom",label:"来源标签"}],damage_types:[]})},
    DS:{character:(id:string)=>id==="a"?"角色甲":id,characters:()=>[{value:"a",label:"角色甲"}],
      prose:(value:unknown,fallback:string)=>value||fallback,hit:(hit:any)=>hit.id,
      fixedSource:(id:unknown)=>id,targetTruth:(v:unknown)=>String(v)},
    extensions:{}, callApi:async()=>({code:0,data:{}}),setStatus:()=>{},$:()=>null};
  const referenceWorld:any={...ports,window:null,console,JSON,Map,Set,Object,Number};
  referenceWorld.window=referenceWorld;
  runInNewContext(frozen,referenceWorld,{filename:"frozen-mechanics-editor-v1.js"});
  return {ports,old:referenceWorld.MC,current:createMechanicsEditor(ports)};
}

describe("mechanics editor ownership migration preserves frozen behavior",()=>{
  it("keeps labels and all existing public editor methods without browser globals in the modules",()=>{
    const f=fixture();expect(copy(f.current.words)).toEqual(copy(f.old.words));
    expect(Object.keys(f.current).sort()).toEqual(Object.keys(f.old).sort());
    expect(f.current.label("custom")).toBe(f.old.label("custom"));
  });
  it.each([
    {type:"number",minimum:1},{type:["null","string"]},{enum:["a","b"]},
    {oneOf:[{type:"boolean"},{type:"null"}]},
    {type:"object",required:["id","availability"],properties:{id:{type:"string"},availability:{const:"fixed"}}},
    {type:"array",items:{type:"integer"}},
  ])("builds the same schema defaults and branch selection: %j",schema=>{
    const f=fixture();expect(copy(f.current.default(schema))).toEqual(copy(f.old.default(schema)));
    expect(f.current.branch(schema,null)).toBe(f.old.branch(schema,null));
  });
  it("renders and edits generic schema forms with identical mutations",()=>{
    const f=fixture(),schema={type:"array",items:{type:"object",required:["enabled"],properties:{enabled:{type:"boolean"}}}};
    const oldValue:any[]=[],newValue:any[]=[],a=document.createElement("section"),b=document.createElement("section");
    f.old.form(a,schema,oldValue,()=>{},"规则",()=>{});
    f.current.form(b,schema,newValue,()=>{},"规则",()=>{});
    expect(b.outerHTML).toBe(a.outerHTML);a.querySelector("button")!.click();b.querySelector("button")!.click();
    expect(newValue).toEqual(copy(oldValue));expect(newValue).toEqual([{enabled:false}]);
  });
  it.each([null,{knowledge:"unknown"},{aura:null},{aura:{element:"cryo",remaining_gu:0.83145678}},
    {ordinary_aura:{element:"hydro",remaining_gu:1},frozen:{remaining_gu:0.4},freeze_history_seconds:2},
  ])("formats elemental state without deriving new game facts: %j",state=>{
    const f=fixture();expect(f.current.elementalStateText(state)).toBe(f.old.elementalStateText(state));
  });
  it("projects full trace/result DOM from the same declared receipt and leaves its input untouched",()=>{
    const f=fixture(),result={hits:[{id:"hit",character:"a"}],mechanics_trace:[
      {t:0,type:"activation",rule_id:"r",layers:1,expires_at:[10],quota:{team:2}},
      {t:1,type:"quota",rule_id:"r",before:2,after:1},
      {t:1,type:"hit",event_id:"hit",reported_damage:true,effect_count:1},
      {t:2,type:"elemental_application",reason:"aura_applied",before:{aura:null},after:{aura:{element:"cryo",remaining_gu:1}}},
      {t:10,type:"expiry",rule_id:"r",layers_before:1,layers_after:0},
    ],sampling_trace:[],effect_rules:[{id:"r",name_zh:"来源规则"}]};
    const before=copy(result),template=f.ports.RT.tpl();
    expect(f.current.renderTrace(result,template).outerHTML).toBe(f.old.renderTrace(result,template).outerHTML);
    expect(result).toEqual(before);
  });
});
