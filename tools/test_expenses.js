// Витрати в обліку (node tools/test_expenses.js): аркуш «Витрати», рядки в «Підсумку», команда «витрата 50 …»
const fs = require('fs');
const vm = require('vm');
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O' };
const sent = [];

// таблиця-підробка: клітинки «рядок:стовпець», діапазони за номерами або «A1»
function sheet(name) {
  const cells = {};
  const s = { name, cells, last: 0, getLastRow: () => s.last, setColumnWidth() {}, setFrozenRows() {} };
  s.getRange = (r, c, n, w) => {
    if (typeof r === 'string') {   // «A1» або «B3:B11»
      const m = r.match(/^([A-Z])(\d+)(?::([A-Z])(\d+))?/);
      c = m[1].charCodeAt(0) - 64; w = m[3] ? m[3].charCodeAt(0) - 64 - c + 1 : 1; n = m[4] ? +m[4] - +m[2] + 1 : 1; r = +m[2];
    }
    n = n || 1; w = w || 1;
    const put = (rr, cc, v) => { cells[rr + ':' + cc] = v; s.last = Math.max(s.last, rr); };
    const rng = new Proxy({}, { get: (_, k) => {
      if (k === 'setValue' || k === 'setFormula') return (v) => { put(r, c, v); return rng; };
      if (k === 'setValues') return (vals) => { vals.forEach((row, i) => row.forEach((v, j) => put(r + i, c + j, v))); return rng; };
      if (k === 'getValue') return () => (cells[r + ':' + c] !== undefined ? cells[r + ':' + c] : '');
      if (k === 'getValues') return () => Array.from({ length: n }, (_, i) => Array.from({ length: w }, (_, j) => {
        const v = cells[(r + i) + ':' + (c + j)]; return v === undefined ? '' : v; }));
      return () => rng;   // формати, кольори — ланцюжком
    } });
    return rng;
  };
  return s;
}
function book() {
  const sum = sheet('Підсумок');
  [5, 1, 4, 300, 250, 120, 42.5, 0.14, 7].forEach((v, i) => sum.getRange(3 + i, 2).setValue(v));   // B3:B11
  sum.getRange(11, 1).setValue('Середньо днів до продажу');
  const b = { sheets: { 'Угоди': sheet('Угоди'), 'Підсумок': sum }, getUrl: () => 'URL' };
  b.getSheetByName = (n) => b.sheets[n] || null;
  b.insertSheet = (n) => (b.sheets[n] = sheet(n));
  return b;
}
const ctx = {
  console: { log: () => {} },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => store[k] || null, setProperty: (k, v) => { store[k] = v; } }) },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
let ss = book();
vm.runInContext('ledger_ = function () { return __ss(); }; notify_ = function (t) { __sent.push(t); };',
  Object.assign(ctx, { __ss: () => ss, __sent: sent }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };
const say = (text, chat) => vm.runInContext('officeMessage(__m)', Object.assign(ctx, { __m: { text: text, chat: { id: chat || 7 } } }));
const main = (text) => vm.runInContext('ledgerCommand(__m)', Object.assign(ctx, { __m: { text: text, chat: { id: 7 } } }));

// аркуш і підсумок
ctx.expensesSheet_(ss);
const sum = ss.sheets['Підсумок'], exp = ss.sheets['Витрати'];
check('sheet created with headers', exp.cells['2:2'], 'Що');
check('summary rows under the main numbers', [sum.cells['12:1'], sum.cells['12:2'], sum.cells['13:1'], sum.cells['13:2']],
      ['Витрати (обладнання, пакування, пересилка), €', "=SUM('Витрати'!C3:C)", 'Прибуток після витрат, €', '=B9-B12']);
ctx.expensesSheet_(ss);
check('second call — no duplicate', Object.keys(sum.cells).filter((k) => /^1[4-9]:/.test(k)).length, 0);

// команди
say('витрата 50 стенд MSI A320 + Ryzen 3200G для тесту RAM');
check('row 3', [exp.cells['3:2'], exp.cells['3:3'], exp.cells['3:4'], Object.prototype.toString.call(exp.cells['3:1']) === '[object Date]'],
      ['стенд MSI A320 + Ryzen 3200G для тесту RAM', 50, 'Обладнання', true]);
check('reply', /Записав витрату.*50 €.*Обладнання[\s\S]*Усього витрат: 50\.00/.test(sent[0]), true);
say('витратив 4,5 термопаста Arctic MX-4');
check('row 4 + total', [exp.cells['4:2'], exp.cells['4:3'], /Усього витрат: 54\.50/.test(sent[1])], ['термопаста Arctic MX-4', 4.5, true]);
main('витрата 3 коробки для пакування');   // з основного бота — теж
check('packaging category, main bot', [exp.cells['5:4'], exp.cells['5:3']], ['Пакування', 3]);
say('витрата 2,70 Porto DHL перехідник');
check('shipping category', exp.cells['6:4'], 'Пересилка');
say('витрата стенд');
check('no amount → hint', /спершу сума/.test(sent[sent.length - 1]), true);
say('облік');
check('облік shows expenses and profit after', /Витрати \(обладнання, пакування…\): 60\.20 € → прибуток після витрат: -17\.70 €/.test(sent[sent.length - 1]), true);
const before = sent.length;
say('витрата 99 чуже', 99);
check('foreign chat — nothing written', [sent.length, exp.cells['7:2']], [before, undefined]);

// таблицю правили вручну: рядки 12–13 зайняті — підсумок витрат нижче за все
ss = book();
ss.sheets['Підсумок'].getRange(12, 1).setValue('мій рядок');
ss.sheets['Підсумок'].getRange(30, 1).setValue('щось');
ctx.expensesSheet_(ss);
check('occupied rows respected', [ss.sheets['Підсумок'].cells['12:1'], ss.sheets['Підсумок'].cells['32:2'], ss.sheets['Підсумок'].cells['33:2']],
      ['мій рядок', "=SUM('Витрати'!C3:C)", '=B9-B32']);
check('облік without expenses — unchanged', (() => { say('облік'); return /Витрати/.test(sent[sent.length - 1]); })(), false);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
