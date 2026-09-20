// Exercise every task/metric selection in the generated offline page.
// This checks JavaScript behavior with a minimal DOM, not browser layout.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '..', 'explorer.html'), 'utf8');
const payload = html.match(/<script id="data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const script = html.match(/<\/script><script>([\s\S]*?)<\/script>/)[1];
const metrics = [...html.matchAll(/<option value="([^"]+)">/g)].map(x => x[1]);
class Element {
  constructor() { this.value = ''; this.textContent = ''; this.innerHTML = ''; this.children = []; this.listeners = {}; }
  appendChild(child) { this.children.push(child); if (!this.value) this.value = child.value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
}
const elements = Object.fromEntries(['data', 'task', 'metric', 'chart', 'summary', 'table'].map(x => [x, new Element()]));
elements.data.textContent = payload;
elements.metric.value = metrics[0];
const context = vm.createContext({document: {getElementById: x => elements[x], createElement: () => new Element()},
  URLSearchParams, location: {search: ''}});
new vm.Script(script).runInContext(context);
if (elements.task.children.length !== 32) throw Error('Expected 32 groups');
let selections = 0;
for (const option of elements.task.children) {
  elements.task.value = option.value;
  for (const metric of metrics) {
    elements.metric.value = metric;
    elements.metric.listeners.change();
    if ((elements.chart.innerHTML.match(/class="grid"/g) || []).length !== 8) throw Error('Expected eight bars');
    if ((elements.table.innerHTML.match(/<tr>/g) || []).length !== 9) throw Error('Expected eight data rows');
    if (/NaN|undefined|Infinity/.test(elements.chart.innerHTML + elements.table.innerHTML)) throw Error('Invalid value');
    selections++;
  }
}
process.stdout.write(JSON.stringify({passed: true, groups: 32, metrics: metrics.length, selections,
  method: 'JavaScript execution with minimal DOM; browser layout not exercised'}) + '\n');
