import {describe,it,expect,vi} from "vitest";
import {createWhiteboardView} from "./view";
import {createWhiteboardCapacity} from "./capacity";
import {createWhiteboardAdmission} from "./admission";
import type {WhiteboardPorts} from "./ports";

function fixture(){
  const ui:any={el:(tag:string,text?:string,cls?:string)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node;},
    button:(label:string,click:()=>void,cls?:string)=>{const node=ui.el("button",label,cls);node.addEventListener("click",click);return node;},
    area:()=>ui.el("textarea"),field:(label:string,node:HTMLElement)=>{const item=ui.el("label",label);item.append(node);return item;}};
  const board={schema_version:3,version_line:"release",team:Array.from({length:4},(_,id)=>({id:String(id),name:`角色${id}`,level:90,constellation:0,weapon:{name:"武器"},artifact_sets:[{name:"套装",count:4}],panel:{atk:2000,energy_recharge:1.5}})),actions:[],capacity_intents:[],action_admission_intents:[]};
  const owner:any={state:{board,selectedActor:"0",pending:[]},colors:[],bindCommandMenu:vi.fn(),unappliedEditor:()=>false,
    actor:(id:string)=>board.team.find(c=>c.id===id),number:String,fold:(label:string,key:string)=>{const d=ui.el("details");d.open=!!owner.state[key];d.append(ui.el("summary",label));return d;}};
  const ports={owner,ui,services:{},document,query:()=>null,status:vi.fn(),api:vi.fn()} as unknown as WhiteboardPorts;
  Object.assign(owner,createWhiteboardCapacity(ports),createWhiteboardAdmission(ports),createWhiteboardView(ports));
  return {owner,box:ui.el("aside") as HTMLElement};
}
describe("compact whiteboard team details",()=>{
  it("keeps empty advanced intent controls accessible but collapsed",()=>{
    const {owner,box}=fixture();owner.renderTeam(box);
    const intents=[...box.querySelectorAll<HTMLDetailsElement>("details.wb-capacity-intents")];
    expect(intents).toHaveLength(2);expect(intents.every(item=>!item.open)).toBe(true);
    expect(intents[0]?.querySelector("summary")?.textContent).toContain("次数意图（0）");
    expect(intents[1]?.querySelector("summary")?.textContent).toContain("动作接纳意图（0）");
    expect(intents.every(item=>item.querySelector("button"))).toBe(true);
  });
  it("shows compact character cards and allows explicit detail disclosure",()=>{
    const {owner,box}=fixture();owner.renderTeam(box);
    const cards=[...box.querySelectorAll(".wb-team-card")];expect(cards).toHaveLength(4);
    expect(cards.every(card=>card.querySelectorAll("span").length===1)).toBe(true);
    expect(cards[0]?.getAttribute("title")).toContain("攻击 2000");
    const summary=[...box.querySelectorAll("details")].find(item=>item.querySelector("summary")?.textContent==="套装与面板摘要")!;
    expect(summary.open).toBe(false);summary.open=true;expect(summary.textContent).toContain("套装 4 件");
    owner.state.capacityIntentsOpen=true;const again=document.createElement("aside");owner.renderTeam(again);
    expect(again.querySelector<HTMLDetailsElement>("details.wb-capacity-intents")?.open).toBe(true);
  });
  it("restores the viewport during SVG sizing and ignores scroll events from a replaced track",()=>{
    const {owner,box}=fixture();
    Object.assign(owner.state,{mode:"actions",scale:2,scroll:260,axisScrollTop:{actions:80}});
    owner.topologyDraft=vi.fn();owner.workflowState=vi.fn();
    owner.draw=vi.fn(({left,top}={left:0,top:0})=>{
      const {svg,wrap}=owner.state;
      svg.setAttribute("width","1550");svg.setAttribute("height","600");
      wrap.scrollLeft=left;wrap.scrollTop=top;
    });
    owner.renderCenter(box);
    expect(owner.draw).toHaveBeenCalledExactlyOnceWith({left:260,top:80});
    expect(owner.state.wrap.scrollLeft).toBe(260);expect(owner.state.wrap.scrollTop).toBe(80);
    const oldTrack=owner.state.wrap;
    owner.renderCenter(document.createElement("section"));
    oldTrack.scrollLeft=0;oldTrack.scrollTop=0;oldTrack.dispatchEvent(new Event("scroll"));
    expect(owner.state.scroll).toBe(260);expect(owner.state.axisScrollTop.actions).toBe(80);
  });
});
