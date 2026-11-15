const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const web = path.join(__dirname, '../src/genshin_app/gui/web');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
const workspace = fs.readFileSync(path.join(web, 'workspace.js'), 'utf8');
const accepted=JSON.parse(fs.readFileSync(path.join(__dirname,'../frontend/dsh-client-accepted-manifest.json'),'utf8'));
const domainPlugin=fs.readFileSync(path.join(__dirname,'../frontend/src/dsh/app-domain-plugin.tsx'),'utf8');

test('DSH owns the global palette while the App facade precedes scoped business styles',()=>{
 const controller=html.indexOf('<script src="theme.js"></script>'),firstStyle=html.indexOf('<link rel="stylesheet"');
 assert.ok(controller>=0&&controller<firstStyle);
 const styles=[...html.matchAll(/<link[^>]*href="([^"]+)"/g)].map(match=>match[1]);
 assert.deepEqual(styles,['legacy-island.css','dsh-production.css','dsh-shell.css']);
 const facade=fs.readFileSync(path.join(web,'theme.js'),'utf8');
 assert.equal(facade.includes('localStorage.setItem'),false);
 assert.equal(html.includes('href="agent-chat.css"'),false);
});

test('the application exposes one navigation vocabulary with one DSH Session/Chat owner',()=>{
 for(const label of ['排轴白板','计算模型','计算报告','资料库','AI 助手'])assert.ok(html.includes(label),label);
 assert.equal((html.match(/id="dsh-app-root"/g)||[]).length,1);
 for(const module of ['@deepseek-ai/dsh-client-ui-session','@deepseek-ai/dsh-client-ui-conversation','@deepseek-ai/dsh-client-ui-chat'])
   assert.equal(accepted.entries.filter(row=>row.id===module).length,1,module);
 assert.equal(html.includes('src="agent-workbench-bootstrap.js"'),false);
});

test('activity and App pending details use official client modules and independent Inspector slots',()=>{
 assert.ok(accepted.entries.some(row=>row.id==='@deepseek-ai/dsh-client-ui-tool'));
 assert.match(domainPlugin,/ctx\.sidebarRightTabs\.register/);
 assert.match(domainPlugin,/genshin-app-operations/);assert.match(domainPlugin,/genshin-session-questions/);assert.match(domainPlugin,/genshin-app-diagnostics/);
 assert.equal(html.includes('src="agent-conversation-view.js"'),false);
});
