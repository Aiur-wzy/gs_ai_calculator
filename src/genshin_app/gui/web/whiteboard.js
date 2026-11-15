// Human-authored intent only. Derived events are projected from a verified return package.
"use strict";
window.WB = {
  state:{board:null,path:"",fileSha:null,savedJson:null,request:0,selectedActor:null,selectedAction:null,
    mode:"actions",effectFilter:"all",damageFocus:"events",scale:1,scroll:0,result:null,resultPath:null,pending:[],cursor:0,page:0,closing:false,
    agentPayloadRevision:0,agentBoardGeneration:0,agentResultGeneration:0,agentPayloadCache:null},
  types:[
    ["action.normal_attack","普通攻击"],["action.charged_attack","重击"],["action.plunging_attack","下落攻击"],
    ["action.elemental_skill","元素战技"],["action.elemental_burst","元素爆发"],["action.dash","冲刺"],
    ["action.jump","跳跃"],["action.switch","切换角色"],["action.wait","等待"]],
  colors:["#7ec4da","#ba99e8","#edbb78","#86c8a0"],
  panelFields:[["atk","攻击力"],["hp","生命值"],["def","防御力"],["em","元素精通"],
    ["crit_rate","暴击率 %",true],["crit_dmg","暴击伤害 %",true],["energy_recharge","元素充能效率 %",true]],
  elements:[["all","全伤害"],["physical","物理"],["pyro","火"],["hydro","水"],["electro","雷"],
    ["cryo","冰"],["anemo","风"],["geo","岩"],["dendro","草"]],
};
const whiteboardPorts={owner:WB,ui:UI,document,events:window,query:selector=>$(selector),
 api:(method,...args)=>callApi(method,...args),status:(...args)=>setStatus(...args),
 plansDir:()=>RT.state.plansDir,confirm:message=>window.confirm(message),clipboard:()=>window.navigator?.clipboard,
 services:{get transport(){return window.JSONTransport;},get workbench(){return window.AgentWorkbenchApp;},
  get contextMenu(){return window.AppContextMenu;},get dialog(){return window.AppDialogKernel;},get workspace(){return window.WS;},
  get plan(){return window.PL;},get theme(){return window.ThemeController;},get display(){return window.DS;},get effects(){return window.EFX;},
  get projection(){return window.TD;},get equipment(){return window.EW;},get capacity(){return window.CAP;}}};
Object.assign(WB,GenshinBusinessUI.createWhiteboardBusiness(whiteboardPorts));
WB.refreshThemeColors();
