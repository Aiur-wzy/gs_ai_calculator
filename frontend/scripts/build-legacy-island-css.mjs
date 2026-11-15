import {createRequire} from "node:module";
import {readFile, writeFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";
const require = createRequire(import.meta.url);
const postcss = createRequire(require.resolve("vite/package.json"))("postcss");
const web = new URL("../../src/genshin_app/gui/web/", import.meta.url);
export const LEGACY_STYLES = ["style.css", "whiteboard.css", "workspace.css", "timeline-workspace.css", "equipment-workbench.css", "gui-foundation-tokens.css", "theme.css"];
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
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const sources = await Promise.all(LEGACY_STYLES.map(async name => `/* Scoped from ${name}. Do not edit generated output. */\n${scopeLegacyCss(await readFile(new URL(name, web), "utf8"))}`));
  await writeFile(new URL("legacy-island.css", web), sources.join("\n"), "utf8");
}
