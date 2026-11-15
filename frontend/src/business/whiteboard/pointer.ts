import {type WhiteboardPorts} from "./ports";
export function createWhiteboardPointer(ports:WhiteboardPorts){
  const WB=ports.owner,$=ports.query,setStatus=ports.status,services=ports.services;
const beginPointer =(e:any,id:any)=>{
  if(e.button!==0&&e.button!==1)return;if(id&&WB.state.mode!=="actions")return;
  e.preventDefault();e.stopPropagation();const wrap=WB.state.wrap;WB.state.dragMoved=false;
  WB.state.pointer={id,board:WB.state.board,revision:WB.state.board.revision,pointerId:e.pointerId,x:e.clientX,scroll:wrap.scrollLeft,
    original:id?WB.action(id)?.start:null,next:null};wrap.setPointerCapture?.(e.pointerId);
};
const movePointer =(e:any)=>{
  const drag=WB.state.pointer;if(!drag||drag.pointerId!==e.pointerId)return;const delta=e.clientX-drag.x;
  if(Math.abs(delta)>3)WB.state.dragMoved=true;
  if(drag.id){drag.next=WB.clampTime(drag.original+delta/WB.state.geometry.pps);WB.draw();setStatus(`释放动作将移动到 ${WB.number(drag.next)} 秒`);}
  else WB.state.wrap.scrollLeft=Math.max(0,drag.scroll-delta);
};
const endPointer =(e:any,cancel=false)=>{
  const drag=WB.state.pointer;if(!drag||drag.pointerId!==e.pointerId)return;WB.state.pointer=null;
  WB.state.wrap.releasePointerCapture?.(e.pointerId);
  if(cancel||drag.board!==WB.state.board||drag.revision!==WB.state.board.revision)return;
  if(drag.id){WB.state.selectedAction=drag.id;WB.state.selectedActor=WB.action(drag.id)?.character;services.workspace?.reveal?.("whiteboard","right");
    if(drag.next!=null&&WB.state.dragMoved){WB.setTime(drag.id,drag.next);return;}WB.render();}
  else if(!WB.state.dragMoved){const rect=WB.state.wrap.getBoundingClientRect();WB.state.cursor=WB.clampTime((e.clientX-rect.left+WB.state.wrap.scrollLeft-WB.state.geometry.left)/WB.state.geometry.pps);WB.render();}
};

  return {beginPointer,movePointer,endPointer};
}
