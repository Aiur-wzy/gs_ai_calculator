import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {test} from "node:test";
import {createRequire} from "node:module";
import {scopeLegacyCss,splitSelectors,LEGACY_STYLES} from "./build-legacy-island-css.mjs";
const require=createRequire(import.meta.url),postcss=createRequire(require.resolve("vite/package.json"))("postcss");
test("splits selector lists without breaking is/where/attribute arguments",()=>{
  assert.deepEqual(splitSelectors('button, :where(input,select),[data-x="a,b"]'),['button',' :where(input,select)','[data-x="a,b"]']);
});
test("root/control/reset selectors cannot style DSH outside the editor",()=>{
  const css=scopeLegacyCss(':root {--x:red} * {margin:0} body {color:red} :root[data-theme-resolved="light"] :where(button,input){color:black} @keyframes spin {from{opacity:0}to{opacity:1}}');
  assert.match(css,/\.gs-legacy-island\[data-theme-resolved="light"\]/);
  assert.match(css,/:where\(\.gs-legacy-island\) \*/);
  assert.match(css,/@keyframes spin \{from/);
  assert.doesNotMatch(css,/:root|\bbody\b/);
});
test("every generated business rule has explicit island ancestry",async()=>{
  for(const name of LEGACY_STYLES){
    const css=scopeLegacyCss(await readFile(new URL(`../../src/genshin_app/gui/web/${name}`,import.meta.url),'utf8'));
    postcss.parse(css).walkRules(rule=>{
      let at=rule.parent;while(at){if(at.type==='atrule'&&/keyframes$/.test(at.name))return;at=at.parent;}
      for(const selector of splitSelectors(rule.selector))assert.match(selector,/^\s*(?:\.gs-legacy-island|:where\(\.gs-legacy-island\))/i,`${name}: ${selector}`);
    });
  }
});

test("timeline canvas uses semantic background at greater specificity than the legacy ID rule",async()=>{
  const css=postcss.parse(await readFile(new URL("../../src/genshin_app/gui/web/timeline-workspace.css",import.meta.url),"utf8"));
  const rule=css.nodes.find(node=>node.type==="rule"&&node.selector===".tl-axis-shell #timeline-wrap");
  assert.ok(rule,"must override the old #timeline-wrap background, not only a low-specificity :where selector");
  assert.equal(rule.nodes.find(node=>node.prop==="background").value,"var(--color-axis-lane-even)");
  const registry=JSON.parse(await readFile(new URL("../src/design-system/tokens/registry.json",import.meta.url),"utf8"));
  assert.ok(JSON.stringify(registry).includes("color-axis-lane-even"));
});

test("selected axis and workspace controls keep paired semantic colors after scoping",async()=>{
  const name="../../src/genshin_app/gui/web/theme.css";
  const css=postcss.parse(scopeLegacyCss(await readFile(new URL(name,import.meta.url),"utf8")));
  const rule=css.nodes.find(node=>node.type==="rule"&&node.selector.includes(".timeline-filters,"));
  assert.ok(rule);
  const selectors=splitSelectors(rule.selector).map(selector=>selector.trim());
  assert.ok(selectors.includes('.gs-legacy-island :is(.timeline-tools,.timeline-modes,.timeline-filters,.wb-toolbar) button[aria-pressed="true"]'));
  assert.ok(selectors.includes('.gs-legacy-island .workspace-pane-toggle[aria-pressed="true"]'));
  assert.ok(selectors.includes('.gs-legacy-island .tl-filter-menu[open]>summary'));
  // The island ancestor is a real class here. A zero-specificity :where()
  // would lose the surface declaration to older selected control selectors.
  assert.ok(selectors.every(selector=>selector.startsWith(".gs-legacy-island ")));
  const declarations=Object.fromEntries(rule.nodes.map(node=>[node.prop,node.value]));
  assert.equal(declarations.background,"var(--color-control-selected)");
  assert.equal(declarations.color,"var(--color-text-primary)");

  const {themes}=JSON.parse(await readFile(new URL("../src/design-system/tokens/registry.json",import.meta.url),"utf8"));
  const luminance=hex=>{
    const linear=hex.match(/\w{2}/g).map(pair=>{
      const channel=parseInt(pair,16)/255;
      return channel<=0.04045?channel/12.92:((channel+0.055)/1.055)**2.4;
    });
    return linear[0]*0.2126+linear[1]*0.7152+linear[2]*0.0722;
  };
  for(const [theme,colors] of Object.entries(themes)){
    const values=[luminance(colors["color-control-selected"]),luminance(colors["color-text-primary"])];
    const contrast=(Math.max(...values)+0.05)/(Math.min(...values)+0.05);
    assert.ok(contrast>=4.5,`${theme} selected control text contrast: ${contrast.toFixed(2)}`);
  }
});
