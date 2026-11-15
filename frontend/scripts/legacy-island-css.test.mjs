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
