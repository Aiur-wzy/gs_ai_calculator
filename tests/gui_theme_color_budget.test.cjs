'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '../src/genshin_app/gui/web');
const REVIEWED_LEGACY_LITERAL_BUDGET = 594;
const GENERATED_TOKEN_STYLESHEET = 'gui-foundation-tokens.css';
// Accepted DSH/provider CSS and compiled bundles are reviewed by exact asset
// manifests, not as hand-authored legacy component literals. Generated island
// CSS is a second copy of the source styles and must not be counted twice.
const GENERATED_ASSETS = new Set(['dsh-production.js','dsh-production.css','legacy-island.css','gui-foundation-v2.js','business-ui.js']);

function sourceFiles(directory) {
  return fs.readdirSync(directory)
    .filter(name => /\.(?:css|js)$/u.test(name) && name !== 'theme.css' && name !== GENERATED_TOKEN_STYLESHEET && !GENERATED_ASSETS.has(name))
    .map(name => path.join(directory, name));
}

test('component colour literals cannot grow without updating the reviewed migration baseline', () => {
  const rows = sourceFiles(WEB).map(file => ({
    file: path.basename(file),
    count: fs.readFileSync(file, 'utf8').match(/#[0-9a-f]{3,8}\b/giu)?.length || 0,
  }));
  const total = rows.reduce((sum, row) => sum + row.count, 0);
  const largest = rows.filter(row => row.count).sort((a, b) => b.count - a.count).slice(0, 8);
  assert.ok(total <= REVIEWED_LEGACY_LITERAL_BUDGET,
    `Unreviewed GUI colour literals grew from ${REVIEWED_LEGACY_LITERAL_BUDGET} to ${total}: ${JSON.stringify(largest)}`);
});
