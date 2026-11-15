import {createHash} from "node:crypto";
import {readdir, readFile, stat, writeFile} from "node:fs/promises";
import {dirname, join, relative, resolve} from "node:path";
import {fileURLToPath} from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");

async function filesUnder(directory) {
  const result = [];
  for (const entry of await readdir(directory, {withFileTypes: true})) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) result.push(...await filesUnder(path));
    else result.push(path);
  }
  return result;
}

const assetRows = [];
for (const path of (await filesUnder(dist)).sort()) {
  if (path.endsWith("asset-manifest.json") || path.endsWith("third-party-licenses.json")) continue;
  const bytes = await readFile(path);
  assetRows.push({
    path: relative(dist, path).replaceAll("\\", "/"),
    bytes: (await stat(path)).size,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  });
}
await writeFile(join(dist, "asset-manifest.json"), `${JSON.stringify({contract: "gui-asset-manifest-v1", assets: assetRows}, null, 2)}\n`);

async function packageDirectories() {
  const store = join(root, "node_modules", ".pnpm");
  const directories = [];
  for (const virtual of await readdir(store, {withFileTypes:true})) {
    if (!virtual.isDirectory()) continue;
    const modules = join(store, virtual.name, "node_modules");
    let entries;
    try { entries = await readdir(modules, {withFileTypes:true}); }
    catch { continue; }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      if (!entry.name.startsWith("@")) directories.push(join(modules, entry.name));
      else {
        for (const scoped of await readdir(join(modules, entry.name), {withFileTypes:true})) {
          if (scoped.isDirectory()) directories.push(join(modules, entry.name, scoped.name));
        }
      }
    }
  }
  return directories;
}

const licenseRows = new Map();
for (const directory of await packageDirectories()) {
  let row;
  try { row = JSON.parse(await readFile(join(directory, "package.json"), "utf8")); }
  catch { continue; }
  if (!row.name || !row.version) continue;
  const key = `${row.name}@${row.version}`;
  licenseRows.set(key, {
    name: row.name,
    version: row.version,
    license: row.license ?? "UNKNOWN",
    homepage: row.homepage ?? null,
  });
}
const licenses = [...licenseRows.values()].sort((left, right) => left.name.localeCompare(right.name) || left.version.localeCompare(right.version));
await writeFile(join(dist, "third-party-licenses.json"), `${JSON.stringify({contract: "gui-third-party-licenses-v1", packages: licenses}, null, 2)}\n`);
