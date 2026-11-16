import {createRequire} from "node:module";
import {createHash} from "node:crypto";
import {lstat, readFile, writeFile} from "node:fs/promises";
import {resolve} from "node:path";
import {fileURLToPath} from "node:url";
const require = createRequire(import.meta.url);
const postcss = createRequire(require.resolve("vite/package.json"))("postcss");
const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const STYLE_LIST = "frontend/scripts/legacy-styles.json";
const GENERATOR = "frontend/scripts/build-legacy-island-css.mjs";
const WEB = "src/genshin_app/gui/web/";
const MARKER = "GENSHIN_LEGACY_CSS_INPUTS_V1";
const sha256 = bytes => createHash("sha256").update(bytes).digest("hex");
function parseStyles(bytes) {
  const styles = JSON.parse(bytes.toString("utf8"));
  if (!Array.isArray(styles) || !styles.length || new Set(styles).size !== styles.length
      || styles.some(name => typeof name !== "string" || !/^[a-z0-9-]+\.css$/.test(name))) {
    throw new Error("dsh_legacy_css_source_list_invalid");
  }
  return styles;
}
export const LEGACY_STYLES = parseStyles(await readFile(resolve(ROOT, STYLE_LIST)));

async function captureLegacySources(root) {
  const read = async name => {
    const path = resolve(root, name);
    if ((await lstat(path)).isSymbolicLink()) throw new Error("dsh_legacy_css_source_symlink");
    return readFile(path);
  };
  const list = await read(STYLE_LIST), styles = parseStyles(list);
  const inputs = {[GENERATOR]:sha256(await read(GENERATOR)), [STYLE_LIST]:sha256(list)};
  const sources = await Promise.all(styles.map(async name => {
    const bytes = await read(WEB + name); inputs[WEB + name] = sha256(bytes);
    return {name, bytes};
  }));
  // File completion order is not build identity. The list itself retains CSS order.
  return {sources, identity:{contract:"genshin-legacy-css-inputs-v1", styles,
    inputs:Object.fromEntries(Object.entries(inputs).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0))}};
}

export async function legacyCssInputs(root = ROOT) {
  return (await captureLegacySources(root)).identity;
}

export function readLegacyCssInputs(bytes) {
  const matches = [...bytes.toString("utf8").matchAll(/\/\*! GENSHIN_LEGACY_CSS_INPUTS_V1:([A-Za-z0-9+/=]+) \*\//g)];
  if (matches.length !== 1) throw new Error("dsh_legacy_css_embedded_identity_missing_or_duplicate");
  return JSON.parse(Buffer.from(matches[0][1], "base64").toString("utf8"));
}

export async function verifyLegacyCssInputs(root, bytes) {
  const actual = readLegacyCssInputs(bytes), expected = await legacyCssInputs(root);
  if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new Error("dsh_legacy_css_source_inputs_mismatch:rebuild_legacy_css");
  return actual;
}
export function splitSelectors(value) {
  const rows = []; let start = 0, depth = 0, quote = "";
  for (let i = 0; i < value.length; i++) {
    const c = value[i];
    if (quote) { if (c === quote && value[i-1] !== "\\") quote = ""; continue; }
    if (c === '"' || c === "'") quote = c;
    else if (c === "(" || c === "[") depth++;
    else if (c === ")" || c === "]") depth--;
    else if (c === "," && depth === 0) { rows.push(value.slice(start, i)); start = i + 1; }
  }
  rows.push(value.slice(start)); return rows;
}
export function scopeLegacyCss(css) {
  const parsed = postcss.parse(css);
  parsed.walkRules(rule => {
    let parent = rule.parent;
    while (parent) { if (parent.type === "atrule" && /keyframes$/i.test(parent.name)) return; parent = parent.parent; }
    rule.selector = splitSelectors(rule.selector).map(raw => {
      const selector = raw.trim();
      // Element-root selectors are translated, never left targeting DSH's body/html.
      const translated = selector.replace(/(^|[\s>+~])(?:html|body|:root)(?=[\s.#:\[>+~]|$)/g, "$1.gs-legacy-island");
      return translated.startsWith(".gs-legacy-island") ? translated : `:where(.gs-legacy-island) ${translated}`;
    }).join(", ");
  });
  return parsed.toString();
}
export async function buildLegacyCss(root = ROOT) {
  const {sources, identity} = await captureLegacySources(root);
  const css = sources.map(({name,bytes}) => `/* Scoped from ${name}. Do not edit generated output. */\n${scopeLegacyCss(bytes.toString("utf8"))}`).join("\n");
  if (JSON.stringify(identity) !== JSON.stringify(await legacyCssInputs(root))) throw new Error("dsh_legacy_css_inputs_changed_during_build");
  return `/*! ${MARKER}:${Buffer.from(JSON.stringify(identity)).toString("base64")} */\n${css}`;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await writeFile(resolve(ROOT, WEB + "legacy-island.css"), await buildLegacyCss(), "utf8");
}
