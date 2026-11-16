import {describe,it,expect} from "vitest";
import {evidenceIndex,modifierEvidence,selectionReference,resolveSelection} from "./evidence-index";
const event=(id:string,detail:any={},character="a")=>({id,kind:"coverage",character,start:1,end:3,detail:{instance_id:"instance",rule_id:"rule",buff_id:"buff",trigger_action:{id:"action"},...detail}});
const projection=(effects:any[])=>({explanationFingerprint:"result1",views:{effects:{events:effects},actions:{events:[{id:"action-row",start:0,detail:{action_id:"action"}}]}}});
describe("timeline evidence navigation",()=>{
  it("uses instance and sampling time rather than names or the current hit time",()=>{
    const a=event("a",{source_bindings:[{binding_id:"binding",owner:{kind:"weapon",weapon_id:1}}]}),p=projection([a]);
    const m={buff_id:"wrong",execution:{instance_id:"instance",rule_id:"rule",sample_t:10},sampling:{sample_t:2}};
    const found=modifierEvidence(p,m);expect(found.events).toEqual([a]);expect(found.actions[0].id).toBe("action-row");expect(found.sources[0].binding_id).toBe("binding");
    expect(evidenceIndex(p)).toBe(evidenceIndex(p));
    expect(modifierEvidence(p,{...m,sampling:{sample_t:3}}).events).toEqual([]);
  });
  it("does not promote rule-only sources to proof of a consumed Buff",()=>{
    const p=projection([event("a",{source:"source evidence"})]),found=modifierEvidence(p,{execution:{rule_id:"rule"}});
    expect(found.events).toEqual([]);expect(found.actions).toEqual([]);expect(found.sources).toEqual(["source evidence"]);expect(found.message).toBeTruthy();
  });
  it("distinguishes receivers, exact target evidence, and execution cycles",()=>{
    const p=projection([event("a",{target_id:"enemy1"}),event("b",{target_id:"enemy2"}),event("c",{},"b"),event("later",{cycle:1})]);
    const found=modifierEvidence(p,{execution:{instance_id:"instance",sample_t:2}},{character:"a",target_id:"enemy1"});
    expect(found.events.map(e=>e.id)).toEqual(["a"]);
  });
  it("permits exact manual Buff references without inventing missing rule links",()=>{
    const a=event("a",{instance_id:null,rule_id:null}),p=projection([a]);
    expect(modifierEvidence(p,{buff_id:"buff"}).events).toEqual([a]);expect(modifierEvidence(p,{name_zh:"buff"}).events).toEqual([]);
  });
  it("read-only selection refuses stale result, foreign owner and ambiguous identity",()=>{
    const a=event("a"),p=projection([a]),ref=selectionReference("doc1",p,a,"effects")!;
    expect(resolveSelection("doc1",p,ref)).toBe(a);expect(resolveSelection("doc2",p,ref)).toBeNull();
    expect(resolveSelection("doc1",{...p,explanationFingerprint:"new"},ref)).toBeNull();
    expect(resolveSelection("doc1",projection([a,event("a")]),ref)).toBeNull();expect(selectionReference("doc1",{},a,"effects")).toBeNull();
  });
});
