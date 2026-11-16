import {describe,expect,it,vi} from "vitest";
import {createWhiteboardInspector} from "./inspector";
import {createWhiteboardProjection} from "./projection";
import {createWhiteboardSvg} from "./svg";
import type {WhiteboardPorts} from "./ports";

describe("whiteboard Buff row disclosure",()=>{
  it("opens the row controls from a colored interval and restores the right button focus",()=>{
    document.body.innerHTML=`<section id="tab-whiteboard"><svg id="labels"></svg><div id="track"><svg id="plot"></svg></div><aside class="wb-inspector"></aside></section>`;
    const query=(selector:string)=>document.querySelector(selector);
    const events=["a","b"].map(character=>({id:`buff:${character}`,character,kind:"coverage",sourceKey:"common",sourceLabel:"队伍增益",label:"队伍增益",start:1,end:4,
      detail:{owner:"a",rule_id:"common",instance_id:character}}));
    const el=(tag:string,text?:string,cls?:string)=>{const item=document.createElement(tag);if(text!=null)item.textContent=text;if(cls)item.className=cls;return item;};
    const ui={el,button:(label:string,click:()=>void,cls?:string)=>{const button=el("button",label,cls) as HTMLButtonElement;button.addEventListener("click",click);return button;}};
    const owner:any={state:{board:{team:[{id:"a",name:"甲"},{id:"b",name:"乙"}],duration:10},wrap:query("#track"),svg:query("#plot"),labelSvg:query("#labels"),
      mode:"effects",scale:1,scroll:0,result:{plan:{},result:{}}},actor:(id:string)=>({id,name:id==="a"?"甲":"乙"}),number:String,colors:["#fff","#999","#555","#000"],themeColor:(_:string,fallback:string)=>fallback,
      rawDetails:()=>el("details"),render:()=>{const inspector=query(".wb-inspector")!;inspector.replaceChildren();owner.draw();owner.renderDerived(inspector);}};
    const ports={owner,ui,query,document,services:{projection:{build:()=>({views:{effects:{events}}})},workspace:{reveal:vi.fn()}}} as unknown as WhiteboardPorts;
    Object.assign(owner,createWhiteboardProjection(ports),createWhiteboardSvg(ports),createWhiteboardInspector(ports));owner.render();
    query('#plot [data-axis-event]')!.dispatchEvent(new MouseEvent("click",{bubbles:true}));
    const recipients=Array.from(document.querySelectorAll(".wb-detail-choice"),item=>item.textContent);
    expect(recipients).toEqual(["接收：甲 · 1 → 4 秒 · 队伍增益","接收：乙 · 1 → 4 秒 · 队伍增益"]);
    const button=()=>query('.wb-inspector [data-inspector-row-toggle]') as HTMLButtonElement;
    expect(button().textContent).toBe("展开实例行");button().focus();button().click();
    expect(owner.project().lanes).toHaveLength(2);
    expect(button().textContent).toBe("合并实例行");expect(button().getAttribute("aria-expanded")).toBe("true");
    expect(document.activeElement).toBe(button());
    const chevron=query('#labels [data-axis-control]') as SVGElement;
    chevron.dispatchEvent(new MouseEvent("click",{bubbles:true}));
    expect(owner.project().lanes).toHaveLength(1);expect(button().textContent).toBe("展开实例行");
    expect(query('#labels [data-axis-control]')?.getAttribute("aria-expanded")).toBe("false");
  });
});
