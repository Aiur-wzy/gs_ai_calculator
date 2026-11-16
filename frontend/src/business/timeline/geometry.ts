/** The scroll container holds only time. Labels are a separate, fixed column. */
export const TIMELINE_LABEL_WIDTH=112;
export const timelineLabelWidth=(collapsed=false)=>collapsed?32:TIMELINE_LABEL_WIDTH;
export const TIMELINE_ACTION_HEIGHT=80;
export const TIMELINE_VIEW_HEIGHT=52;
export const TIMELINE_RULER_HEIGHT=40;
export function timeAt(clientX:number,containerLeft:number,scrollLeft:number,pps:number,plotLeft=0):number {
  return (clientX-containerLeft+scrollLeft-plotLeft)/pps;
}
export function zoomScroll(scroll:number,anchor:number,oldScale:number,nextScale:number,plotLeft=0):number {
  return Math.max(0,(scroll+anchor-plotLeft)*nextScale/oldScale-anchor+plotLeft);
}
export function visibleRows(count:number,top:number,height:number,rowHeight:number,ruler=TIMELINE_RULER_HEIGHT):[number,number]{
  return [Math.max(0,Math.floor((top-ruler)/rowHeight)-1),Math.min(count,Math.ceil((top+height-ruler)/rowHeight)+1)];
}
