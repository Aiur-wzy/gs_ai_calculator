// Compatibility facade; the pure projection stages live in the typed business bundle.
"use strict";
window.TD = {
  build:(input={})=>GenshinBusinessUI.buildTimelineProjection({...input,
    describeEffect:window.EFX ? (event,context)=>window.EFX.describe(event,context) : undefined}),
  groupCoverage:GenshinBusinessUI.groupCoverage,
  stateName:GenshinBusinessUI.stateName,endReason:GenshinBusinessUI.endReason,
};
