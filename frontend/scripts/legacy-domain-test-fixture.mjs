/** Test-only real DOM and production script loader; never bundled into the App. */
import {readFile} from "node:fs/promises";
import {createRequire} from "node:module";
import {JSDOM} from "jsdom";

const require=createRequire(import.meta.url);
const runtime=require("../../tests/helpers/gui_runtime.cjs");
export async function legacyDomainFixture(scripts,ports={}){
  if(Object.hasOwn(ports,"document"))throw new Error("The fixture requires the actual JSDOM document");
  const html=await readFile(new URL("../../src/genshin_app/gui/web/index.html",import.meta.url),"utf8");
  // outside-only parses the actual production HTML without booting DSH, making
  // HTTP requests, registering a second Agent owner or starting a desktop window.
  const dom=new JSDOM(html,{runScripts:"outside-only",url:"https://legacy-domain.invalid/",pretendToBeVisual:true});
  const context=dom.window;
  Object.assign(context,{console,TextEncoder,TextDecoder,...ports});
  context.$=selector=>context.document.querySelector(selector);
  try{
    // Reuse the same loader and real prerequisites as the maintained CJS tests.
    // business-ui is the current production bundle, never an empty stand-in.
    runtime.loadWebScripts(dom.getInternalVMContext(),["timeline-data.js",...scripts]);
  }catch(error){dom.window.close();throw error;}
  return {context,dom,close:()=>dom.window.close()};
}
