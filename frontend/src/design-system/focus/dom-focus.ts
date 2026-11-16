/** Shared focus eligibility for App surfaces; does not own modal state. */
const tag=(node:Element)=>String(node.tagName||"").toLowerCase();
const descendants=(root:Element):HTMLElement[]=>Array.from(root.children||[]).flatMap(child=>[child as HTMLElement,...descendants(child)]);
function visible(node:HTMLElement):boolean {
  if(node.isConnected===false)return false;
  const view=node.ownerDocument?.defaultView;
  if(view?.getComputedStyle){const style=view.getComputedStyle(node);if(style.visibility==="hidden"||style.visibility==="collapse")return false;}
  for(let current:Element|null=node;current;current=current.parentElement||current.parentNode as Element|null){
    if((current as HTMLElement).hidden||(current as HTMLElement).inert||current.hasAttribute?.("inert"))return false;
    if(current!==node&&tag(current)==="details"&&!(current as HTMLDetailsElement).open){
      const summary=Array.from(current.children||[]).find(child=>tag(child)==="summary");
      if(!summary?.contains(node))return false;
    }
    if(view?.getComputedStyle&&current.nodeType===1){const style=view.getComputedStyle(current);if(style.display==="none"||style.contentVisibility==="hidden")return false;}
  }
  return true;
}
export function canReceiveFocus(node:HTMLElement|null|undefined):node is HTMLElement {
  if(!node||typeof node.focus!=="function"||!visible(node)||node.matches?.(":disabled")||(node as HTMLButtonElement).disabled)return false;
  if(tag(node)==="input"&&String((node as HTMLInputElement).type).toLowerCase()==="hidden")return false;
  if(["a","area"].includes(tag(node))&&!node.hasAttribute?.("href")&&!node.hasAttribute?.("tabindex"))return false;
  if(Number(node.tabIndex)>=0||node.hasAttribute?.("tabindex"))return true;
  // Partial test/embedded DOMs can omit tabIndex. Native browser elements use
  // their actual tabIndex above, including summary, contenteditable and links.
  return node.tabIndex===undefined&&["button","input","select","textarea","a"].includes(tag(node));
}
export function focusableElements(root:Element):HTMLElement[]{
  return descendants(root).filter(node=>canReceiveFocus(node)&&!(Number(node.tabIndex)<0))
    .map((node,index)=>({node,index,order:node.tabIndex>0?node.tabIndex:Infinity}))
    .sort((a,b)=>a.order-b.order||a.index-b.index).map(row=>row.node);
}
