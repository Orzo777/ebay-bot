// «📦 Де посилки» (node tools/test_parcels.js): які посилки йдуть у GitHub (до нас — куплене й не отримане; від нас —
// відправлене покупцю, доставлене — ще 3 дні), команда й кнопка, «немає посилок».
const fs = require('fs');
const vm = require('vm');
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O', EBAY_RT: 'rt' };
const sent = [], disp = [], tg = [];
const ctx = {
  console: { log: () => {} },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => store[k] || null, setProperty: (k, v) => { store[k] = v; } }) },
  Utilities: { formatDate: (d) => d.toISOString().slice(0, 10) },
  UrlFetchApp: { fetch: (u, o) => { tg.push([u.split('/').pop(), (o || {}).payload]); return { getResponseCode: () => 200, getContentText: () => '{}' }; } },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
let rows = [
  { n: 1, title: 'Corsair Vengeance LPX 32GB', src: 'eBay', status: 'Продано', id: '', date: '2026-10-01' },
  { n: 2, title: 'OWC 2x16GB DDR4 SO-DIMM', src: 'eBay', status: 'В дорозі', id: '317456789012', date: '2026-09-29' },
  { n: 3, title: 'Corsair 2x16 DDR4 (KA)', src: 'Kleinanzeigen', status: 'Оплачено', id: '', date: '2026-10-11' },
  { n: 4, title: 'Kingston FURY', src: 'eBay', status: 'Отримано', id: '', date: '2026-10-02' },
  { n: 5, title: 'Samsung 2x8 DDR5', src: 'eBay', status: 'Продано', id: '', date: '2026-09-20' },
  { n: 6, title: 'Crucial 2x16 DDR5', src: 'eBay', status: 'Продано', id: '', date: '2026-09-15' }];
const today = new Date().toISOString().slice(0, 10), old = '2026-01-01';
let ev = { 1: { sent: today, outTrack: '00340434000000000001', outCarrier: 'DHL' }, 2: { track: '990502112245', carrier: 'DHL' },
  5: { sent: old, outTrack: 'H1000000000000000001', outCarrier: 'Hermes', outDelivered: old }, 6: { sent: today, outDelivered: today } };
vm.runInContext('ledgerRows_ = function () { return __rows(); }; events_ = function () { return __ev(); };' +
  'notify_ = function (t, mk) { __sent.push([t, mk]); }; sellDispatch_ = function (d) { __disp.push(d); return 204; };',
  Object.assign(ctx, { __rows: () => rows, __ev: () => ev, __sent: sent, __disp: disp }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };
const say = (text) => vm.runInContext('officeMessage(__m)', Object.assign(ctx, { __m: { text: text, chat: { id: 7 } } }));

say('посилки');
const d = disp[0];
check('mode + ebay key', [d.mode, d.rt], ['parcels', 'rt']);
check('incoming: bought, not yet received', d.incoming.map((r) => [r.n, r.track, r.id]), [[2, '990502112245', '317456789012'], [3, '', '']]);
check('outgoing: sent, delivered ≤ 3 days ago', d.outgoing.map((r) => [r.n, r.track, r.delivered]),
      [[1, '00340434000000000001', ''], [6, '', today]]);
check('waiting message', /Дивлюся посилки \(2 до тебе, 2 від тебе\)/.test(sent[0][0]), true);

// кнопка «📦 Де посилки» → та сама команда, кнопки з повідомлення не прибираємо
vm.runInContext('officeCallback(__cq)', Object.assign(ctx, { __cq: { id: 'q', data: 'c|посилки|0', message: { chat: { id: 7 }, message_id: 5 } } }));
check('button runs the command', disp.length, 2);
check('button keeps the keyboard', tg.some((x) => x[0] === 'editMessageReplyMarkup'), false);

rows = rows.filter((r) => r.status !== 'В дорозі' && r.status !== 'Оплачено'); ev = { 5: ev[5] };
say('де посилки');
check('nothing in transit — no GitHub run', [disp.length, /Посилок у дорозі немає/.test(sent[sent.length - 1][0])], [2, true]);
// 12.10: Hermes із KA — 14 цифр; DPD — 14 цифр з нуля; DHL — 12 / 20
check('carrier guess', [ctx.trackArg_('53284131401093').carrier, ctx.trackArg_('01234567890123').carrier, ctx.trackArg_('990502112245').carrier],
      ['Hermes', 'DPD', 'DHL']);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
