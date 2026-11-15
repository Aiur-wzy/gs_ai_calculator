import {describe,expect,it} from "vitest";
import {createTimelineContext} from "./context";
import {projectHits} from "./hits";
import {resolveDamageExplanation} from "./damage-detail";

function fixture() {
  const special = (id:string,kind:string,t:number,component:string) => ({id,kind,t,action_id:"cast.E",target_id:"enemy",area_id:"arena",
    ability_profile:{ability_binding:{character_id:"actor",ability_id:"ability.E",component_id:component},
      parameters:{component,element:kind === "direct_stellar_damage" ? "cryo" : "electro",
        reaction_type:kind === "direct_stellar_damage" ? "stellar_superconduct" : "lunar_electro",count:3}}});
  return {template:{characters:[{id:"actor",name:"角色甲"}],hits:[],buffs:[],loop_duration:10},
    plan:{plan_version:3,rotation_model:{actions:[{id:"cast.E",ability_id:"ability.E",start:1,occupation_end:1.5}],
      abilities:[{id:"ability.E",character:"actor",action_type_id:"action.elemental_skill",name_zh:"测试战技"}]},
      mechanics_model:{hit_events:[],hit_patterns:[],special_reaction_events:[
        special("stellar:explicit", "direct_stellar_damage",2,"channel.a"),
        special("lunar:explicit", "direct_lunar_damage",3,"channel.b")]}}};
}

function project(input:any) {
  const context = createTimelineContext(input);
  projectHits(context);
  return context;
}

function successfulInput() {
  const input:any = fixture();
  const hit = {id:"runtime-hit",character:"actor",skill:"星超导",hit_name:"星超导·能力直伤（channel.a）",
    t:2,element:"cryo",damage_system:"stellar",expected:123};
  input.current = true;
  input.result = {hits:[hit],mechanics_trace:[
    {type:"special_reaction_damage_request",event_id:"0:stellar:explicit",action_id:"cast.E",component:"channel.a",
      ability_profile:input.plan.mechanics_model.special_reaction_events[0].ability_profile,
      reaction_type:"stellar_superconduct",owner:"actor",t:2,cycle:0},
    {type:"special_reaction_damage_projection",event_id:"projection",hit_id:hit.id,request_id:"0:stellar:explicit",
      action_id:"cast.E",t:2,cycle:0,status:"executed"},
    {type:"reaction_damage",event_id:hit.id,special_reaction_request_id:"0:stellar:explicit",action_id:"cast.E",
      t:2,cycle:0,status:"executed",reported_damage:true}],special_reactions:{trace:[]}};
  return input;
}

