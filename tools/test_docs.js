// Документи покупок (node tools/test_docs.js, 18.10): «чек 5» на фото / PDF → аркуш «Документи», не фото для eBay;
// альбом за підписом будь-якого фото; дубль не пишемо; «документи 5» — надіслати назад; фото без «чек» — не документ.
const fs = require('fs');
const vm = require('vm');
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O' };
const sent = [], tg = [];

function sheet() {
  const rows = [];
  return { rows, getLastRow: () => rows.length, appendRow: (r) => { rows.push(r.slice()); },
    getRange: (r, c, h, w) => ({ getValues: () => rows.slice(r - 1, r - 1 + h).map((x) => x.slice(c - 1, c - 1 + w)) }) };
}
const sheets = {};
const deals = { cells: {}, getLastRow: () => 9, getRange: (r, c, n, w) => ({
  getValues: () => Array.from({ length: n || 1 }, (_, i) => Array.from({ length: w || 1 }, (_, j) => deals.cells[(r + i) + ':' + (c + j)] || '')),
  getValue: () => deals.cells[r + ':' + c] || '' }) };
deals.cells['9:3'] = 'Patriot Viper 4 2x4GB DDR4-3000'; deals.cells['9:11'] = 'Виставлено'; deals.cells['9:2'] = new Date('2026-10-18T10:00:00Z');
const ctx = {
  console: { log: () => {} },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => (k in store ? store[k] : null),
    setProperty: (k, v) => { store[k] = v; }, deleteProperty: (k) => { delete store[k]; } }) },
  UrlFetchApp: { fetch: (u, o) => { tg.push([u.split('/').pop(), (o || {}).payload]); return { getResponseCode: () => 200, getContentText: () => '{"ok":true}' }; } },
  Utilities: { formatDate: () => '18.10' },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
vm.runInContext(`ledger_ = function () { return { getSheetByName: function (n) { return n === 'Угоди' ? __deals : (__sheets[n] || null); },
  insertSheet: function (n) { __sheets[n] = __mk(); return __sheets[n]; } }; };
  notify_ = function (t) { __sent.push(t); };
  rowByN_ = function (n) { return n === 5 ? { n: 5, title: 'Patriot Viper 4 2x4GB DDR4-3000', status: 'Виставлено' } : null; };`,
Object.assign(ctx, { __deals: deals, __sheets: sheets, __sent: sent, __mk: sheet }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };
const photo = (id, extra) => Object.assign({ chat: { id: 7 }, message_id: 1, photo: [{ file_id: id + '_s' }, { file_id: id, file_unique_id: 'U' + id }] }, extra || {});

check('photo with «чек 5» → document', ctx.docMessage_(photo('C1', { caption: 'чек 5' })), true);
check('row written', sheets['Документи'].rows.slice(1).map((r) => [r[1], r[3], r[6]]), [[5, 'фото', 'C1']]);
check('told the user', /Документ покупки №5/.test(sent[sent.length - 1]), true);
check('duplicate not written twice', [ctx.docMessage_(photo('C1', { caption: 'чек 5' })), sheets['Документи'].rows.length], [true, 2]);
// PDF рахунок
ctx.docMessage_({ chat: { id: 7 }, message_id: 2, caption: 'Rechnung 5', document: { file_id: 'P1', file_unique_id: 'UP1', file_name: 'rechnung.pdf', mime_type: 'application/pdf' } });
check('pdf invoice', sheets['Документи'].rows[2][3], 'rechnung.pdf');
// альбом: перше фото без підпису чекало номер як фото товару → підпис «чек 5» забирає і його
store.PH_PENDING = JSON.stringify([{ f: { id: 'A1', t: 'photo', u: 'UA1' }, m: 10, mg: 'MG_g1', ts: Date.now() }]);
ctx.docMessage_(photo('A2', { caption: 'чек 5', media_group_id: 'g1' }));
check('album sibling taken as document', [sheets['Документи'].rows.slice(3).map((r) => r[6]), store.PH_PENDING], [['A2', 'A1'], undefined]);
ctx.docMessage_(photo('A3', { media_group_id: 'g1' }));
check('later sibling too', sheets['Документи'].rows[sheets['Документи'].rows.length - 1][6], 'A3');
// звичайне фото товару — не документ
check('plain photo «5» is not a document', ctx.docMessage_(photo('X1', { caption: '5' })), false);
check('shipping receipt «квитанція 5» is not a purchase document', ctx.docMessage_(photo('X2', { caption: 'квитанція 5' })), false);
check('unknown row', [ctx.docMessage_(photo('X3', { caption: 'чек 99' })), /немає №99/.test(sent[sent.length - 1])], [true, true]);
// «документи 5» → альбом; рядки з документами — для тижневого звіту
ctx.docShowCommand_({ text: 'документи 5', chat: { id: 7 } });
check('show sends album', tg.some((c) => c[0] === 'sendMediaGroup'), true);
check('doc rows', ctx.docRows_(), { 5: 1 });
ctx.docShowCommand_({ text: 'документи 7', chat: { id: 7 } });
check('no docs → hint', /документів ще немає/.test(sent[sent.length - 1]), true);

console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
