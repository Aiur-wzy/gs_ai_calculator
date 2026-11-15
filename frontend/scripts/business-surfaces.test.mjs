import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile} from "node:fs/promises";
import {JSDOM} from "jsdom";
async function fixture(){
  const dom=new JSDOM('<!doctype html><html><body><div id="genshin-legacy-editor" class="gs-legacy-island"><div id="workspace-menubar"><button class="workspace-menu-button">文件</button><button class="workspace-menu-button">编辑</button></div><main><button id="opener">打开</button></main><div id="app-overlay-root"></div></div><button id="outside">其他位置</button></body></html>',{runScripts:"dangerously",url:"https://business.invalid/",pretendToBeVisual:true});
  const w=dom.window;w.setStatus=()=>{};w.$=selector=>w.document.querySelector(selector);
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  for(const file of ["gui-foundation-v2.js","ui.js","workspace-kernel.js","workspace.js","context-menu.js"])w.eval(await readFile(new URL(`../../src/genshin_app/gui/web/${file}`,import.meta.url),"utf8"));
  w.WS.commands=()=>[{label:"文件",items:[{id:"save",label:"保存",enabled:true}]},{label:"编辑",items:[{id:"edit",label:"修改",enabled:true}]}];
  return {dom,w};
}
test("menubar shares one App Surface with right-click menus and closes on outside/Escape",async()=>{
  const {dom,w}=await fixture();try{
    w.WS.openMenu(0,true);const first=w.WS.menu.popup;assert.equal(w.AppSurfaceKernel.getActive(),first);assert.equal(w.document.activeElement.textContent,"保存");
    const trigger=w.document.getElementById("opener");w.AppContextMenu.forDocument().open({target:trigger,label:"白板",items:[{id:"inspect",label:"查看详情",run(){}}]});
    assert.equal(w.WS.menu,null);assert.equal(first.isConnected,false);assert.equal(w.document.querySelectorAll('[role="menu"]').length,1);
    w.document.getElementById("outside").dispatchEvent(new w.Event("pointerdown",{bubbles:true}));assert.equal(w.AppSurfaceKernel.getActive(),null);
    w.WS.openMenu(1,true);const edit=w.WS.menu.popup;edit.dispatchEvent(new w.KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}));assert.equal(w.WS.menu,null);assert.equal(w.document.querySelectorAll('[role="menu"]').length,0);assert.equal(w.document.activeElement.textContent,"编辑");
  }finally{dom.window.close();}
});
test("a busy App dialog refuses menubar replacement without losing the user's draft",async()=>{
  const {dom,w}=await fixture();try{
    const dialog=w.AppDialogKernel.create({title:"未提交的装备修改",busy:true});assert.ok(dialog);w.WS.openMenu(0,true);assert.equal(w.WS.menu,null);assert.equal(w.AppSurfaceKernel.getActive(),dialog);assert.ok(dialog.open);
    dialog.setBusy(false);dialog.finish();w.WS.openMenu(0,true);assert.ok(w.WS.menu);
  }finally{dom.window.close();}
});
test("business-only menu styles and action diagram colours use theme tokens",async()=>{
  const css=await readFile(new URL("../../src/genshin_app/gui/web/workspace.css",import.meta.url),"utf8"),equipment=await readFile(new URL("../../src/genshin_app/gui/web/equipment-workbench.css",import.meta.url),"utf8"),checks=await readFile(new URL("../../src/genshin_app/gui/web/checks.js",import.meta.url),"utf8");
  assert.match(css,/\.app-context-menu\s*\{[^}]*background:var\(--color-overlay\)/);assert.match(equipment,/\.ew-native-binding\s*\{[^}]*position:relative/);
  assert.match(checks,/fill:"var\(--color-axis-label\)"/);assert.doesNotMatch(checks,/fill:"#e6e9f0"/);
  const bridge=await readFile(new URL("../../src/genshin_app/gui/web/dsh-shell.css",import.meta.url),"utf8");
  assert.match(bridge,/\.gs-legacy-island\[data-theme-resolved\]/,"DSH aliases must have enough specificity to override legacy resolved palette definitions");
  const publicTheme=await readFile(new URL("../node_modules/@deepseek-ai/dsh-client-ui-theme/lib/client.js",import.meta.url),"utf8"),defined=new Set([...publicTheme.matchAll(/(--dsw-[\w-]+):/g)].map(match=>match[1]));
  for(const [,_all,name]of bridge.matchAll(/(var\((--dsw-[\w-]+))/g))assert.ok(defined.has(name),`Unknown pinned DSH theme token: ${name}`);
});
test("narrow editor islands display one optional pane without overwriting desktop preferences",async()=>{
  const {dom,w}=await fixture();try{
    const root=w.document.querySelector("main");root.classList.add("workspace-layout");root.innerHTML='<aside class="ws-left-pane"></aside><section class="wb-center"></section><aside class="ws-right-pane"></aside>';
    let width=600;Object.defineProperty(root,"clientWidth",{get:()=>width});const record={root,key:"whiteboard"};w.WS.layouts.set("whiteboard",record);
    const pref=w.WS.preference("whiteboard",1200);pref.leftHidden=false;pref.rightHidden=false;w.WS.applyLayout(record);
    assert.ok(root.classList.contains("workspace-left-hidden"));assert.ok(root.classList.contains("workspace-right-hidden"));
    w.WS.toggle("whiteboard","left");assert.equal(root.classList.contains("workspace-left-hidden"),false);assert.ok(root.classList.contains("workspace-right-hidden"));
    w.WS.reveal("whiteboard","right");assert.ok(root.classList.contains("workspace-left-hidden"));assert.equal(root.classList.contains("workspace-right-hidden"),false);
    width=1200;w.WS.applyLayout(record);assert.equal(root.classList.contains("workspace-compact-panes"),false);assert.equal(root.classList.contains("workspace-left-hidden"),false);assert.equal(root.classList.contains("workspace-right-hidden"),false);assert.equal(pref.leftHidden,false);assert.equal(pref.rightHidden,false);
  }finally{dom.window.close();}
});
