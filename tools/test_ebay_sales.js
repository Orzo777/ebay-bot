// 03.11 (node tools/test_ebay_sales.js): продажі з eBay без листа (пошту блокували), скасований продаж №4, лот на 2 штуки
// (№5 і №6, SKU ledger-5), звірка таблиці з фінансовим звітом eBay, стан Apps Script ззовні (тригери)
const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');
const signed = (buf) => Array.from(buf).map((b) => (b > 127 ? b - 256 : b));
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O' };
const sent = [];

function sheet() {
  const cells = {}, formulas = {};
  const s = { cells, formulas, last: 0, getLastRow: () => s.last };
  s.getRange = (r, c, n, w) => {
    n = n || 1; w = w || 1;
    const rng = new Proxy({}, { get: (_, k) => {
      if (k === 'setValue') return (v) => { cells[r + ':' + c] = v; delete formulas[r + ':' + c]; s.last = Math.max(s.last, r); return rng; };
      if (k === 'setFormula') return (v) => { formulas[r + ':' + c] = v; cells[r + ':' + c] = ''; return rng; };
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
const deals = sheet();
const D = (s) => new Date(s + 'T12:00:00Z');
// №1 OWC (продано 22.10 за 103 — без пересилки покупця), №2 Corsair 3200 (09.10, 149), №4 Corsair 2133 («продано» 01.11 — скасовано),
// №5 Corsair 2400 (30.10, 160), №6 — та сама модель, друга штука лота SKU ledger-5 (ще «Виставлено»), №8 G.Skill (виставлено)
const ROWS = {
  1: ['OWC 32GB (2x16GB) DDR4-2400 SO-DIMM', 'Продано', '2026-10-22', 103, '(eBay 336849525826)'],
  2: ['Corsair Vengeance LPX 32GB DDR4-3200', 'Продано', '2026-10-09', 149, '(eBay 336837002037)'],
  4: ['2x Corsair Vengeance LPX DDR4 RAM 32GB (2x16GB) 2133MHz', 'Продано', '2026-11-01', 163, 'виставлено за 163 € (eBay 336844624233)'],
  5: ['Corsair Vengeance DDR4 32 GB (2 x 16GB) 2400', 'Продано', '2026-10-30', 160, '(eBay 336849011147)'],
  6: ['Corsair Vengeance DDR4 32 GB (2 x 16GB) 2400', 'Виставлено', '', '', 'друга штука лота №5 (eBay 336849011147)'],
  8: ['G.SKILL FORTIS 32GB (2x16GB) DDR4-2400', 'Виставлено', '', '', '(eBay 336849477931)'] };
const ctx = {
  console: { log: () => {} },
  Utilities: { formatDate: (d, tz, f) => (f === 'dd.MM' ? '03.11' : d.toISOString().slice(0, 10)), getUuid: () => 'u',
    Charset: { UTF_8: 'utf8' },
    computeHmacSha256Signature: (msg, key) => signed(crypto.createHmac('sha256', key).update(msg, 'utf8').digest()),
    newBlob: (x) => (typeof x === 'string' ? { getBytes: () => signed(Buffer.from(x, 'utf8')) }
      : { getDataAsString: () => Buffer.from(x.map((b) => b & 255)).toString('utf8') }),
    base64Encode: (bytes) => Buffer.from(bytes.map((b) => b & 255)).toString('base64'),
    base64Decode: (s) => signed(Buffer.from(s, 'base64')) },
  LockService: { getScriptLock: () => ({ waitLock() {}, tryLock() { return true; }, releaseLock() {} }) },
  SpreadsheetApp: { flush() {} },
  UrlFetchApp: { fetch: () => ({ getResponseCode: () => 200, getContentText: () => '{}' }) },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => (k in store ? store[k] : null),
    setProperty: (k, v) => { store[k] = String(v); }, deleteProperty: (k) => { delete store[k]; } }) },
};
const triggers = [], made = [];
ctx.ScriptApp = { getProjectTriggers: () => triggers.slice(), deleteTrigger: (t) => triggers.splice(triggers.indexOf(t), 1),
  newTrigger: (f) => { const b = { timeBased: () => b, everyMinutes: () => b, atHour: () => b, nearMinute: () => b, everyDays: () => b,
    inTimezone: () => b, create: () => { const t = { getHandlerFunction: () => f }; triggers.push(t); made.push(f); return t; } }; return b; } };
ctx.ContentService = { MimeType: { JSON: 'json' }, createTextOutput: (t) => ({ setMimeType: () => ({ json: JSON.parse(t) }) }) };
ctx.HtmlService = { createHtmlOutput: (t) => ({ html: t }) };
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
const COL = vm.runInContext('COL', ctx);
Object.keys(ROWS).forEach((n) => {
  const r = Number(n) + 4, v = ROWS[n];
  deals.cells[r + ':2'] = D('2026-10-10'); deals.cells[r + ':3'] = v[0]; deals.cells[r + ':11'] = v[1]; deals.cells[r + ':10'] = 90;
  if (v[2]) deals.cells[r + ':' + COL.sdate] = D(v[2]);
  if (v[3]) { deals.cells[r + ':' + COL.sprice] = v[3]; deals.cells[r + ':' + COL.sto] = 'eBay'; }
  deals.cells[r + ':' + COL.note] = v[4];
  deals.last = Math.max(deals.last, r);
});
[3, 7].forEach((n) => { deals.cells[(n + 4) + ':3'] = 'Скасований рядок ' + n; deals.cells[(n + 4) + ':11'] = 'Скасовано'; });
let packs = 0;
vm.runInContext('ledger_ = function () { return { getSheetByName: () => __d }; }; notify_ = function (t, k) { __sent.push([t, k]); };' +
  'packCheck_ = function () { __pack(); }; tgOffice_ = function () { return {}; };',
  Object.assign(ctx, { __d: deals, __sent: sent, __pack: () => packs++ }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };
const cell = (n, c) => deals.cells[(n + 4) + ':' + COL[c]];
const last = () => (sent[sent.length - 1] || [''])[0];
const say = (text) => vm.runInContext('officeMessage(__m)', Object.assign(ctx, { __m: { text: text, chat: { id: 7 } } }));

// 1) звірка таблиці з фінансовим звітом eBay (один раз): ціна з пересилкою покупця, етикетка, реклама; №4 — назад у продаж
ctx.fix1103_();
check('№1/№2: price with buyer shipping, label', [cell(1, 'sprice'), cell(1, 'sship'), cell(2, 'sprice'), cell(2, 'sship')], [109.49, 6.19, 155.49, 6.19]);
check('№5/№6: label + ad fee', [cell(5, 'sship'), cell(5, 'comm')], [5.79, 9.52]);
check('№6 not sold yet — untouched', [cell(6, 'status'), cell(6, 'sprice')], ['Виставлено', undefined]);
check('№4 unsold, new listing', [cell(4, 'status'), cell(4, 'sprice'), cell(4, 'sdate'), /перевиставлено за 163 € 03.11 \(eBay 336873413243\)/.test(cell(4, 'note')),
  ctx.listingId_(cell(4, 'note'))], ['Виставлено', '', '', true, '336873413243']);
check('fix report', [/№1: ціна 103,00 → 109,49 € \(з пересилкою від покупця\), етикетка — → 6,19 €/.test(last()), /№4: продаж 01\.11 покупець скасував/.test(last()),
  /№6: у таблиці не «Продано»/.test(last())], [true, true, true]);
const nFix = sent.length;
ctx.fix1103_();
check('fix once', sent.length, nFix);

// 2) продаж №6 без листа: SKU ledger-5 (№5 уже продано) → номер оголошення в нотатках → №6, ціна, дата, «запакуй»
const sale6 = { order_id: '03-15393-72038', item_id: '336849011147', sku: 'ledger-5', price: 169, qty: 1, buyer: 'leomko', date: '2026-11-03',
  title: 'Corsair Vengeance LPX 32GB (2x16GB) DDR4-2400 CMK32GX4M2A2400C16 getestet' };
const old5 = { order_id: '12-15361-67725', item_id: '336849011147', sku: 'ledger-5', price: 160, qty: 1, buyer: 'mr.wombat', date: '2026-10-30', title: 'x' };
vm.runInContext('syncApply_(__p)', Object.assign(ctx, { __p: { lots: [], ask: [], ended: [{ n: 6, id: '336849011147' }], sales: [old5, sale6] } }));
check('№6 sold from eBay data', [cell(6, 'status'), cell(6, 'sprice'), cell(6, 'sto'), ctx.iso_(cell(6, 'sdate')), packs], ['Продано', 169, 'eBay', '2026-11-03', 1]);
check('sale card', [/💰 Продано на eBay №6: Corsair Vengeance LPX[\s\S]*169 € \(покупець leomko, замовлення 03-15393-72038\)[\s\S]*Листа від eBay не було/.test(
  sent.filter((x) => /Продано на eBay №6/.test(x[0]))[0][0]), sent.some((x) => /№6: оголошення на eBay більше не активне/.test(x[0]))], [true, false]);
check('order → row', [ctx.orderRows_()['03-15393-72038'][0], ctx.orderRows_()['12-15361-67725'][0]], [6, 5]);
check('№5 untouched', [cell(5, 'sprice'), cell(5, 'status')], [160, 'Продано']);
const nSale = sent.length;
vm.runInContext('syncApply_(__p)', Object.assign(ctx, { __p: { lots: [], ask: [], ended: [], sales: [old5, sale6] } }));
check('sale recorded once', [sent.length, packs], [nSale, 1]);
// лист eBay прийшов пізніше — не записує вдруге (і не бере іншу штуку)
check('mail after sync = duplicate', [ctx.syncedSale_(['336849011147'], D('2026-11-04')), ctx.syncedSale_(['336849011147'], D('2026-11-09'))], [6, 0]);

// 3) етикетка й реклама нового замовлення — на №6, а не на №5 (SKU ledger-5); відправка — теж №6
vm.runInContext('syncApply_(__p)', Object.assign(ctx, { __p: { lots: [], ask: [], ended: [],
  fees: [{ order_id: '03-15393-72038', item_id: '336849011147', sku: 'ledger-5', label: 5.79, fees: 10.06 }],
  shipped: [{ order_id: '03-15393-72038', item_id: '336849011147', sku: 'ledger-5', track: '02307144017733', carrier: 'Hermes', date: '2026-11-03' }] } }));
check('fees on №6', [cell(6, 'sship'), cell(6, 'comm'), cell(5, 'comm')], [5.79, 10.06, 9.52]);
check('shipped №6', [ctx.events_()[6].outTrack, (ctx.events_()[5] || {}).outTrack], ['02307144017733', undefined]);

// 3b) 06.11: «відправив» натиснули раніше за звірку — відправка без треку; звірка дописує трек з eBay, один раз
ctx.setEvents_(5, { sent: '2026-10-31' });
vm.runInContext('syncApply_(__p)', Object.assign(ctx, { __p: { lots: [], ask: [], ended: [],
  shipped: [{ order_id: '12-15361-67725', item_id: '336849011147', sku: 'ledger-5', track: '02304112003507', carrier: 'Hermes', date: '2026-10-31' }] } }));
check('track filled after manual sent', [ctx.events_()[5].outTrack, ctx.events_()[5].outCarrier, ctx.events_()[5].sent], ['02304112003507', 'Hermes', '2026-10-31']);
check('track message', /📮 №5 .*трек з eBay: 02304112003507 \(Hermes\), записав/.test(last()), true);
const nTrack = sent.length;
vm.runInContext('syncApply_(__p)', Object.assign(ctx, { __p: { lots: [], ask: [], ended: [],
  shipped: [{ order_id: '12-15361-67725', item_id: '336849011147', sku: 'ledger-5', track: '02304112003507', carrier: 'Hermes', date: '2026-10-31' }] } }));
check('track once', sent.length, nTrack);
// «відправив 1» без треку, продаж на eBay: звірка одразу, замість «додай трек» — «візьму з eBay сам»
const disp = [];
store.EBAY_RT = 'rt';
vm.runInContext('sellDispatch_ = function (d) { __disp.push(d); return 204; };', Object.assign(ctx, { __disp: disp }));
say('відправив 1');
check('sent without track pulls from eBay', [disp.map((d) => d.mode), /Трек візьму з eBay сам/.test(last()), /Додай трек/.test(last())], [['sync'], true, false]);
say('відправив 1 02304112003507');
check('sent with track — no extra sync', disp.filter((d) => d.mode === 'sync').length, 1);
delete store.EBAY_RT;

// 4) скасоване замовлення → рядок назад у продаж (та сама дата й ціна); вже скасоване — вдруге не чіпаємо
deals.cells['12:11'] = 'Продано'; deals.cells['12:' + COL.sdate] = D('2026-11-02'); deals.cells['12:' + COL.sprice] = 169;   // №8
deals.cells['12:' + COL.sto] = 'eBay';
vm.runInContext('syncApply_(__p)', Object.assign(ctx, { __p: { lots: [], ask: [], ended: [],
  cancelled: [{ order_id: '99-1', item_id: '336849477931', sku: 'ledger-8', price: 150, date: '2026-11-02' },
              { order_id: '99-2', item_id: '336849477931', sku: 'ledger-8', price: 169, date: '2026-11-02' }] } }));
check('cancelled → unsold (price must match)', [cell(8, 'status'), cell(8, 'sprice'), /↩️ №8 .*продаж на eBay скасовано \(замовлення 99-2/.test(last())],
  ['Перевірено', '', true]);
check('cancelled order blocks sale/fees', [ctx.orderRows_()['99-2'][0], ctx.orderRows_()['99-1'][0]], [0, 0]);
vm.runInContext('syncApply_(__p)', Object.assign(ctx, { __p: { lots: [], ask: [], ended: [],
  sales: [{ order_id: '99-2', item_id: '336849477931', sku: 'ledger-8', price: 169, qty: 1, date: '2026-11-02', title: 'G.Skill' }] } }));
check('cancelled order not re-recorded', cell(8, 'status'), 'Перевірено');

// 5) «Продано», а лот живий → питання з кнопкою «↩️ Не продано»; кнопка → очищує продаж
deals.cells['12:11'] = 'Продано'; deals.cells['12:' + COL.sprice] = 160;
vm.runInContext('syncApply_(__p)', Object.assign(ctx, { __p: { lots: [], ask: [], ended: [], sold_active: [{ n: 8, id: '336849477931', price: 169, title: 'G.SKILL' }] } }));
check('sold but active → ask', [/⚠️ №8 .*«Продано», але на eBay цей лот ще активний за 169 €/.test(last()), sent[sent.length - 1][1].inline_keyboard[0][0].callback_data],
  [true, 'c|непродано|8']);
const nAsk = sent.length;
vm.runInContext('syncApply_(__p)', Object.assign(ctx, { __p: { lots: [], ask: [], ended: [], sold_active: [{ n: 8, id: '336849477931', price: 169 }] } }));
check('asked once', sent.length, nAsk);
ctx.officeCallback({ id: 'q', data: 'c|непродано|8', message: { chat: { id: 7 }, message_id: 5 } });
check('unsold by button', [cell(8, 'status'), cell(8, 'sprice'), /↩️ №8 .*продаж очистив/.test(last())], ['Перевірено', '', true]);
say('не продано 8');
check('not sold → nothing', /не «Продано»/.test(last()), true);
say('назад 8');
check('undo restores status', cell(8, 'status'), 'Продано');

// 6) продаж без рядка (номера оголошення ніде немає) — питає «продав N», нічого не записує
vm.runInContext('syncApply_(__p)', Object.assign(ctx, { __p: { lots: [], ask: [], ended: [],
  sales: [{ order_id: '55-1', item_id: '111', sku: '', price: 99, qty: 1, date: '2026-11-03', title: 'Kingston Fury 2x16' }] } }));
check('unknown sale → asks', /Не знаю, який це рядок[\s\S]*«продав N 99 ebay»/.test(last()), true);
// давні (до 02.11) продажі — не чіпаємо
vm.runInContext('syncApply_(__p)', Object.assign(ctx, { __p: { lots: [], ask: [], ended: [],
  sales: [{ order_id: '44-1', item_id: '336849477931', sku: 'ledger-8', price: 99, qty: 1, date: '2026-10-20', title: 'x' }] } }));
check('old orders ignored', ctx.orderRows_()['44-1'], undefined);

// 6b) 03.11 вечір: перший запуск обліку після простою взяв старий лист про продаж №6 (вже «Продано») і за назвою записав №4
// (Corsair LPX 2400 ↔ 2133; №4 щойно повернувся в продаж з власним лотом). Тепер: замовлення відоме / оголошення лише в
// проданих / рядок з іншим лотом — не цей продаж.
deals.cells['8:11'] = 'Виставлено'; ['sdate', 'sto', 'sprice'].forEach((c) => { deals.cells['8:' + COL[c]] = ''; });
deals.cells['12:11'] = 'Виставлено';   // №8 знову в продажу
store.EXPENSES_V1 = '1';
check('known order', ctx.knownSale_({ order: '03-15393-72038' }), 6);
check('listing only in sold rows', ctx.knownSale_({ order: '', id: '336849011147', ids: [] }), 6);
check('listing with an open row — not known', ctx.knownSale_({ order: '', id: '336849477931', ids: [] }), 0);
check('title never takes a row with another lot', ctx.saleRowByTitle_('Corsair Vengeance LPX 32GB (2x16GB) DDR4-2400 CMK32GX4M2A2400C16',
  ['336849011147']).row, 0);
const mailMsg = { getId: () => 'm6', getFrom: () => 'eBay <ebay@ebay.de>', getDate: () => new Date('2026-11-03T11:05:00Z'),
  getSubject: () => '🎉 Artikel verkauft - Corsair Vengeance LPX 32GB (2x16GB) DDR4-2400 CMK32...',
  getPlainBody: () => 'Gute Neuigkeiten - Ihr Artikel wurde verkauft!\nCorsair Vengeance LPX 32GB (2x16GB) DDR4-2400 CMK32...\nVerkauft:\n' +
    'EUR 169,00\nBestellnummer: 03-15393-72038\nhttps://www.ebay.de/itm/336849011147', getBody: () => '' };
const logSh = sheet();
logSh.appendRow = (r) => { logSh.cells[(logSh.last + 1) + ':5'] = r[4]; logSh.last++; };
logSh.last = 1;
vm.runInContext('ledger_ = function () { return { getSheetByName: function (n) { return n === "Лог листів" ? __log : (n === "Угоди" ? __dd : null); } }; };' +
  'GmailApp = { search: function () { return [{ getMessages: function () { return [__m6]; } }]; } };',
  Object.assign(ctx, { __log: logSh, __dd: deals, __m6: mailMsg }));
store.LEDGER_SCANNED = '1';
// як у житті: №6 записала одноразова звірка (fix1103_) — без номера оголошення й позначки «sync»
store.ORDER_ROW = JSON.stringify(Object.assign(JSON.parse(store.ORDER_ROW), { '03-15393-72038': [6, '2026-11-03', ''] }));
const nMail = sent.length;
try { ctx.processLedger(); } catch (e) { console.log('processLedger: ' + e); }
check('old sale mail → nothing recorded', [cell(4, 'status'), cell(8, 'status'), logSh.cells['2:5']], ['Виставлено', 'Виставлено',
  'продаж уже записано зі звірки eBay (№6)']);
check('no sale card', sent.slice(nMail).some((x) => /💰 Продано №/.test(x[0])), false);
vm.runInContext('ledger_ = function () { return { getSheetByName: () => __d }; };', ctx);
// одноразово: №4 записаний тим листом (169 €, 03.11) → назад; №9 з рук — без «відправ»
deals.cells['8:11'] = 'Продано'; deals.cells['8:' + COL.sprice] = 169; deals.cells['8:' + COL.sdate] = D('2026-11-03');
deals.cells['13:3'] = 'Patriot Viper 4 2x4GB'; deals.cells['13:11'] = 'Продано'; deals.cells['13:' + COL.sdate] = D('2026-11-01'); deals.last = 13;
delete store.FIX1103B;   // уже відпрацювала в processLedger вище (тоді ще нічого виправляти)
ctx.fix1103b_();
check('fix1103b', [cell(4, 'status'), cell(4, 'sprice'), ctx.events_()[9].handed, /№4: лист eBay про продаж №6/.test(last())], ['Виставлено', '', 1, true]);

// 7) стан ззовні: тригерів немає / давно не працювали → перевстановити (раз на 2 год) і написати; відповідь — JSON у doPost
store.LAST_CHECK_OK = String(Date.now() - 600 * 60000); store.LEDGER_OK = String(Date.now() - 650 * 60000);
const res = ctx.healthCheck_(1);
check('heal', [res.stale, made.sort().join(), /🔧 Таймери Apps Script не спрацьовували \(пошта KA — 600 хв, облік — 650 хв/.test(last())],
  [true, 'check,dailyReport,kaFilter,processLedger,watchdog', true]);
const res2 = ctx.healthCheck_(1);
check('heal throttled', [res2.stale, res2.healed.length, made.length], [true, 0, 5]);
store.LAST_CHECK_OK = String(Date.now()); store.LEDGER_OK = String(Date.now()); store.WD_STATE = JSON.stringify({ at: Date.now() });
check('healthy', ctx.healthCheck_(1).stale, false);
const blob = ctx.seal_({ kind: 'health', heal: 0 }, 'O', 'n1');
const out = ctx.doPost({ postData: { contents: JSON.stringify({ sealed: blob }) }, parameter: { bot: 'office' } });
check('doPost answers JSON', [out.json && out.json.stale, out.json && out.json.triggers.length], [false, 5]);
check('telegram update still html', ctx.doPost({ postData: { contents: JSON.stringify({ update_id: 1 }) }, parameter: {} }).html, 'ok');

console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
