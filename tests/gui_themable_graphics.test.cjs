const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const web = path.join(__dirname, '../src/genshin_app/gui/web');
const timeline = fs.readFileSync(path.join(web, 'timeline.js'), 'utf8');
const timelineView = fs.readFileSync(path.join(web, 'timeline-view.js'), 'utf8');
const business=path.join(__dirname,'../frontend/src/business');
const timelineBusiness=['dom','svg'].map(name=>fs.readFileSync(path.join(business,'timeline',name+'.ts'),'utf8')).join('\n');
const whiteboard = ['view','svg'].map(name=>fs.readFileSync(path.join(business,'whiteboard',name+'.ts'),'utf8')).join('\n');

test('timeline event colours resolve through the app theme and repaint when it changes', () => {
  assert.match(timeline, /ThemeController\?\.token/);
  for (const token of ['color-axis-action', 'color-axis-damage', 'color-axis-buff',
    'color-axis-reaction', 'color-axis-character-']) assert.ok(timelineBusiness.includes(token), token);
  assert.match(timelineView, /genshin-theme-change/);
  assert.match(timelineView, /TV\.requestPaint/);
});

test('whiteboard SVG uses semantic axis colours and redraws in-place on theme change', () => {
  assert.match(whiteboard, /const themeColor =/);
  for (const token of ['color-axis-lane-even', 'color-axis-coverage-lane',
    'color-axis-grid', 'color-axis-event-text', 'color-axis-cursor']) assert.ok(whiteboard.includes(token), token);
  assert.match(whiteboard, /genshin-theme-change/);
  assert.match(whiteboard, /WB\.refreshThemeColors\(\);WB\.draw\(\)/);
});
