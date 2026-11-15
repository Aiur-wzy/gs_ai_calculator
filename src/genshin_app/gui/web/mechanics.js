"use strict";
// Explicit legacy bindings only. Business modules own forms, sources and reports.
if (typeof window.GenshinBusinessUI?.createMechanicsEditor !== "function")
  throw new Error("mechanics_business_module_missing");
window.MC = window.GenshinBusinessUI.createMechanicsEditor({
  UI: window.UI, PL: window.PL, RT: window.RT, RC: window.RC,
  RS: window.RS, DS: window.DS, EF: window.EF,
  callApi: (...args) => callApi(...args),
  setStatus: (...args) => setStatus(...args),
  $: selector => $(selector),
  extensions: {
    get AppDialogKernel() { return window.AppDialogKernel; },
    get AM() { return window.AM; }, get EFX() { return window.EFX; },
    get RC() { return window.RC; }, get CF() { return window.CF; },
    get REM() { return window.REM; }, get RR() { return window.RR; },
    get SC() { return window.SC; }, get SI() { return window.SI; },
    get AE() { return window.AE; }
  }
});
