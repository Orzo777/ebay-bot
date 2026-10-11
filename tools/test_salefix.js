// 11.10 (node tools/test_salefix.js): продаж записано без ціни → разова міграція бере суму з листа «Artikel verkauft»;
// «Підсумок»: скасовані купівлі не входять у «Витрачено».
const fs = require('fs');
const vm = require('vm');
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O' };
const sent = [];
function sheet() {
  const cells = {}, formulas = {};
  const s = { cells, formulas, last: 0, getLastRow: () => s.last };
  s.getRange = (r, c, n, w) => {
    if (typeof r === 'string') { const m = r.match(/^([A-Z])(\d+)$/); c = m[1].charCodeAt(0) - 64; r = +m[2]; }
    n = n || 1; w = w || 1;
    const rng = new Proxy({}, { get: (_, k) => {
      if (k === 'setValue') return (v) => { cells[r + ':' + c] = v; s.last = Math.max(s.last, r); return rng; };
      if (k === 'setFormula') return (v) => { formulas[r + ':' + c] = v; return rng; };
      if (k === 'getFormula') return () => formulas[r + ':' + c] || '';
      if (k === 'getValue') return () => (cells[r + ':' + c] !== undefined ? cells[r + ':' + c] : '');
      if (k === 'getValues') return () => Array.from({ length: n }, (_, i) => Array.from({ length: w }, (_, j) => {
        const v = cells[(r + i) + ':' + (c + j)]; return v === undefined ? '' : v; }));
      return () => rng;
    } });
    return rng;
  };
  return s;
}
const deals = sheet(), sum = sheet();
[['Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200', 'Продано', ''], ['OWC 2x16GB DDR4 SO-DIMM', 'Отримано', ''],
 ['Kingston FURY 32GB (2x16GB) DDR4', 'Скасовано', '']].forEach((v, i) => {
  deals.cells[(5 + i) + ':3'] = v[0]; deals.cells[(5 + i) + ':11'] = v[1]; deals.cells[(5 + i) + ':14'] = v[2]; deals.last = 5 + i;
});
const R = (c) => "'Угоди'!" + c + '5:' + c;
sum.formulas['6:2'] = '=SUM(' + R('J') + ')';
sum.formulas['15:4'] = '=SUMIFS(' + R('J') + ',' + R('D') + ',A15)';
sum.formulas['23:3'] = '=SUMIFS(' + R('J') + ',' + R('E') + ',A23)';
sum.formulas['16:4'] = '=МОЯ ФОРМУЛА';
const body = 'Gute Neuigkeiten - Ihr Artikel wurde verkauft!\nIhr Käufer hat noch nicht bezahlt, innerhalb von 48 Stunden.\n' +
  'Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200 CMK32...\nVerkauft:\nEUR 149,00\nVerkauft am:\n09. Okt. 2026\nVerkaufte Stückzahl:\n1';
const msg = { getFrom: () => 'eBay <ebay@ebay.de>', getSubject: () => '🎉 Artikel verkauft - Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200 CMK32...',
  getPlainBody: () => body, getBody: () => body, getDate: () => new Date('2026-10-09T12:00:00Z'), getId: () => 'm1' };
let searches = 0;
const ctx = {
  console: { log: () => {} },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => store[k] || null, setProperty: (k, v) => { store[k] = v; } }) },
  GmailApp: { search: () => { searches++; return [{ getMessages: () => [msg] }]; } },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
vm.runInContext("ledger_ = function () { return { getSheetByName: function (n) { return n === 'Угоди' ? __d : n === 'Підсумок' ? __s : null; } }; };" +
  'notify_ = function (t) { __sent.push(t); };', Object.assign(ctx, { __d: deals, __s: sum, __sent: sent }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };

ctx.fixSalePrice1011_();
check('price filled on the sold row only', [deals.cells['5:14'], deals.cells['6:14'], deals.cells['7:14']], [149, '', '']);
check('told the user', /Дописав ціну продажу №1: 149 €/.test(sent[0] || ''), true);
ctx.fixSalePrice1011_();
check('once', [searches, sent.length], [1, 1]);

ctx.summaryNoCancelled1011_();
check('summary without cancelled', [sum.formulas['6:2'], sum.formulas['15:4'], sum.formulas['23:3']],
      ['=SUMIFS(' + R('J') + ',' + R('K') + ',"<>Скасовано")', '=SUMIFS(' + R('J') + ',' + R('D') + ',A15,' + R('K') + ',"<>Скасовано")',
       '=SUMIFS(' + R('J') + ',' + R('E') + ',A23,' + R('K') + ',"<>Скасовано")']);
check('user-edited formula untouched', sum.formulas['16:4'], '=МОЯ ФОРМУЛА');
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
