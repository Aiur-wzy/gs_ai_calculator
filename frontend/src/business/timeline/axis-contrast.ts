function rgb(color:string):number[]|null {
  const hex=color.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i)?.[1];
  if(hex){const expanded=hex.length===3?Array.from(hex,c=>c+c).join(""):hex;
    return [0,2,4].map(index=>parseInt(expanded.slice(index,index+2),16));}
  const channels=color.trim().match(/^rgb\(\s*(\d{1,3})[, ]+\s*(\d{1,3})[, ]+\s*(\d{1,3})\s*\)$/i);
  return channels?channels.slice(1).map(Number):null;
}

/** Use the ink with the stronger contrast against the actual marker fill. */
export function markerInk(fill:string,opacity=1,background=""):string {
  const foreground=rgb(fill),under=opacity===1?null:rgb(background);
  if(!foreground)return "#000";
  const channels=(under?foreground.map((value,index)=>value*opacity+under[index]*(1-opacity)):foreground)
    .map(value=>value/255).map(value=>value<=0.04045?value/12.92:((value+0.055)/1.055)**2.4);
  const luminance=channels[0]*0.2126+channels[1]*0.7152+channels[2]*0.0722;
  return (luminance+0.05)/0.05>=1.05/(luminance+0.05)?"#000":"#fff";
}
