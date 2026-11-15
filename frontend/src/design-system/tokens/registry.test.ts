import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";
import registry from "./registry.json";

describe("design token registry", () => {
  it("keeps light and dark semantic keys symmetric", () => {
    expect(Object.keys(registry.themes.dark).sort()).toEqual(Object.keys(registry.themes.light).sort());
  });

  it("generates every registered token into CSS", () => {
    const css = readFileSync(resolve(process.cwd(), "src/design-system/tokens/tokens.generated.css"), "utf8");
    for (const key of [...Object.keys(registry.shared), ...Object.keys(registry.themes.light)]) {
      expect(css).toContain(`--g-${key}:`);
    }
    expect(css).not.toMatch(/var\(--g-[^)]+,[^)]+\)/);
  });
});
