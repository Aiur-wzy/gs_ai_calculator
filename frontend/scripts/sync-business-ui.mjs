import {copyFile,readFile,writeFile} from "node:fs/promises";
import {createHash} from "node:crypto";
const from=new URL("../dist/business/business-ui.js",import.meta.url),web=new URL("../../src/genshin_app/gui/web/",import.meta.url);
await copyFile(from,new URL("business-ui.js",web));
await copyFile(new URL("../dist/business/business-ui.js.map",import.meta.url),new URL("business-ui.js.map",web));
await writeFile(new URL("business-ui-manifest.json",web),JSON.stringify({contract:"genshin-business-ui-assets-v1",assets:await Promise.all(["business-ui.js","business-ui.js.map"].map(async name=>{const data=await readFile(new URL(name,web));return {name,bytes:data.length,sha256:createHash("sha256").update(data).digest("hex")};}))},null,2)+"\n");
