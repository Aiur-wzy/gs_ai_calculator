import {copyFile,readFile,writeFile} from "node:fs/promises";
import {createHash} from "node:crypto";
await copyFile(new URL("../dist/production/dsh-production.js", import.meta.url), new URL("../../src/genshin_app/gui/web/dsh-production.js", import.meta.url));
await copyFile(new URL("../dist/production/dsh-production.css", import.meta.url), new URL("../../src/genshin_app/gui/web/dsh-production.css", import.meta.url));
const accepted=JSON.parse(await readFile(new URL("../dsh-client-accepted-manifest.json",import.meta.url),"utf8"));
const assets=await Promise.all(["dsh-production.js","dsh-production.css","legacy-island.css","dsh-shell.css","business-ui.js","business-ui.js.map"].map(async name=>{
  const bytes=await readFile(new URL(`../../src/genshin_app/gui/web/${name}`,import.meta.url));
  return {name,bytes:bytes.length,sha256:createHash("sha256").update(bytes).digest("hex")};
}));
await writeFile(new URL("../../src/genshin_app/gui/web/dsh-production-manifest.json",import.meta.url),JSON.stringify({contract:"genshin-dsh-production-assets-v1",dshRelease:accepted.dshRelease,acceptedSemanticSha256:accepted.semanticSha256,assets},null,2)+"\n");
