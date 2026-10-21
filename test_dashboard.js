const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('index.html', 'utf8');
function source(name) {
  const start = html.indexOf(`function ${name}(`);
  const end = html.indexOf('\nfunction ', start + 1);
  return html.slice(start, end).replace(/\n(?:\/\/[^\n]*\n|\s)*$/, '');
}
const context = vm.createContext({
  getDisplayName: x => x, getShortName: x => x, getModelColor: () => '#fff',
  CATS: ['math'], MODELS: [], BY_QUESTION: {1: [{auto: null}]},
  RAW_DATA: {labels: {1: 'Question'}},
  getSort: () => [], esc: String, fmt: String, heatClass: () => '',
  el: () => ({innerHTML: ''})
});
vm.runInContext(source('computeModelStats'), context);
const stats = context.computeModelStats([
  {model: 'mock', seconds: 2, auto: true},
  {model: 'mock', seconds: 4, auto: null}
], []);
assert.equal(stats[0].median, 3, 'even median must average the middle pair');
assert.equal(stats[0].accuracy, 100, 'skipped grades must not lower accuracy');
const hardware = context.computeModelStats([{model: 'mock', run: 'new', auto: true}], [
  {id: 'old', models: ['mock'], system: {gpu: 'Old GPU'}},
  {id: 'new', models: ['mock'], system: {gpu: 'New GPU'}}
]);
assert.equal(hardware[0].hardware, 'New GPU');
vm.runInContext(source('renderSkillHeatmap'), context);
context.renderSkillHeatmap();
const elements = {};
context.el = id => elements[id] ||= {};
context.RAW_DATA = {runs: [{id: 'test', system: {cpu: '<img src=x onerror=alert(1)>'}}]};
vm.runInContext(source('esc'), context);
vm.runInContext(source('renderAudit'), context);
context.renderAudit();
assert.ok(!elements['audit-runs'].innerHTML.includes('<img'));
for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
  if (/application\/(?:ld\+)?json/.test(match[1])) JSON.parse(match[2]);
  else new vm.Script(match[2]);
}

(async () => {
  let loadFolder;
  let imported;
  context.AUTO_REFRESH_TIMER = null;
  context.clearInterval = () => {};
  context.toast = () => {};
  context.processData = data => { imported = data; };
  context.el = id => ({addEventListener: (event, callback) => { if (id === 'folder-picker') loadFolder = callback; }});
  const start = html.indexOf("el('folder-picker').addEventListener('change'");
  const end = html.indexOf('// Auto-refresh control', start);
  vm.runInContext(html.slice(start, end), context);
  const snapshot = JSON.parse(fs.readFileSync('results.json', 'utf8'));
  context.DEFAULT_LABELS = snapshot.labels;
  await loadFolder({target: {files: [{name: 'results.json', text: async () => JSON.stringify(snapshot)}]}});
  assert.equal(imported.runs.length, snapshot.runs.length);
  assert.equal(imported.records[0].run, snapshot.records[0].run);
  assert.equal(imported.runs[0].system.gpu, snapshot.runs[0].system.gpu);
  console.log('Dashboard statistics, rendering, import, and syntax tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
