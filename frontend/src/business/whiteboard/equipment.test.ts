import {expect,it,vi} from "vitest";
import {createWhiteboardEquipment} from "./equipment";
import type {WhiteboardPorts} from "./ports";

it("blocks duplicate equipment submissions and exposes retry after a failed check",async()=>{
  const actor={id:"actor",name:"角色",level:90};
  const board={team:[actor],version_line:"release"};
  const dialog=document.createElement("dialog");
  const body=document.createElement("section");dialog.append(body);
  Object.assign(dialog,{appBody:body});
  let reply!:(value:unknown)=>void;
  const api=vi.fn(()=>new Promise(resolve=>{reply=resolve;}));
  const owner={state:{board},actor:()=>actor,dialog:()=>dialog,token:()=>board,current:()=>true,
    closeDialog:vi.fn(),change:vi.fn(),recalculateEquipment:vi.fn()};
  const ui={el:(tag:string,title?:string,css?:string)=>{const node=document.createElement(tag);node.textContent=title||"";if(css)node.className=css;return node;},
    button:(name:string,fn:()=>void)=>{const node=document.createElement("button");node.textContent=name;node.addEventListener("click",fn);return node;},
    error:()=>document.createTextNode("检查失败"),clone:structuredClone};
  const editor={validate:()=>[],draft:()=>({...actor,level:80})};
  const equipment=createWhiteboardEquipment({owner,ui,api,status:vi.fn(),
    services:{equipment:{mount:()=>editor}}} as unknown as WhiteboardPorts);
  equipment.editCharacter(actor.id);
  const [apply,save]=body.querySelectorAll<HTMLButtonElement>(".ew-actions button");
  apply.click();expect(apply.disabled).toBe(true);expect(save.disabled).toBe(true);
  save.dispatchEvent(new MouseEvent("click"));expect(api).toHaveBeenCalledOnce();
  reply({code:1});await Promise.resolve();
  expect(body.querySelector('[role="status"]')?.textContent).toBe("检查失败");
  expect(apply.disabled).toBe(false);expect(save.disabled).toBe(false);
  expect(owner.change).not.toHaveBeenCalled();
  save.click();reply({code:0});await Promise.resolve();
  expect(owner.change).toHaveBeenCalledOnce();expect(owner.recalculateEquipment).not.toHaveBeenCalled();
});
