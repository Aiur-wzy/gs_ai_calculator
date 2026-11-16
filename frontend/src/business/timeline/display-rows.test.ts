import {describe,it,expect} from "vitest";
import {buildDisplayRows,foldSourceDirectories,intervalItems,packDisplayItems,itemSelection} from "./display-rows";
import {compactLaneName,displayName,eventDisplayName} from "./display-names";
import {timeAt,zoomScroll,visibleRows} from "./geometry";
const buff=(id:string,start:number,end:number,detail:any={},character="a")=>({id,kind:"coverage",start,end,character,
  sourceKey:"r",sourceLabel:"同名效果",label:"同名效果",detail:{owner:"provider",rule_id:"r",instance_id:id,...detail}});
describe("timeline display rows preserve calculation identity",()=>{
  it("shares a source row across shifted windows without filling gaps or losing members",()=>{
    const events=[buff("one",0,2),buff("two",1,3,{},"b"),buff("three",4,5)],before=JSON.stringify(events);
    const rows=buildDisplayRows(events,"effects");expect(rows).toHaveLength(1);expect(rows[0].expandable).toBe(true);
    const slices=intervalItems(rows[0].events);
    expect(slices.map(s=>[s.start,s.end,s.events.map(e=>e.id)])).toEqual([[0,1,["one"]],[1,2,["one","two"]],[2,3,["two"]],[4,5,["three"]]]);
    expect(buildDisplayRows(events,"effects",new Set([rows[0].key]))).toHaveLength(3);
    expect(JSON.stringify(events)).toBe(before);
  });
  it("same names never join different sources, owners, target areas or binding parts",()=>{
    const a=buff("a",0,3),b={...buff("b",0,3),sourceKey:"another"};
    expect(buildDisplayRows([a,b,buff("c",0,3,{owner:"other"}),buff("d",0,3,{target_id:"enemy2"}),buff("e",0,3,{area_id:"zone2"})],"effects")).toHaveLength(5);
    const binding={binding_id:"set1",part_id:"cryo"};
    expect(buildDisplayRows([buff("a",0,1,{source_bindings:[binding]}),buff("b",1,2,{source_bindings:[{...binding,part_id:"electro"}]})],"effects")).toHaveLength(2);
  });
  it("unknown source stays distinct instead of grouping by captions",()=>{
    const events=["x","y"].map(id=>({id,kind:"coverage",label:"same",character:"a",start:0,end:1}));
    expect(buildDisplayRows(events,"effects")).toHaveLength(2);
  });
  it("same-name independent rows fold only as a directory, with all separate sources expandable",()=>{
    const a=buff("a",0,2),b={...buff("b",1,3),sourceKey:"different"};
    const raw=buildDisplayRows([a,b],"effects"),folded=foldSourceDirectories(raw);expect(raw).toHaveLength(2);
    expect(folded).toHaveLength(1);expect(folded[0].kind).toBe("source_directory");expect(folded[0].sourceCount).toBe(2);
    expect(folded[0].directoryMembers).toEqual([a,b]);
    const open=foldSourceDirectories(raw,new Set([folded[0].key]));expect(open).toHaveLength(3);expect(open[0].events).toEqual([]);
    expect(open.slice(1).flatMap(r=>r.events)).toEqual([a,b]);expect(open[1].key).not.toBe(open[2].key);
    const other=buff("target",0,3,{target_id:"other"});expect(foldSourceDirectories(buildDisplayRows([a,other],"effects"))).toHaveLength(2);
  });
  it("keeps the same directory when one nested source splits into recipient rows",()=>{
    const events=[buff("first-a",0,2),buff("first-b",0,2,{},"b"),
      {...buff("second-a",1,3),sourceKey:"other"},{...buff("second-b",1,3,{},"b"),sourceKey:"other"}];
    const base=buildDisplayRows(events,"effects"),directory=foldSourceDirectories(base)[0];
    const expanded=new Set([directory.key,base[0].key]);
    const rows=foldSourceDirectories(buildDisplayRows(events,"effects",expanded),expanded);
    expect(rows[0]).toMatchObject({key:directory.key,expanded:true,sourceCount:2});
    expect(rows.slice(1).map(row=>row.events.map(event=>event.id))).toEqual([["first-a"],["first-b"],["second-a","second-b"]]);
    expect(rows.slice(1).map(row=>row.label.slice(-4))).toEqual(["来源 1","来源 1","来源 2"]);
    expect(rows[0].directoryMembers).toEqual(events);
  });
  it("keeps layers, quota exhaustion and multiple field establishments as explicit states",()=>{
    const a=buff("a",0,4,{states:[{start:0,end:1,layers:2,status:"active"},{start:1,end:3,layers:3,status:"active"},{start:3,end:4,layers:3,status:"exhausted",remaining_quota:0}]}),rows=intervalItems([a]);
    expect(rows.map(s=>[s.start,s.end,s.state?.layers,s.state?.status])).toEqual([[0,1,2,"active"],[1,3,3,"active"],[3,4,3,"exhausted"]]);
    const selected=itemSelection(rows[2]);expect(selected.detail.remaining_quota).toBe(0);expect(selected.originalEvent).toBe(a);
    const both=intervalItems([a,buff("b",0,4,{layers:7})]);expect(both[0].state?.status).toBe("mixed");expect(both[0].state?.layers).toBeNull();
  });
  it("point aggregation retains all records and is display-scale dependent",()=>{
    const points=[0,.1,.3].map((start,i)=>({id:`h${i}`,kind:"damage",start,value:i+1}));
    const small=packDisplayItems(points,50,"damage"),large=packDisplayItems(points,500,"damage");
    expect(small).toHaveLength(1);expect(large).toHaveLength(3);expect(small[0].events).toEqual(points);
    expect(small[0].events.reduce((n,p)=>n+p.value,0)).toBe(6);
  });
  it("abbreviations require exact saved identity and do not change source IDs",()=>{
    expect(displayName("翠绿之影",{entity_type:"artifact_set",database_id:15002}).short).toBe("风套");
    expect(displayName("翠绿之影",{entity_type:"artifact_set",database_id:999}).short).toBe("翠绿之影");
    const e=buff("b",0,1,{source_name:"冰抗降低",source_bindings:[{owner:{kind:"artifact_set",set_id:15002}}]}),before=JSON.stringify(e);
    expect(eventDisplayName(e).short).toBe("风套 · 冰抗降低");expect(JSON.stringify(e)).toBe(before);
  });
  it("uses the saved source first and keeps distinctions in compact labels",()=>{
    const effects=["华彩·星烁","华彩·星扩散","超越之匙·超越"].map((name,i)=>({...buff(`buff${i}`,0,2,{source_name:name}),
      sourceKey:name,sourceLabel:`角色甲 · 星烁伤害加成 · ${name}`}));
    const rows=buildDisplayRows(effects,"effects");
    expect(rows.map(row=>compactLaneName(row.shortLabel,"角色甲"))).toEqual(["华彩·星烁","华彩·星扩散","超越之匙·超越"]);
    expect(rows[0].sourceLabel).toBe("角色甲 · 星烁伤害加成 · 华彩·星烁");
    expect(compactLaneName("角色甲 · 某个很长的增益·攻击","角色甲")).toMatch(/·攻击$/);
    expect(compactLaneName("角色甲 · 易燃增益","角色甲")).toBe("易燃增益");
  });
  it("separates label coordinates from time and preserves cursor time while zooming",()=>{
    expect(timeAt(320,120,50,25)).toBe(10);
    expect(timeAt(320,120,zoomScroll(50,200,1,2),50)).toBe(10);
    expect(visibleRows(100,1000,300,52)).toEqual([17,26]);
  });
});
