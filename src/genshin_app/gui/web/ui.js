"use strict";
// Small DOM controls shared by the native plan and equipment views. No raw source HTML.
window.UI = {
  el(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text;
    if (cls) n.className = cls; return n; },
  button(text, fn, cls = "") { const n = UI.el("button", text, cls);
    n.type = "button"; n.addEventListener("click", fn); return n; },
  field(label, control) { const n = UI.el("label", null, "eq-field");
    n.append(UI.el("span", label), control); control.setAttribute("aria-label", label); return n; },
  input(value, fn, numeric = false) { const n = UI.el("input"); n.type = numeric ? "number" : "text";
    if (numeric) n.step = "any"; n.value = value ?? "";
    n.addEventListener("change", () => { const v = numeric ? (n.value.trim() ? Number(n.value) : null) : n.value;
      if (numeric && v !== null && !Number.isFinite(v)) { setStatus("数值必须为有限数字"); return; }
      fn(v); }); return n; },
  select(options, value, fn) { const n = UI.el("select");
    options.forEach(o => { const item = typeof o === "string" ? {value:o,label:o} : o;
      const opt = UI.el("option", item.label); opt.value = item.value ?? ""; n.append(opt); });
    n.value = value ?? ""; n.addEventListener("change", () => fn(n.value)); return n; },
  area(value, fn) { const n = UI.el("textarea"); n.rows = 3; n.value = value ?? "";
    n.addEventListener("change", () => fn(n.value)); return n; },
  details(label, value) { return DS.raw(label, value); },
  check(label, checked, fn) { const n = UI.el("input"); n.type = "checkbox"; n.checked = checked;
    n.addEventListener("change", () => fn(n.checked)); return UI.field(label, n); },
  clone(value) { return JSONTransport.clone(value); },
  id(prefix) { return `${prefix}.${globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}.${Math.random().toString(36).slice(2)}`}`; },
  error(r) { const message=r.message || "操作失败";
    if (window.WS?.statusCenter?.publish) window.WS.statusCenter.publish({factKey:`local.error.${message}`,kind:"error",message,details:r.details,scope:"local",projectToFooter:false});
    else setStatus(message); const box = DS.textBlock(message, "操作失败，请展开查看原始错误");
    box.className = "eq-error"; if(r.details)box.append(DS.raw("错误定位", r.details)); return box; },
  tabs(name) { document.querySelectorAll(".tab").forEach(n => n.classList.toggle("active", n.dataset.tab === name));
    document.querySelectorAll(".panel").forEach(n => n.classList.toggle("active", n.id === `tab-${name}`)); window.WS?.refresh?.(); },
};

// Shared modal shell.  It owns only presentation, close semantics and focus;
// domain drafts and authorization remain with the calling feature.
window.AppDialogKernel = (() => {
  let sequence = 0, active = null, lastCreateResult = {ok:true,status:"idle"};
  const focus = target => { if (target && target.isConnected !== false && typeof target.focus === "function") target.focus(); };
  const descendants = root => {
    const found=[];for(const child of Array.from(root?.children||[])){found.push(child,...descendants(child));}return found;
  };
  const focusable = root => descendants(root).filter(node=>{
    if(node.hidden||node.disabled||Number(node.tabIndex)<0)return false;
    const tag=String(node.tagName||"").toLowerCase();
    return ["button","input","select","textarea","a"].includes(tag)||Number(node.tabIndex)>=0;
  });
  const initialTarget = (dialog, preferred) => {
    const explicit=typeof preferred==="function"?preferred(dialog):preferred;
    if(explicit)return explicit;
    return descendants(dialog).find(node=>node.autofocus||node.hasAttribute?.("autofocus"))||focusable(dialog)[0]||dialog;
  };
  const replaceActive = () => {
    if(!active)return true;const previous=active;
    if(previous.appBusy)return false;
    const accepted=previous.requestClose?.("replaced",{allowBusy:false,restoreFocus:false});
    return accepted!==false&&active!==previous;
  };
  const create = ({title, description = "", className = "", onRequestClose = null,
    returnFocus = document.activeElement, initialFocus = null, busy = false, onClosed = null} = {}) => {
    if(!replaceActive()){
      lastCreateResult={ok:false,status:"blocked",reason:active?.appBusy?"active_busy":"active_refused_close",active};return null;
    }
    const dialog = UI.el("dialog", null, `eq-dialog app-dialog ${className}`.trim()), id = ++sequence,
      heading = UI.el("header", null, "app-dialog-header"), titleNode = UI.el("h3", title || "操作"),
      body = UI.el("section", null, "app-dialog-body"), footer = UI.el("footer", null, "app-dialog-footer");
    titleNode.id = `app-dialog-title-${id}`;heading.append(titleNode);dialog.setAttribute("aria-labelledby",titleNode.id);
    if(description){const note=UI.el("p",description,"app-dialog-description");note.id=`app-dialog-description-${id}`;
      body.append(note);dialog.setAttribute("aria-describedby",note.id);}
    footer.hidden=true;dialog.append(heading,body,footer);dialog.appBody=body;dialog.appFooter=footer;
    let finished=false,allowBusyClose=false,closedReason=null;const closeListeners=new Set();dialog.appBusy=!!busy;dialog.setAttribute("aria-busy",String(dialog.appBusy));
    // All closures, including Foundation Escape and Owner destruction, report
    // one terminal event. Decision callers must not depend on native cancel.
    dialog.onClosed=listener=>{if(finished){listener(closedReason,dialog);return ()=>{};}closeListeners.add(listener);return ()=>closeListeners.delete(listener);};
    if(typeof onClosed==="function")closeListeners.add(onClosed);
    const finalize=(reason="cancel",options={})=>{
      if(finished)return true;finished=true;dialog.appFinished=true;if(active===dialog)active=null;
      closedReason=reason;
      if(dialog.open&&typeof dialog.close==="function")dialog.close();dialog.remove();
      const listeners=[...closeListeners];closeListeners.clear();
      for(const listener of listeners){try{listener(reason,dialog);}catch(error){console.error("dialog_close_listener_failed",error);}}
      return true;
    };
    const acceptsClose=(reason="cancel")=>{
      if(finished)return true;
      if(dialog.appBusy&&!allowBusyClose)return false;
      if(typeof onRequestClose==="function"&&onRequestClose(reason,dialog)===false)return false;
      return true;
    };
    dialog.setBusy=value=>{dialog.appBusy=!!value;dialog.setAttribute("aria-busy",String(dialog.appBusy));return dialog.appBusy;};
    dialog.setBusy(busy);
    dialog.focusInitial=(preferred=initialFocus)=>{const target=initialTarget(dialog,preferred);focus(target);return target;};
    dialog.setActions=(...controls)=>{footer.replaceChildren(...controls);footer.hidden=controls.length===0;
      if(document.activeElement===dialog)dialog.focusInitial();return footer;};
    // Resolve `finish` at event time so a feature can wrap the close contract
    // (for example, to acknowledge a cancelled Agent file action) without
    // replacing the shell or bypassing focus restoration.
    dialog.addEventListener("cancel",event=>{event.preventDefault();if(dialog.appBusy)return;dialog.finish("escape",{allowBusy:false});});
    dialog.addEventListener("keydown",event=>{
      if(event.key!=="Tab")return;const choices=focusable(dialog);
      if(!choices.length){event.preventDefault();focus(dialog);return;}
      const current=document.activeElement,index=choices.indexOf(current),next=event.shiftKey
        ?(index<=0?choices.length-1:index-1):(index<0||index===choices.length-1?0:index+1);
      if(index<0||index===0&&event.shiftKey||index===choices.length-1&&!event.shiftKey){event.preventDefault();focus(choices[next]);}
    });
    // Clicking the native backdrop is deliberately non-destructive.  Features
    // with drafts must expose an explicit Cancel/Back decision instead.
    dialog.addEventListener("click",event=>{if(event.target===dialog)event.preventDefault();});
    dialog.tabIndex=-1;
    const opened=window.AppSurfaceKernel?.create?.({surface:dialog,returnFocus,kind:"modal",role:"dialog",
      ariaLabelledby:titleNode.id,removeOnClose:true,dismissOnOutside:false,initialFocus:null,
      onRequestClose:reason=>acceptsClose(reason),onClosed:reason=>finalize(reason,{restoreFocus:false})});
    if(!opened){dialog.remove();lastCreateResult={ok:false,status:"blocked",
      reason:window.AppSurfaceKernel?.getLastCreateResult?.()?.reason||"surface_unavailable",active};return null;}
    const surfaceRequest=dialog.requestClose,surfaceDestroy=dialog.destroy;
    dialog.requestClose=(reason="cancel",options={})=>{allowBusyClose=options.allowBusy===true;
      try{return surfaceRequest(reason,options);}finally{allowBusyClose=false;}};
    // `finish` is the explicit caller path, so it may close a busy dialog.
    // Ambient dismissal and modal replacement pass allowBusy:false.
    dialog.finish=(reason="complete",options={})=>dialog.requestClose(reason,{...options,allowBusy:options.allowBusy!==false});
    dialog.destroy=(reason="destroy",options={})=>surfaceDestroy(reason,{...options,restoreFocus:options.restoreFocus!==false});
    active=dialog;dialog.showModal();dialog.focusInitial();
    lastCreateResult={ok:true,status:"opened",dialog};return dialog;
  };
  return Object.freeze({create,getActive:()=>active,getLastCreateResult:()=>lastCreateResult});
})();

// One lifecycle owner for modal dialogs, overlays and drawers.  The caller
// keeps domain state; this kernel owns dismissal, focus and ARIA only.
window.AppSurfaceKernel = (() => {
  const foundation=window.GenshinGuiFoundationV2;
  if(!foundation||foundation.contract!=="genshin-gui-foundation-v2")throw new Error("gui_foundation_v2_required");
  let host=null,overlayRoot=null;
  const normalizePartialDom=(domDocument,appRoot,portal)=>{
    // Legacy embedded/test documents can expose elements without the complete
    // EventTarget/Element surface.  Supplying only missing primitives keeps the
    // Foundation host authoritative instead of reviving a second owner.
    if(typeof domDocument.addEventListener!=="function")domDocument.addEventListener=()=>{};
    if(typeof domDocument.removeEventListener!=="function")domDocument.removeEventListener=()=>{};
    for(const root of [appRoot,portal]){
      if(typeof root.setAttribute!=="function")root.setAttribute=()=>{};
      if(typeof root.removeAttribute!=="function")root.removeAttribute=()=>{};
      if(typeof root.contains!=="function")root.contains=target=>{for(let node=target;node;node=node.parentNode)if(node===root)return true;return false;};
    }
  };
  const getHost=()=>{if(host)return host;overlayRoot=document.getElementById?.("app-overlay-root")||document.body;
    const appRoot=document.querySelector?.("main")||document.body;normalizePartialDom(document,appRoot,overlayRoot);
    host=foundation.runtime().attachDom({document,appRoot,overlayRoot});return host;};
  let sequence=0,lastCreateResult={ok:true,status:"idle"};const surfaces=[];
  const current=()=>surfaces.at(-1)||null;
  const containsNode=(root,target)=>{if(!root||!target)return false;if(typeof root.contains==="function")return root.contains(target);
    for(let node=target;node;node=node.parentNode)if(node===root)return true;return false;};
  const restoreAttribute=(node,name,value)=>{if(value==null)node?.removeAttribute?.(name);else node?.setAttribute?.(name,value);};
  const create=({surface=null,trigger=null,returnFocus=trigger||document.activeElement,className="",kind="overlay",
    role="dialog",ariaLabel="",ariaLabelledby="",onRequestClose=null,onClosed=null,initialFocus=null,removeOnClose=surface==null,
    dismissOnOutside=true,dismissOnScroll=kind!=="drawer"}={})=>{
    const owned=!surface,node=surface||UI.el("section",null,className);if(className&&surface)node.classList?.add?.(...className.split(/\s+/).filter(Boolean));
    const modal=kind==="modal";
    const modalParent=!modal?[...surfaces].reverse().find(candidate=>(candidate.getAttribute?.("aria-modal")||candidate.attributes?.["aria-modal"])==="true"
      &&(containsNode(candidate,trigger)||containsNode(candidate,node)))||null:null;
    const keepCount=modalParent?surfaces.indexOf(modalParent)+1:0;
    while(surfaces.length>keepCount){const previous=current(),accepted=previous?.requestClose?.("replaced",{allowBusy:false,restoreFocus:false});
      if(accepted===false||current()===previous){lastCreateResult={ok:false,status:"blocked",reason:"active_refused_close",active:current()};return null;}}
    if(!node.id)node.id=`app-surface-${++sequence}`;node.hidden=false;node.setAttribute("role",role);node.setAttribute("aria-modal",String(modal));
    if(ariaLabel)node.setAttribute("aria-label",ariaLabel);if(ariaLabelledby)node.setAttribute("aria-labelledby",ariaLabelledby);
    const previousExpanded=trigger?.getAttribute?.("aria-expanded"),previousControls=trigger?.getAttribute?.("aria-controls");
    trigger?.setAttribute?.("aria-expanded","true");trigger?.setAttribute?.("aria-controls",node.id);
    const originalParent=node.parentNode,originalNext=node.nextSibling;
    if(!modalParent&&originalParent&&originalParent!==overlayRoot)node.remove?.();
    let finished=false;
    const restoreNode=()=>{if(removeOnClose||owned){node.remove?.();return;}node.remove?.();
      if(originalParent&&originalParent.isConnected!==false){if(originalNext&&originalNext.parentNode===originalParent&&typeof originalParent.insertBefore==="function")originalParent.insertBefore(node,originalNext);
        else originalParent.append?.(node);}node.hidden=true;};
    const surfaceHost=getHost(),opened=surfaceHost.open({id:node.id,ownerId:"legacy-app-surface",container:node,trigger:trigger||null,fallback:returnFocus||null,
      initialFocus:typeof initialFocus==="function"?initialFocus(node):initialFocus,kind:modal?"modal":kind==="drawer"?"drawer":"ephemeral",
      focusOnOpen:initialFocus!=null,
      group:modal?"modal":modalParent?"modal-child":kind==="drawer"?"right-drawer":"ephemeral",modal,
      preservePlacement:!!modalParent,dismissOnEscape:true,dismissOnOutside:modal?false:dismissOnOutside,dismissOnScroll:modal?false:dismissOnScroll,
      onRequestClose:reason=>typeof onRequestClose!=="function"||onRequestClose(reason,node)!==false,
      onClosed:reason=>{if(finished)return;finished=true;node.appFinished=true;const index=surfaces.indexOf(node);if(index>=0)surfaces.splice(index,1);
        restoreAttribute(trigger,"aria-expanded",previousExpanded==null?"false":previousExpanded);
        restoreAttribute(trigger,"aria-controls",previousControls);restoreNode();if(typeof onClosed==="function")onClosed(reason,node);}});
    if(!opened){restoreAttribute(trigger,"aria-expanded",previousExpanded);restoreAttribute(trigger,"aria-controls",previousControls);
      if(originalParent&&!node.parentNode)originalParent.append?.(node);lastCreateResult={ok:false,status:"blocked",reason:"active_refused_close",active:current()};return null;}
    node.appSurfaceParent=modalParent;
    node.requestClose=(reason="cancel",options={})=>{const ownIndex=surfaces.indexOf(node);
      while(ownIndex>=0&&surfaces.length>ownIndex+1){const child=current(),accepted=child?.requestClose?.(`parent-${reason}`,{allowBusy:false,restoreFocus:false});
        if(accepted===false||current()===child)return false;}
      return surfaceHost.requestClose(node.id,reason,{restoreFocus:options.restoreFocus!==false});};
    node.finish=node.requestClose;node.destroy=(reason="destroy",options={})=>{const ownIndex=surfaces.indexOf(node);
      while(ownIndex>=0&&surfaces.length>ownIndex+1)current()?.destroy?.(`parent-${reason}`,{restoreFocus:false});
      return surfaceHost.destroy(node.id,reason,{restoreFocus:options.restoreFocus===true});};surfaces.push(node);
    lastCreateResult={ok:true,status:"opened",surface:node};return node;
  };
  // Upgrade an existing <details> disclosure without replacing its semantic
  // trigger.  This keeps static HTML useful without JavaScript while giving
  // the live App one consistent outside-click, Escape, focus and page-change
  // lifecycle for status, filter and tool popovers.
  const bindDisclosure=(details,{panel=null,kind="popover",role="dialog",ariaLabel="",initialFocus=null,
    dismissOnOutside=true,dismissOnScroll=true,onRequestClose=null}={})=>{
    if(!details)return null;
    const children=Array.from(details.children||[]),summary=children.find(node=>String(node.tagName||"").toLowerCase()==="summary"),
      content=panel||children.find(node=>node!==summary);
    if(!summary||!content)return null;
    let surface=null,destroyed=false,syncing=false;
    const setOpen=value=>{syncing=true;details.open=!!value;summary.setAttribute("aria-expanded",String(!!value));syncing=false;};
    const open=()=>{
      if(destroyed)return null;
      if(surface&&!surface.appFinished)return surface;
      summary.setAttribute("aria-expanded","false");
      const opened=create({surface:content,trigger:summary,returnFocus:summary,kind,role,ariaLabel,
        initialFocus,removeOnClose:false,dismissOnOutside,dismissOnScroll,onRequestClose:(reason,node)=>{
          if(typeof onRequestClose==="function"&&onRequestClose(reason,node)===false)return false;
          surface=null;setOpen(false);return true;
        }});
      if(!opened){setOpen(false);return null;}
      surface=opened;setOpen(true);return opened;
    };
    const close=(reason="disclosure-close")=>{
      if(!surface||surface.appFinished){surface=null;setOpen(false);return true;}
      const current=surface,accepted=current.finish?.(reason);
      if(accepted!==false){surface=null;setOpen(false);}return accepted!==false;
    };
    const onToggle=()=>{if(syncing||destroyed)return;if(details.open)open();else close("native-close");};
    details.addEventListener("toggle",onToggle);summary.setAttribute("aria-expanded",String(!!details.open));
    const controller={open,close,getSurface:()=>surface,destroy:()=>{if(destroyed)return true;destroyed=true;
      details.removeEventListener?.("toggle",onToggle);const current=surface;surface=null;
      current?.destroy?.("binding-destroyed",{restoreFocus:false});return true;}};
    details.appSurfaceController=controller;if(details.open)open();return controller;
  };
  return Object.freeze({create,bindDisclosure,getActive:current,getLastCreateResult:()=>lastCreateResult,
    close:(reason="cancel",options={})=>current()?.requestClose?.(reason,options)??true});
})();
