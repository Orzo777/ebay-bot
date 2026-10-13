// Таблиця простіше (13.10, node tools/test_tidy.js): кольори статусів, деталі згорнуті в групи, службові аркуші сховані — один раз.
const fs = require('fs');
const vm = require('vm');
const store = { LEDGER_ID: 'L' };
const calls = [];
const rule = () => {
  const r = { text: '', bg: '', ranges: [] };
  const b = { whenTextEqualTo: (t) => { r.text = t; return b; }, setBackground: (c) => { r.bg = c; return b; },
    setRanges: (x) => { r.ranges = x; return b; }, build: () => r };
  return b;
};
const deals = {
  rules: [{ text: 'Продано' }],
  getConditionalFormatRules() { return this.rules.slice(); },
  setConditionalFormatRules(x) { this.rules = x; },
  getRange: (...a) => ({ a, shiftColumnGroupDepth: (d) => calls.push(['group', a[1], a[3], d]) }),
  getColumnGroup: (i, d) => ({ collapse: () => calls.push(['collapse', i, d]) }),
};
const hidden = [];
const ss = { getSheetByName: (n) => (n === 'Угоди' ? deals : ['Лог листів', 'Налаштування'].includes(n) ? { hideSheet: () => hidden.push(n) } : null) };
const ctx = {
  console: { log: () => {} },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => store[k] || null, setProperty: (k, v) => { store[k] = v; } }) },
  SpreadsheetApp: { newConditionalFormatRule: rule },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
vm.runInContext('ledger_ = function () { return __ss; };', Object.assign(ctx, { __ss: ss }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };

ctx.tidySheet1013_();
check('status colours added, old rules kept', deals.rules.map((r) => r.text),
      ['Продано', 'Перевірено', 'Виставлено', 'Отримано', 'В дорозі', 'Оплачено', 'Скасовано']);
check('details grouped and collapsed (F–I, O–R)', calls, [['group', 6, 4, 1], ['collapse', 6, 1], ['group', 15, 4, 1], ['collapse', 15, 1]]);
check('service sheets hidden', hidden, ['Лог листів', 'Налаштування']);
ctx.tidySheet1013_();
check('only once', [calls.length, deals.rules.length], [4, 7]);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
