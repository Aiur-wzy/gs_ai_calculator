import {readFile, writeFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";

const registryPath = fileURLToPath(new URL("../src/design-system/tokens/registry.json", import.meta.url));
const outputPath = fileURLToPath(new URL("../src/design-system/tokens/tokens.generated.css", import.meta.url));
const registry = JSON.parse(await readFile(registryPath, "utf8"));

const declarations = entries => Object.entries(entries)
  .sort(([left], [right]) => left.localeCompare(right))
  .map(([name, value]) => `  --g-${name}: ${value};`)
  .join("\n");
const themeDeclarations = entries => Object.entries(entries)
  .sort(([left], [right]) => left.localeCompare(right))
  .flatMap(([name, value]) => [`  --g-${name}: ${value};`, `  --${name}: var(--g-${name});`])
  .join("\n");
const aliasDeclarations = aliases => Object.entries(aliases)
  .sort(([left], [right]) => left.localeCompare(right))
  .map(([name, target]) => `  --${name}: var(--g-${target});`)
  .join("\n");

const lightKeys = Object.keys(registry.themes.light).sort();
const darkKeys = Object.keys(registry.themes.dark).sort();
if (JSON.stringify(lightKeys) !== JSON.stringify(darkKeys)) throw new Error("theme_token_key_mismatch");

const css = `/* Generated from registry.json. Do not edit by hand. */
:root {
${declarations(registry.shared)}
${aliasDeclarations(registry.aliases)}
}

:root,
:root[data-theme="dark"],
:root[data-theme="system"] {
${themeDeclarations(registry.themes.dark)}
  --theme-color-scheme: dark;
  color-scheme: dark;
}

:root[data-theme="light"],
:root[data-theme="system"][data-theme-resolved="light"] {
${themeDeclarations(registry.themes.light)}
  --theme-color-scheme: light;
  color-scheme: light;
}

@media (prefers-color-scheme: light) {
  :root:not([data-theme]),
  :root[data-theme="system"] {
${themeDeclarations(registry.themes.light)}
    --theme-color-scheme: light;
    color-scheme: light;
  }
}
`;

await writeFile(outputPath, css, "utf8");
