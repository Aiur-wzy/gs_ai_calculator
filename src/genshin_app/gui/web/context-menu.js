(function (global) {
  "use strict";

  // One reusable, document-scoped context-menu owner. Consumers provide only
  // immutable command descriptions; this module owns popup lifetime, focus,
  // keyboard navigation and viewport-safe placement.
  const instances = new WeakMap();
  const MARGIN = 8;

  const editable = node => {
    const tag = String(node?.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || node?.isContentEditable === true;
  };
  const menuKey = event => event?.key === "ContextMenu" || (event?.key === "F10" && event.shiftKey === true) || event?.keyCode === 93;

  function create(options = {}) {
    const documentObject = options.document || global.document;
    const windowObject = options.window || global;
    if (!documentObject?.createElement || !documentObject?.body) throw new TypeError("context_menu_document_invalid");
    const surfaceKernel = options.surfaceKernel || global.AppSurfaceKernel;
    if (!surfaceKernel?.create) throw new TypeError("context_menu_surface_kernel_required");
    let active = null;

    const viewport = () => ({
      width: Math.max(1, Number(windowObject.innerWidth) || Number(documentObject.documentElement?.clientWidth) || 1024),
      height: Math.max(1, Number(windowObject.innerHeight) || Number(documentObject.documentElement?.clientHeight) || 768),
    });

    function close({restoreFocus = false} = {}) {
      if (!active) return false;
      const current = active;
      return current.popup.finish?.("context-menu-close", {restoreFocus}) !== false;
    }

    function place(current) {
      const {popup, target, point} = current;
      const screen = viewport();
      popup.style.maxHeight = `${Math.max(48, screen.height - MARGIN * 2)}px`;
      const width = Math.max(1, Number(popup.offsetWidth) || 220);
      const height = Math.max(1, Math.min(Number(popup.offsetHeight) || 240, screen.height - MARGIN * 2));
      const rect = target?.getBoundingClientRect?.() || {left:MARGIN,top:MARGIN,right:MARGIN,bottom:MARGIN};
      const preferredLeft = Number.isFinite(point?.x) ? point.x : rect.left;
      const preferredTop = Number.isFinite(point?.y) ? point.y : rect.bottom + 4;
      let left = preferredLeft;
      let top = preferredTop;
      if (left + width > screen.width - MARGIN) left = Number.isFinite(point?.x) ? preferredLeft - width : screen.width - MARGIN - width;
      if (top + height > screen.height - MARGIN) top = Number.isFinite(point?.y) ? preferredTop - height : rect.top - height - 4;
      left = Math.max(MARGIN, Math.min(left, screen.width - MARGIN - width));
      top = Math.max(MARGIN, Math.min(top, screen.height - MARGIN - height));
      popup.style.left = `${Math.round(left)}px`;
      popup.style.top = `${Math.round(top)}px`;
      popup.style.visibility = "visible";
    }

    function open(config = {}) {
      const target = config.target;
      if (!target) return false;
      close();
      const rows = Array.isArray(config.items) ? config.items : [];
      const popup = documentObject.createElement("div");
      popup.className = config.className || "app-context-menu";
      popup.setAttribute("role", "menu");
      popup.setAttribute("aria-label", String(config.label || "操作"));
      popup.style.visibility = "hidden";
      const enabled = [];
      for (const item of rows) {
        if (!item || item.type === "separator") {
          const separator = documentObject.createElement("div");
          separator.className = "app-context-menu-separator";
          separator.setAttribute("role", "separator");
          popup.append(separator);
          continue;
        }
        const button = documentObject.createElement("button");
        button.type = "button";
        button.className = `app-context-menu-item${item.danger === true ? " is-danger" : ""}`;
        button.setAttribute("role", "menuitem");
        button.dataset.command = String(item.id || "");
        const allowed = item.enabled !== false && typeof item.run === "function";
        button.disabled = !allowed;
        button.tabIndex = -1;
        button.setAttribute("aria-disabled", String(!allowed));
        const label = documentObject.createElement("span");
        label.textContent = String(item.label || "操作");
        button.append(label);
        if (!allowed && item.disabledReason) {
          const reason = documentObject.createElement("small");
          reason.textContent = String(item.disabledReason);
          button.title = String(item.disabledReason);
          button.setAttribute("aria-label", `${item.label || "操作"}（${item.disabledReason}）`);
          button.append(reason);
        }
        button.addEventListener("click", event => {
          event.preventDefault?.();
          event.stopPropagation?.();
          if (!allowed) return;
          const run = item.run;
          close({restoreFocus:true});
          try {
            const pending = run();
            if (pending && typeof pending.catch === "function") pending.catch(error => config.onError?.(error, item));
          } catch (error) { config.onError?.(error, item); }
        });
        popup.append(button);
        if (allowed) enabled.push(button);
      }
      if (!popup.children?.length) return false;

      const trigger = config.trigger || target;
      const current = {popup,target,trigger,returnFocus:config.returnFocus || trigger,point:config.point || null};
      popup.addEventListener("keydown", event => {
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
          event.preventDefault?.();
          if (!enabled.length) return;
          const index = enabled.indexOf(documentObject.activeElement);
          const next = event.key === "Home" ? 0 : event.key === "End" ? enabled.length - 1
            : (index + (event.key === "ArrowDown" ? 1 : -1) + enabled.length) % enabled.length;
          enabled[next]?.focus?.();
        } else if (event.key === "Enter" || event.key === " ") {
          const item = enabled.find(row => row === documentObject.activeElement);
          if (item) { event.preventDefault?.(); item.click?.(); }
        } else if (event.key === "Escape" && !event.defaultPrevented) {
          event.preventDefault?.(); close({restoreFocus:true});
        } else if (event.key === "Tab") {
          close({restoreFocus:true});
        }
      });
      const opened=surfaceKernel.create({surface:popup,trigger,returnFocus:current.returnFocus,kind:"popover",role:"menu",
        ariaLabel:String(config.label||"操作"),initialFocus:config.focus===false?null:enabled[0]||null,removeOnClose:true,
        dismissOnOutside:true,dismissOnScroll:true,onRequestClose:()=>true,onClosed:()=>{if(active===current)active=null;}});
      if(!opened)return false;
      active=current;place(current);
      return true;
    }

    function bind(target, config = {}) {
      if (!target?.addEventListener) throw new TypeError("context_menu_target_invalid");
      target.setAttribute?.("aria-haspopup", "menu");
      target.setAttribute?.("aria-expanded", "false");
      const show = (event, keyboard) => {
        if (!keyboard && editable(event.target) && event.target !== target) return;
        event.preventDefault?.();
        event.stopPropagation?.();
        const items = typeof config.items === "function" ? config.items() : config.items;
        open({
          target,
          trigger:config.trigger?.(event, keyboard) || target,
          returnFocus:keyboard ? (event.target || target) : (config.returnFocus?.(event, keyboard) || target),
          point:keyboard ? null : {x:event.clientX,y:event.clientY},
          items,
          label:typeof config.label === "function" ? config.label() : config.label,
          onError:config.onError,
        });
      };
      const context = event => show(event, false);
      const keydown = event => { if (menuKey(event)) show(event, true); };
      target.addEventListener("contextmenu", context);
      target.addEventListener("keydown", keydown);
      return () => {
        target.removeEventListener?.("contextmenu", context);
        target.removeEventListener?.("keydown", keydown);
        if (active?.target === target) close();
      };
    }

    return Object.freeze({open, close, bind, isOpen:() => active !== null, activeTarget:() => active?.target || null});
  }

  function forDocument(documentObject = global.document, windowObject = global) {
    if (!documentObject || (typeof documentObject !== "object" && typeof documentObject !== "function")) throw new TypeError("context_menu_document_invalid");
    let value = instances.get(documentObject);
    if (!value) { value = create({document:documentObject,window:windowObject}); instances.set(documentObject,value); }
    return value;
  }

  global.AppContextMenu = Object.freeze({create,forDocument,menuKey});
})(window);
