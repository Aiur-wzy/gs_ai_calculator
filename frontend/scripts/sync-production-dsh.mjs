import {copyFile,readFile,writeFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {fileURLToPath} from "node:url";
import {productionInputs, verifyEmbeddedInputs} from "./production-provenance.mjs";
import {verifyLegacyCssInputs} from "./build-legacy-island-css.mjs";
const compiled = await readFile(new URL("../dist/production/dsh-production.js", import.meta.url));
const provenance = await productionInputs(fileURLToPath(new URL("..", import.meta.url)));
verifyEmbeddedInputs(compiled, provenance);
const legacyCssInputs = await verifyLegacyCssInputs(fileURLToPath(new URL("../..", import.meta.url)),
  await readFile(new URL("../../src/genshin_app/gui/web/legacy-island.css", import.meta.url)));
await copyFile(new URL("../dist/production/dsh-production.js", import.meta.url), new URL("../../src/genshin_app/gui/web/dsh-production.js", import.meta.url));
await copyFile(new URL("../dist/production/dsh-production.css", import.meta.url), new URL("../../src/genshin_app/gui/web/dsh-production.css", import.meta.url));
const accepted=JSON.parse(await readFile(new URL("../dsh-client-accepted-manifest.json",import.meta.url),"utf8"));
const assets=await Promise.all(["dsh-production.js","dsh-production.css","legacy-island.css","dsh-shell.css","business-ui.js","business-ui.js.map","agent-window.html","json-transport.js","index.html","ui.js","workspace.js","app.js","candidate-projection.js","plans.js","app-file-actions.js","result.js","gui-foundation-v2.js","gui-foundation-tokens.css","gui-foundation-manifest.json"].map(async name=>{
  const bytes=await readFile(new URL(`../../src/genshin_app/gui/web/${name}`,import.meta.url));
  return {name,bytes:bytes.length,sha256:createHash("sha256").update(bytes).digest("hex")};
}));
await writeFile(new URL("../../src/genshin_app/gui/web/dsh-production-manifest.json",import.meta.url),JSON.stringify({contract:"genshin-dsh-production-assets-v1",dshRelease:accepted.dshRelease,acceptedSemanticSha256:accepted.semanticSha256,buildInputs:provenance,legacyCssInputs,assets},null,2)+"\n");
// Production changes dist too. Refresh the existing shared inventory after
// every child/browser generation, not only after a separate gallery build.
await import("./write-build-metadata.mjs");