describe("explicit special damage and its parent ability",()=>{
  it("keeps current identity when capacity omits the explanation reference",()=>{
    const input:any=successfulInput();input.result.damage_explanation_fingerprint="current";
    input.result.damage_explanations={contract:"damage-explanation-v1",schema_version:1,status:"partial",events:{}};
    const out=project(input), row=out.views.damage.events.find(row=>row.kind==="damage"&&row.value===123)!;
    expect(row.detail.explanation_ref).toBe(null);
    expect(row.detail.explanation_fingerprint).toBe("current");
    expect(resolveDamageExplanation(out,row.detail).status).toBe("omitted");
  });
  it.each(["manual", "native_empty_metadata"])("keeps ordinary %s hits without a descriptor readable",kind=>{
    const template={meta:{},characters:[{id:"actor",name:"角色甲"}],buffs:[],loop_duration:10,
      hits:[{id:"ordinary",t:2,character:"actor",skill:"普通战技",hit_name:"普通命中",
        damage_system:"classic",element:"pyro",damage_type_ids:["damage.elemental_skill"],extensions:null}]};
    const input:any=kind === "manual" ? {template} : {template:{manual_template:template},
      plan:{plan_version:2,manual_template:template,rotation_model:{},mechanics_model:{}}};
    const before=JSON.stringify(input), out=project(input), rows=out.views.damage.events;
    expect(rows).toHaveLength(1);expect(rows[0].sourceLabel).toBe("角色甲 · 普通战技");
    expect(rows[0].label).toBe("普通命中（待计算）");
    expect(rows[0].detail.source_lineage).toMatchObject({character:"actor",action_id:null,ability_id:null,component_id:null});
    expect(out.hitLineage(template.hits[0],null).request_id).toBe(null);
    expect(JSON.stringify(input)).toBe(before);
  });
  it("previews each explicit stellar/lunar declaration once without numeric damage or runtime layers",()=>{
    const input=fixture(), before=JSON.stringify(input), out=project(input);
    const rows=out.views.damage.events;
    expect(rows).toHaveLength(2);expect(rows.map(row=>row.start)).toEqual([2,3]);
    expect(rows.every(row=>row.label.includes("待计算"))).toBe(true);
    expect(rows.every(row=>row.sourceLabel === "角色甲 · 测试战技")).toBe(true);
    expect(rows.map(row=>row.detail.source_lineage.component_id)).toEqual(["channel.a","channel.b"]);
    expect(new Set(rows.map(row=>row.sourceKey)).size).toBe(1);
    expect(new Set(rows.map(row=>row.componentKey)).size).toBe(2);
    expect(rows[0].componentLabel).toBe("测试战技 · 分量（channel.a）");
    expect(rows[1].componentLabel).toBe("测试战技 · 分量（channel.b）");
    for (const row of rows) {
      expect(row).not.toHaveProperty("value");expect(row.detail).not.toHaveProperty("layers");
      expect(row.detail).not.toHaveProperty("system_factor");expect(row.detail.execution_status).toBe("not_calculated");
    }
    expect(JSON.stringify(input)).toBe(before);
  });
  it("does not infer unknown times, components, parents, periodic counts, or mismatched owners",()=>{
    const input:any=fixture(), events=input.plan.mechanics_model.special_reaction_events;
    events[0].t=null;delete events[1].ability_profile.parameters.component;
    delete events[1].ability_profile.ability_binding.component_id;
    expect(project(input).views.damage.events).toHaveLength(0);
    const wrong:any=fixture();wrong.plan.mechanics_model.special_reaction_events[0].ability_profile.ability_binding.character_id="other";
    wrong.plan.mechanics_model.special_reaction_events[1].action_id="unknown";
    expect(project(wrong).views.damage.events).toHaveLength(0);
  });
  it("includes a known delayed declaration in the automatic display extent",()=>{
    const input:any=fixture();delete input.template.loop_duration;
    input.plan.mechanics_model.special_reaction_events[1].t=20;
    expect(project(input).views.damage.events.map(row=>row.start)).toContain(20);
  });
  it("does not preview a parent action excluded from the displayed execution window",()=>{
    const input:any=fixture();input.plan.rotation_model.actions[0].repeat={mode:"cycles",cycles:[1]};
    expect(project(input).views.damage.events).toHaveLength(0);
  });
  it("uses actual projection/request references to replace the successful preview and retain E/component provenance",()=>{
    const input=successfulInput(), before=JSON.stringify(input), out=project(input);
    const damage=out.views.damage.events.filter(row=>row.kind==="damage" && row.start===2);
    expect(damage).toHaveLength(1);expect(damage[0].value).toBe(123);
    expect(damage[0].sourceLabel).toBe("角色甲 · 测试战技");
    expect(damage[0].label).toContain("测试战技");expect(damage[0].label).not.toContain("channel.a");
    expect(damage[0].detail.source_lineage.component_id).toBe("channel.a");
    expect(damage[0].detail.action_id).toBe("cast.E");expect(damage[0].element).toBe("cryo");
    expect(damage[0].detail.preview).not.toBe(true);expect(JSON.stringify(input)).toBe(before);
  });
  it("does not let a later internal check cycle overwrite first-window special provenance",()=>{
    const input=successfulInput();
    input.result.mechanics_trace.push({type:"special_reaction_damage_request",event_id:"0:stellar:explicit",
      action_id:"other.Q",component:"wrong",owner:"other",t:2,cycle:1},
    {type:"special_reaction_damage_projection",event_id:"later",hit_id:"runtime-hit",request_id:"later-request",
      action_id:"other.Q",t:2,cycle:1,status:"executed"});
    const row=project(input).views.damage.events.find(row=>row.kind==="damage" && row.start===2)!;
    expect(row.sourceLabel).toBe("角色甲 · 测试战技");
    expect(row.detail.source_lineage).toMatchObject({action_id:"cast.E",ability_id:"ability.E",component_id:"channel.a"});
  });
  it.each(["missed","rejected","suppressed","failed"])("shows a real %s receipt without inventing damage or keeping a pending preview",status=>{
    const input:any=fixture();input.current=true;
    input.result={hits:[],mechanics_trace:[{type:"special_reaction_damage_request",event_id:"0:stellar:explicit",status,t:2,cycle:0}]};
    const out=project(input), damage=out.views.damage.events.find(row=>row.start===2)!;
    expect(damage.detail.execution_status).toBe(status);expect(damage.detail.preview).toBe(false);
    expect(damage).not.toHaveProperty("value");expect(damage.label).not.toContain("待计算");
    expect(out.views.damage.events.filter(row=>row.start===2)).toHaveLength(1);
  });
  it("does not call an unrecorded suffix executed after an interrupted current result",()=>{
    const input:any=fixture();input.current=true;input.result={hits:[],mechanics_trace:[]};
    const rows=project(input).views.damage.events;
    expect(rows.every(row=>row.detail.execution_status === "not_verified")).toBe(true);
    expect(rows.every(row=>row.label.includes("未验证") && !row.label.includes("待计算"))).toBe(true);
  });
  it("uses fresh input previews rather than a stale result",()=>{
    const input=successfulInput();input.current=false;
    const rows=project(input).views.damage.events;
    expect(rows).toHaveLength(2);expect(rows.every(row=>row.label.includes("待计算"))).toBe(true);
    expect(rows.every(row=>row.value === undefined)).toBe(true);
  });
  it("uses explanation lineage when runtime model/extensions are absent and carries only references",()=>{
    const input:any=fixture();input.current=true;
    input.plan.mechanics_model.special_reaction_events=[];
    input.result={hits:[{id:"opaque",t:2,character:"actor",skill:"星超导",hit_name:"星超导·能力直伤（x）",
      element:"cryo",damage_system:"stellar",expected:5,explanation_ref:"explanation"}],
      mechanics_trace:[],damage_explanation_fingerprint:"digest",
      damage_explanations:{contract:"damage-explanation-v1",schema_version:1,
        events:{explanation:{source_ref:"source"}},sources:{source:{character:"actor",action_id:"cast.E",
          ability_id:"ability.E",component_id:"channel.a",source_event_id:"explicit",component_name_zh:"后台追击"}}}};
    const out=project(input), row=out.views.damage.events.find(row=>row.kind==="damage")!;
    expect(out.explanationDocument).toBe(input.result.damage_explanations);
    expect(row.label).toContain("后台追击");expect(row.sourceLabel).toBe("角色甲 · 测试战技");
    expect(row.detail).toMatchObject({explanation_ref:"explanation",explanation_fingerprint:"digest"});
    expect(row.detail).not.toHaveProperty("damage_explanations");
    expect(row.detail).not.toHaveProperty("sources");
  });
  it("does not mistake a reaction classification for an unknown parent skill",()=>{
    const input:any=fixture();input.current=true;input.plan.mechanics_model.special_reaction_events=[];
    input.result={hits:[{id:"old",t:2,character:"actor",skill:"星超导",hit_name:"星超导·能力直伤（opaque）",
      damage_system:"stellar",element:"cryo",expected:5}],mechanics_trace:[]};
    const row=project(input).views.damage.events.find(row=>row.kind==="damage")!;
    expect(row.sourceLabel).toBe("角色甲 · 技能来源未保存");
    expect(row.detail.source_lineage.ability_id).toBe(null);
    expect(row.detail.source_lineage.component_id).toBe(null);
  });
});
