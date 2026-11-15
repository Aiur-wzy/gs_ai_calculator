import {createHash} from "node:crypto";
import {copyFile, mkdir} from "node:fs/promises";
import {readFile, writeFile} from "node:fs/promises";
import {dirname, resolve} from "node:path";
import {fileURLToPath} from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const web = resolve(root, "../src/genshin_app/gui/web");
await mkdir(web, {recursive: true});
await copyFile(resolve(root, "dist/legacy/gui-foundation-v2.js"), resolve(web, "gui-foundation-v2.js"));
await copyFile(resolve(root, "src/design-system/tokens/tokens.generated.css"), resolve(web, "gui-foundation-tokens.css"));
const assets = [];
for (const name of ["gui-foundation-v2.js", "gui-foundation-tokens.css"]) {
  const bytes = await readFile(resolve(web, name));
  assets.push({name, bytes:bytes.length, sha256:createHash("sha256").update(bytes).digest("hex")});
}
await writeFile(resolve(web, "gui-foundation-manifest.json"), `${JSON.stringify({contract:"gui-foundation-production-assets-v1",assets}, null, 2)}\n`, "utf8");
