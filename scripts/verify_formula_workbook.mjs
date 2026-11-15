// RF-07 engineering acceptance with the separately supplied Artifact Tool.
// Run from a temporary directory whose node_modules points to bundled packages.
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(path.join(process.cwd(), 'acceptance.mjs'));
const { FileBlob, SpreadsheetFile } = require('@oai/artifact-tool');
const out = path.resolve(process.argv[2] || 'outputs/engineering-formula-d20');
const controls = JSON.parse(await fs.readFile(path.join(out, 'controls.json'), 'utf8'));
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(out, 'formula-probe.xlsx')));
wb.recalculate();
const summary = wb.worksheets.getItem('汇总');
const inputs = wb.worksheets.getItem('参数');
const values = inputs.getRange('A1:J500').values;
const paths = new Map(values.map((row, i) => [row[0], i + 1]));
const results = [];
const checkNumber = (actual, expected, name) => {
  if (typeof actual !== 'number' || Math.abs(actual - expected) > Math.max(1e-7, Math.abs(expected) * 1e-10))
    throw new Error(`${name}: ${actual} != ${expected}`);
};
checkNumber(summary.getRange('B6').values[0][0], controls.baseline, 'baseline');
for (const test of controls.cases) {
  const address = `C${paths.get(test.path)}`;
  const original = inputs.getRange(address).values[0][0];
  inputs.getRange(address).values = [[test.value]];
  wb.recalculate();
  const actual = summary.getRange('B6').values[0][0];
  checkNumber(actual, test.expected, test.path);
  if (!summary.getRange('B8').values[0][0].includes('局部情景')) throw new Error('scenario status unchanged');
  if (!summary.getRange('B9').values[0][0].includes('失效')) throw new Error('cycle status unchanged');
  results.push({ ...test, actual });
  inputs.getRange(address).values = [[original]];
  wb.recalculate();
  checkNumber(summary.getRange('B6').values[0][0], controls.baseline, 'restored');
}
for (const fixedPath of ['hits[0].count', 'buffs[0].end']) {
  const address = `C${paths.get(fixedPath)}`;
  const original = inputs.getRange(address).values[0][0];
  inputs.getRange(address).values = [[original + 1]];
  wb.recalculate();
  const actual = summary.getRange('B6').values[0][0];
  if (actual !== '需重新生成') throw new Error(`${fixedPath}: stale result remained ${actual}`);
  results.push({ path: fixedPath, actual });
  inputs.getRange(address).values = [[original]];
}
const erAddress = `C${paths.get('characters[0].stats.energy_recharge')}`;
const erOriginal = inputs.getRange(erAddress).values[0][0];
inputs.getRange(erAddress).values = [[erOriginal + .2]];
wb.recalculate();
if (!summary.getRange('B9').values[0][0].includes('失效')) throw new Error('ER cycle invalidation failed');
inputs.getRange(erAddress).values = [[erOriginal]];
const attackAddress = `C${paths.get('characters[0].stats.atk')}`;
const attackOriginal = inputs.getRange(attackAddress).values[0][0];
inputs.getRange(attackAddress).values = [[null]];
wb.recalculate();
if (summary.getRange('B6').values[0][0] !== '需重新生成') throw new Error('blank input concealed as zero');
inputs.getRange(attackAddress).values = [[attackOriginal]];
wb.recalculate();
checkNumber(summary.getRange('B6').values[0][0], controls.baseline, 'final restored');
const errors = await wb.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!', options: { useRegex: true, maxResults: 100 }, maxChars: 3000 });
await fs.writeFile(path.join(out, 'artifact-formula-error-scan.txt'), errors.ndjson);
const inspected = await wb.inspect({ kind: 'table', range: '汇总!A4:C13', include: 'values,formulas', tableMaxRows: 12, tableMaxCols: 3, maxChars: 6000 });
await fs.writeFile(path.join(out, 'artifact-summary-inspect.txt'), inspected.ndjson);
for (const [sheetName, range, file] of [['汇总', 'A1:C13', 'summary'], ['参数', 'B1:F15', 'inputs'], ['逐事件', 'A1:H11', 'events-inputs'], ['逐事件', 'I4:Q11', 'events-zones'], ['逐事件', 'R4:Z11', 'events-results'], ['来源与范围', 'A1:B13', 'sources']]) {
  const blob = await wb.render({ sheetName, range, scale: 1.5, format: 'png' });
  await fs.writeFile(path.join(out, `${file}.png`), new Uint8Array(await blob.arrayBuffer()));
}
await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(out, 'formula-probe-recalculated.xlsx'));
const dynamic = await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(out, 'mechanics-formula-probe.xlsx')));
const di = dynamic.worksheets.getItem('参数'), ds = dynamic.worksheets.getItem('汇总');
const dp = new Map(di.getRange('A1:J500').values.map((row, i) => [row[0], i + 1]));
dynamic.recalculate();
checkNumber(ds.getRange('B6').values[0][0], controls.mechanics.baseline, 'mechanics baseline');
const dynamicResults = [];
for (const test of controls.mechanics.cases) {
  const address = `C${dp.get(test.path)}`, original = di.getRange(address).values[0][0];
  di.getRange(address).values = [[test.value]];
  dynamic.recalculate();
  const actual = ds.getRange('B6').values[0][0];
  checkNumber(actual, test.expected, `mechanics ${test.path}`);
  dynamicResults.push({ ...test, actual });
  di.getRange(address).values = [[original]];
  dynamic.recalculate();
  checkNumber(ds.getRange('B6').values[0][0], controls.mechanics.baseline, 'mechanics restore');
}
const durationAddress = `C${dp.get('mechanics_model.rules[2].timing.duration')}`;
const durationOriginal = di.getRange(durationAddress).values[0][0];
di.getRange(durationAddress).values = [[durationOriginal + 1]];
dynamic.recalculate();
if (ds.getRange('B6').values[0][0] !== '需重新生成') throw new Error('dynamic rule structure change not blocked');
di.getRange(durationAddress).values = [[durationOriginal]];
dynamic.recalculate();
const dynamicErrors = await dynamic.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!', options: { useRegex: true, maxResults: 100 }, maxChars: 3000 });
await fs.writeFile(path.join(out, 'mechanics-formula-error-scan.txt'), dynamicErrors.ndjson);
await (await SpreadsheetFile.exportXlsx(dynamic)).save(path.join(out, 'mechanics-formula-probe-recalculated.xlsx'));
const dynamicPreview = await dynamic.render({ sheetName: '汇总', range: 'A1:C13', scale: 1.5, format: 'png' });
await fs.writeFile(path.join(out, 'mechanics-summary.png'), new Uint8Array(await dynamicPreview.arrayBuffer()));
const energy = await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(out, 'energy-window-formula-probe.xlsx')));
const ei = energy.worksheets.getItem('参数'), es = energy.worksheets.getItem('汇总');
const ep = new Map(ei.getRange('A1:J500').values.map((row, i) => [row[0], i + 1]));
energy.recalculate();
checkNumber(es.getRange('B6').values[0][0], controls.energy_window.baseline, 'energy window baseline');
const energyResults = [];
for (const test of controls.energy_window.cases) {
  const address = `C${ep.get(test.path)}`, original = ei.getRange(address).values[0][0];
  ei.getRange(address).values = [[test.value]];
  energy.recalculate();
  const actual = es.getRange('B6').values[0][0];
  checkNumber(actual, test.expected, `energy window ${test.path}`);
  if (!es.getRange('B9').values[0][0].includes('失效')) throw new Error('energy window cycle invalidation failed');
  energyResults.push({ ...test, actual });
  ei.getRange(address).values = [[original]];
  energy.recalculate();
  checkNumber(es.getRange('B6').values[0][0], controls.energy_window.baseline, 'energy window restore');
}
const energyErrors = await energy.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!', options: { useRegex: true, maxResults: 100 }, maxChars: 3000 });
await fs.writeFile(path.join(out, 'energy-window-formula-error-scan.txt'), energyErrors.ndjson);
await (await SpreadsheetFile.exportXlsx(energy)).save(path.join(out, 'energy-window-formula-probe-recalculated.xlsx'));
await fs.writeFile(path.join(out, 'artifact-acceptance.json'), JSON.stringify({ runtime: 'Artifact Tool', baseline: controls.baseline, results, mechanics: dynamicResults, energy_window: energyResults, restored: summary.getRange('B6').values[0][0], nativeExcel: 'see native-excel-acceptance.json' }, null, 2));
console.log(JSON.stringify({ baseline: controls.baseline, passed: results.length + dynamicResults.length + energyResults.length + 3, out }));
