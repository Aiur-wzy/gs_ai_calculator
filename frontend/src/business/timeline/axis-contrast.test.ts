import {describe,expect,it} from "vitest";
import {markerInk} from "./axis-contrast";

function luminance(rgb:number[]){
  return rgb.map(value=>value/255).map(value=>value<=0.04045?value/12.92:((value+0.055)/1.055)**2.4)
    .reduce((sum,value,i)=>sum+value*[.2126,.7152,.0722][i],0);
}

describe("timeline marker label contrast",()=>{
  it.each([
    ["#6ed0a0",[110,208,160],"",[],1],
    ["#22855a",[34,133,90],"",[],1],
    ["#68c8a1",[104,200,161],"#203044",[32,48,68],.78],
    ["#23805c",[35,128,92],"#e7effa",[231,239,250],.78],
  ] as const)("keeps readable ink on %s against its rendered fill",(fill,channels,under,background,alpha)=>{
    const l=luminance(channels.map((channel,i)=>channel*alpha+(background[i]??0)*(1-alpha)));
    const ink=markerInk(fill,alpha,under);
    const ratio=ink==="#000"?(l+0.05)/0.05:1.05/(l+0.05);
    expect(ratio).toBeGreaterThanOrEqual(4.5);
  });
});
