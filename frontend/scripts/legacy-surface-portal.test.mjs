import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {test} from "node:test";
import {JSDOM} from "jsdom";

const web=new URL("../../src/genshin_app/gui/web/",import.meta.url);
async function fixture(){
  const dom=new JSDOM(await readFile(new URL("index.html",web),"utf8"),{runScripts:"outside-only",pretendToBeVisual:true});
  const w=dom.window;
  // JSDOM has no top layer or layout; these tests assert ownership and DOM
  // contracts. The production Chromium probe separately asserts visibility.
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.HTMLElement.prototype.getClientRects=function(){return this.isConnected&&!this.closest("[hidden]")?[{}]:[];};
  w.ThemeController={current:()=>({preference:"dark",resolved:"dark"})};
  w.eval(await readFile(new URL("gui-foundation-v2.js",web),"utf8"));
  w.eval(await readFile(new URL("ui.js",web),"utf8"));
  const app=w.document.getElementById("genshin-application-root"),portal=w.document.getElementById("app-overlay-root");
  const opener=w.document.createElement("button");w.document.getElementById("dsh-app-root").append(opener);opener.focus();
  return {dom,w,app,portal,opener};
}

test("legacy dialog remains outside parked editor and makes the whole application inert",async()=>{
  const f=await fixture();try{
    assert.equal(f.portal.closest("[hidden]"),null);assert.equal(f.app.contains(f.portal),false);
    const dialog=f.w.AppDialogKernel.create({title:"保留方案"});
    assert.equal(dialog.parentNode,f.portal);assert.equal(f.app.inert,true);assert.equal(f.app.getAttribute("aria-hidden"),"true");
    assert.equal(f.portal.dataset.themeResolved,"dark");
    const action=f.w.UI.button("关闭",()=>dialog.finish());dialog.setActions(action);
    assert.equal(f.w.document.activeElement,action);
    action.dispatchEvent(new f.w.KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}));
    assert.equal(dialog.isConnected,false);assert.equal(f.app.inert,false);assert.equal(f.app.hasAttribute("aria-hidden"),false);
    assert.equal(f.w.document.activeElement,f.opener);
  }finally{f.dom.window.close();}
});

test("an official modal is preserved and blocks a competing legacy surface",async()=>{
  const f=await fixture();try{
    const official=f.w.document.createElement("section");official.setAttribute("role","dialog");official.setAttribute("aria-modal","true");f.w.document.body.append(official);
    assert.equal(f.w.AppDialogKernel.create({title:"不能盖住官方确认"}),null);
    assert.equal(f.w.AppDialogKernel.getLastCreateResult().reason,"external_modal_active");
    assert.equal(official.isConnected,true);assert.equal(f.portal.childElementCount,0);
    official.remove();assert.ok(f.w.AppDialogKernel.create({title:"官方确认已完成"}));
  }finally{f.dom.window.close();}
});

test("portal follows the official theme and native modal keeps Tab ownership",async()=>{
  const f=await fixture();try{
    const dialog=f.w.AppDialogKernel.create({title:"焦点"}),first=f.w.UI.button("第一项",()=>{}),last=f.w.UI.button("最后一项",()=>{});dialog.setActions(first,last);
    f.w.document.dispatchEvent(new f.w.CustomEvent("genshin-theme-change",{detail:{preference:"light",resolved:"light"}}));
    assert.equal(f.portal.dataset.themeResolved,"light");
    let competingHandler=0;f.w.document.addEventListener("keydown",()=>competingHandler++);
    last.focus();last.dispatchEvent(new f.w.KeyboardEvent("keydown",{key:"Tab",bubbles:true,cancelable:true}));
    assert.equal(competingHandler,0);assert.equal(f.w.document.activeElement,first);
  }finally{f.dom.window.close();}
});

test("closing native top layer yields focus to a newly arrived official modal",async()=>{
  const f=await fixture();try{
    const dialog=f.w.AppDialogKernel.create({title:"先打开的操作"});
    const official=f.w.document.createElement("section"),action=f.w.document.createElement("button");
    official.setAttribute("role","dialog");official.setAttribute("aria-modal","true");official.append(action);f.w.document.body.append(official);
    dialog.finish("complete");assert.equal(official.isConnected,true);assert.equal(f.w.document.activeElement,action);
    assert.equal(f.app.inert,false);
  }finally{f.dom.window.close();}
});

test("portal style isolation does not add the legacy scope to the DSH root",async()=>{
  const f=await fixture();try{
    assert.equal(f.w.document.getElementById("dsh-app-root").closest(".gs-legacy-island"),null);
    assert.ok(f.portal.classList.contains("gs-legacy-island"));
    const css=await readFile(new URL("dsh-shell.css",web),"utf8");
    assert.match(css,/#app-overlay-root\.gs-legacy-portal\s*\{[^}]*display:contents/);
  }finally{f.dom.window.close();}
});
