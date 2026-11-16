import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";
import {test} from "node:test";

test("detached window passes calculation availability without inventing result data",async()=>{
  const web=new URL("../../src/genshin_app/gui/web/",import.meta.url),calls=[];
  const ctx={document:{createElement:()=>({})},TD:{build:input=>{calls.push(input);return {views:{actions:{events:[]}},characters:[]};}}};ctx.window=ctx;
  vm.createContext(ctx);vm.runInContext(await readFile(new URL("candidate-projection.js",web),"utf8"),ctx);
  const html=await readFile(new URL("agent-window.html",web),"utf8");
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],ctx);
  ctx.GenshinEditorBridge.projectCandidate({},"native",undefined,{hasCalculation:true});
  assert.equal(calls[0].current,false);assert.equal(calls[0].result,undefined);assert.equal(calls[0].resultAvailability,"not_loaded");
  const calculated={result:{summary:{total_damage:4}}};ctx.GenshinEditorBridge.projectCandidate({},"native",calculated,{hasCalculation:true});
  assert.equal(calls[1].current,true);assert.equal(calls[1].result,calculated);assert.equal(calls[1].resultAvailability,undefined);
});
