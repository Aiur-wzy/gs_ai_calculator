// Compatibility facade. DSH ThemeRuntime is the sole preference/DOM owner.
(() => {
  "use strict";
  let snapshot = Object.freeze({preference:"system",resolved:"light"});
  window.ThemeController = Object.freeze({
    version:2,storageKey:"genshin.theme.v1",choices:Object.freeze(["system","light","dark"]),
    current:()=>snapshot,restore:()=>snapshot,resolve:()=>snapshot.resolved,
    set(value){if(["system","light","dark"].includes(value))document.dispatchEvent(new CustomEvent("genshin-theme-request",{detail:{preference:value}}));return snapshot;},
    accept(value){snapshot=Object.freeze({...value});document.dispatchEvent(new CustomEvent("genshin-theme-change",{detail:snapshot}));},
    token(name){const node=document.getElementById("genshin-legacy-editor");return node?getComputedStyle(node).getPropertyValue(`--${String(name).replace(/^--/,"")}`).trim():"";}
  });
})();
