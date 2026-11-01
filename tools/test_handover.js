// 01.11 (node tools/test_handover.js): «продав 9 Patriot Viper 4…» — 9 це номер, ціна з KA (20 €), продаж з рук без «запакуй»
const fs = require('fs');
const vm = require('vm');
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O' };
const sent = [];

function sheet() {
  const cells = {};
  const s = { cells, last: 0, getLastRow: () => s.last };
  s.getRange = (r, c, n, w) => {
    n = n || 1; w = w || 1;
    const rng = new Proxy({}, { get: (_, k) => {
      if (k === 'setValue' || k === 'setFormula') return (v) => { cells[r + ':' + c] = v; s.last = Math.max(s.last, r); return rng; };
      if (k === 'getValue') return () => (cells[r + ':' + c] !== undefined ? cells[r + ':' + c] : '');
      if (k === 'getValues') return () => Array.from({ length: n }, (_, i) => Array.from({ length: w }, (_, j) => {
        const v = cells[(r + i) + ':' + (c + j)]; return v === undefined ? '' : v; }));
      return () => rng;
    } });
    return rng;
  };
  return s;
}
const deals = sheet();
const rows = [['OWC 2x16GB DDR4 SO-DIMM', 'Виставлено'], ['Patriot Viper 4 2x4GB DDR4-3000 PV48G300C6K', 'Виставлено'],
  ['Corsair Vengeance 2x16GB DDR4', 'Виставлено']];
rows.forEach((v, i) => {   // №1–№3; №9 — Patriot (як у користувача): рядки 5.. → №(r-4)
  const r = i === 1 ? 13 : 5 + i;
  deals.cells[r + ':2'] = new Date('2026-10-20T10:00:00Z'); deals.cells[r + ':3'] = v[0]; deals.cells[r + ':11'] = v[1];
  deals.cells[r + ':10'] = 0; deals.last = Math.max(deals.last, r);
});
const ctx = {
  console: { log: () => {} },
  Utilities: { formatDate: (d) => d.toISOString().slice(0, 10), getUuid: () => 'u' },
  LockService: { getScriptLock: () => ({ waitLock() {}, tryLock() { return true; }, releaseLock() {} }) },
  SpreadsheetApp: { flush() {} },
  UrlFetchApp: { fetch: () => ({ getResponseCode: () => 200, getContentText: () => '{}' }) },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => (k in store ? store[k] : null),
    setProperty: (k, v) => { store[k] = v; }, deleteProperty: (k) => { delete store[k]; } }) },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
let packs = 0;
vm.runInContext('ledger_ = function () { return { getSheetByName: () => __d }; }; notify_ = function (t) { __sent.push(t); };' +
  'packCheck_ = function () { __pack(); };', Object.assign(ctx, { __d: deals, __sent: sent, __pack: () => packs++ }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };
const say = (text) => vm.runInContext('officeMessage(__m)', Object.assign(ctx, { __m: { text: text, chat: { id: 7 } } }));
const last = () => sent[sent.length - 1] || '';

// «продано 9 Patriot…» — не перепитує «продати чи продав», а «продав»; ціни немає й не знаємо — питає
check('«продано N назва» → продав', ctx.canonCmd_('продано 9 Patriot Viper 4'), 'продав 9 Patriot Viper 4');
check('«продано N» без нічого — кнопка продажу з листа', ctx.canonCmd_('продано 9'), 'продано 9');
say('продано 9 Patriot Viper 4 2x4GB DDR4-3000');
check('unknown price → ask', [/№9 «Patriot[\s\S]*за скільки продав/.test(last()), deals.cells['13:11']], [true, 'Виставлено']);
// ціна з KA («виставив 9 ka 20») — 20 €, з рук: без пересилки й пакування, без «запакуй», без нагадувань
ctx.setEvents_(9, { ka: 1, kaPrice: 20 });
say('продав 9 Patriot Viper 4 2x4GB DDR4-3000');
check('sold row 9 at KA price', [deals.cells['13:11'], deals.cells['13:14'], deals.cells['13:15'], deals.cells['13:16'], packs],
  ['Продано', 20, 0, 0, 0]);
check('message says handed over', [/№9[\s\S]*20 € \(ціна з оголошення/.test(last()), /З рук — пакувати/.test(last())], [true, true]);
const e9 = ctx.events_()[9];
check('events: handed, no shipping reminders', [e9.handed, !!e9.sent, !!e9.outDelivered], [1, true, true]);
check('not in outgoing parcels', ctx.parcelsData_().outgoing.length, 0);
// «відправив 9 трек» після «з рук» — таки поштою: позначку знято, пакування — за замовчуванням
say('відправив 9 00340434161094000001');
const e9b = ctx.events_()[9];
check('shipped after handed', [e9b.handed, e9b.outDelivered, !!e9b.outTrack, deals.cells['13:16']], [undefined, undefined, true, '']);

// «продав 8 140» — №8 немає → як раніше («8» — ціна); «продав 110 OWC» — ціна 110, рядок за назвою
say('продав 110 OWC');
check('price-first still works', [deals.cells['5:11'], deals.cells['5:14'], /З рук/.test(last())], ['Продано', 110, true]);
// «продав 3 150 ebay» — №3 за 150 € на eBay: не з рук, «запакуй»
say('продав 3 150 ebay');
check('number + price + ebay', [deals.cells['7:11'], deals.cells['7:14'], deals.cells['7:13'], packs], ['Продано', 150, 'eBay', 1]);

console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
