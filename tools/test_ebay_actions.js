// Дії в eBay кнопками (node tools/test_ebay_actions.js): трек з «відправив», «📨 Надіслати», «Прийняти / Зустрічна / Відхилити»,
// «⬇️ Знизити» з тижневого звіту, «знижено» назад в облік, «✅ Межі оновив».
const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');
const signed = (buf) => Array.from(buf).map((b) => (b > 127 ? b - 256 : b));
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: '123:ABC', TELEGRAM_BOT_TOKEN: 'M', GITHUB_TOKEN: 'g' };
const tg = [], sent = [], gh = [];
function sheet() {
  const cells = {};
  const s = { cells, last: 4, getLastRow: () => s.last };
  s.getRange = (r, c, n, w) => {
    n = n || 1; w = w || 1;
    const rng = new Proxy({}, { get: (_, k) => {
      if (k === 'setValue') return (v) => { cells[r + ':' + c] = v; s.last = Math.max(s.last, r); return rng; };
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
[['Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200', 'Перевірено', 70.63], ['PS5 Slim', 'Продано', 300],
 ['Kingston 16GB DDR4', 'Отримано', 20]].forEach((v, i) => {
  const r = 5 + i;
  deals.cells[r + ':2'] = new Date('2026-10-01T10:00:00Z'); deals.cells[r + ':3'] = v[0]; deals.cells[r + ':4'] = 'RAM';
  deals.cells[r + ':11'] = v[1]; deals.cells[r + ':10'] = v[2]; deals.last = r;
});
let dispatchCode = 204;
const ctx = {
  console: { log: () => {} },
  Utilities: {
    Charset: { UTF_8: 'utf8' },
    computeHmacSha256Signature: (msg, key) => signed(crypto.createHmac('sha256', key).update(msg, 'utf8').digest()),
    newBlob: (x) => (typeof x === 'string' ? { getBytes: () => signed(Buffer.from(x, 'utf8')) }
      : { getDataAsString: () => Buffer.from(x.map((b) => b & 255)).toString('utf8') }),
    base64Encode: (bytes) => Buffer.from(bytes.map((b) => b & 255)).toString('base64'),
    base64Decode: (s) => signed(Buffer.from(s, 'base64')),
    getUuid: () => crypto.randomUUID(),
    formatDate: () => '09.10',
  },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => (k in store ? store[k] : null),
    setProperty: (k, v) => { store[k] = v; }, deleteProperty: (k) => { delete store[k]; } }) },
  LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
  HtmlService: { createHtmlOutput: (x) => x },
  UrlFetchApp: { fetch: (u, o) => {
    if (/api\.github\.com/.test(u)) { gh.push(JSON.parse(o.payload).inputs); return { getResponseCode: () => dispatchCode, getContentText: () => '' }; }
    if (/api\.telegram\.org/.test(u)) tg.push([u.split('/').pop(), (o || {}).payload]);
    return { getResponseCode: () => 200, getContentText: () => JSON.stringify({ ok: true, result: {} }) };
  } },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
vm.runInContext(`ledger_ = function () { return { getSheetByName: () => __deals }; };
  notify_ = function (t, mk) { __sent.push([t, mk]); };`, Object.assign(ctx, { __deals: deals, __sent: sent }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };
const say = (text, id) => ctx.officeMessage({ text, chat: { id: 7 }, message_id: id || 0 });
const last = () => sent[sent.length - 1];
const opened = () => ctx.unseal_(gh[gh.length - 1], store.OFFICE_BOT_TOKEN);
const cb = (data, mid, markup) => ctx.officeCallback({ id: 'q', data, message: { chat: { id: 7 }, message_id: mid || 70, reply_markup: markup } });
const datas = (mk) => (mk ? mk.inline_keyboard.flat().map((b) => b.callback_data || b.url) : []);

// без ключа eBay — кнопок немає, трек нікуди не йде
deals.cells['6:13'] = 'eBay';
check('no buttons without key', ctx.ebayMailButtons_({ kind: 'question', reply_de: 'Hallo', role: 'seller' }, null), null);
say('відправив 2 00340434161234567890');
check('no ship dispatch without key', gh.length, 0);

store.EBAY_RT = 'v^1.1#RT';
// трек проданого на eBay → ship
say('відправив 2 00340434161234567891');
let p = opened();
check('ship dispatched', [p.mode, p.action, p.row, p.track, p.carrier, p.rt], ['ebay', 'ship', 2, '00340434161234567891', 'DHL', 'v^1.1#RT']);
check('told', /Передаю трек в eBay/.test(last()[0]), true);
// продано на KA — у eBay не шлемо
deals.cells['6:13'] = 'Kleinanzeigen';
const g0 = gh.length;
say('трек 2 00340434161234567892');
check('KA sale: no eBay', gh.length, g0);
deals.cells['6:13'] = 'eBay';

// питання покупця → кнопка «📨» → дія answer з чернеткою; кнопки з картки прибрано, посилання лишилось
const qm = ctx.ebayMailButtons_({ kind: 'question', role: 'seller', reply_de: 'Hallo, ja, läuft mit XMP.', message: 'Läuft XMP?',
  item: 'Corsair Vengeance' }, { inline_keyboard: [[{ text: '🔗', url: 'https://ebay.de/x' }]] });
check('question buttons', [datas(qm).length, /^e\|\d+\|send$/.test(datas(qm)[0]), datas(qm)[1]], [2, true, 'https://ebay.de/x']);
cb(datas(qm)[0], 71, qm);
p = opened();
check('answer dispatched', [p.action, p.op, p.text, p.question, p.title], ['answer', 'send', 'Hallo, ja, läuft mit XMP.', 'Läuft XMP?', 'Corsair Vengeance']);
const ed = tg.filter((c) => c[0] === 'editMessageReplyMarkup').pop();
check('buttons removed, link kept', JSON.parse(ed[1].reply_markup).inline_keyboard, [[{ text: '🔗', url: 'https://ebay.de/x' }]]);

// пропозиція покупця → 3 кнопки; «🔁» → op cnt
const om = ctx.ebayMailButtons_({ kind: 'offer', role: 'seller', amount: 140, item: 'Corsair Vengeance' }, null);
check('offer buttons', datas(om).map((d) => d.split('|')[2]), ['acc', 'cnt', 'dec']);
cb(datas(om)[1], 72, om);
p = opened();
check('offer counter dispatched', [p.action, p.op, p.amount], ['offer', 'cnt', 140]);
// пропозиція, де користувач — покупець: кнопок немає
check('buyer side: no buttons', ctx.ebayMailButtons_({ kind: 'offer', role: 'buyer', amount: 50 }, null), null);
// чужий чат і застарілий номер
const g1 = gh.length;
ctx.officeCallback({ id: 'q', data: datas(om)[0], message: { chat: { id: 99 }, message_id: 73 } });
cb('e|99999|acc', 74);
check('foreign chat / stale: nothing dispatched', [gh.length, /застаріла/.test(last()[0])], [g1, true]);

// «⬇️ Знизити» з тижневого звіту → revise з ціною; назад «repriced» → нотатка
cb('p|1|140', 75);
p = opened();
check('revise dispatched', [p.action, p.row, p.price, p.title], ['revise', 1, 140, 'Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200']);
ctx.sealedPost(ctx.seal_({ kind: 'repriced', row: 1, price: 140, item_id: '1234567890' }, '123:ABC', 'n5'));
check('repriced note', /ціна знижена до 140 € 09.10 \(eBay 1234567890\)/.test(deals.cells['5:' + vm.runInContext('COL.note', ctx)]), true);
// номер eBay з нотаток іде в наступні дії
cb('p|1|130', 76);
check('item id from note', opened().item_id, '1234567890');

// «✅ Межі оновив» → ka_bounds.yml
cb('c|межі|1', 77);
check('bounds dispatched', [gh[gh.length - 1].mode, /Записую нові межі/.test(last()[0])], ['accept', true]);
say('межі');
check('bounds show', [gh[gh.length - 1].mode, /Рахую межі/.test(last()[0])], ['show', true]);

// тижневий звіт знає, що eBay підключено (кнопки «⬇️»)
ctx.weeklyReport();
check('weekly: ebay flag', opened().ebay, true);
// 10.10: «📋 Покроково» під карткою «продати» → steps:true з фото; кнопки картки лишаються
store.PH_1 = JSON.stringify([{ id: 'P1', t: 'photo', u: 'u1' }]);
cb('c|кроки|1', 78);
p = opened();
check('steps dispatched', [p.steps, p.row, p.photos.length, p.mode], [true, 1, 1, undefined]);
check('card buttons kept on steps', tg.some((c) => c[0] === 'editMessageReplyMarkup' && c[1].message_id === 78), false);
// план продажу після «📒 Записав покупку»: id повідомлення → GitHub mode=plan з текстом і кнопками
ctx.planAfter_({ getContentText: () => JSON.stringify({ ok: true, result: { message_id: 555 } }) }, 3, 'Kingston 16GB DDR4', 20, '📒 Записав покупку №3', { inline_keyboard: [] });
p = opened();
check('plan dispatched', [p.mode, p.mid, p.row, p.cost, p.text], ['plan', 555, 3, 20, '📒 Записав покупку №3']);
const g2 = gh.length;
ctx.planAfter_(null, 3, 'x', 1, 't', null);
check('no message id → no plan', gh.length, g2);
// 10.10: фото квитанції з номером → «відправлено», трек у eBay і подяка покупцю (лише один раз)
deals.cells['7:11'] = 'Продано'; deals.cells['7:13'] = 'eBay';
let line = ctx.receiptShipped_(3, { kind: 'receipt', receipt: { track: '0034 0434 1612 3456 7890', carrier: 'DHL' } });
p = opened();
check('receipt → ship + thanks', [p.action, p.row, p.track, p.carrier, p.thanks], ['ship', 3, '00340434161234567890', 'DHL', true]);
check('receipt line', /🧾 Квитанція: DHL 00340434161234567890 → «відправлено» · ⏳ Передаю трек в eBay і подяку покупцю/.test(line), true);
check('receipt event', JSON.parse(store.LEDGER_EVENTS)[3].outTrack, '00340434161234567890');
ctx.receiptShipped_(3, { kind: 'receipt', receipt: { track: '00340434161234567890', carrier: 'DHL' } });
check('thanks only once', opened().thanks, false);
check('no track read', /трек не прочитав/.test(ctx.receiptShipped_(3, { kind: 'receipt', receipt: { track: '' } })), true);
const g3 = gh.length;
check('not sold → hint, nothing sent', [/не позначений проданим/.test(ctx.receiptShipped_(1, { kind: 'receipt', receipt: { track: '00340434161234567899' } })), gh.length], [true, g3]);
// відгук після продажу
ctx.feedbackToEbay_(3, 'Kingston 16GB DDR4');
check('feedback dispatched', [opened().action, opened().row], ['feedback', 3]);
// повідомлення покупця після покупки — теж з кнопкою «📨»
const mm = ctx.ebayMailButtons_({ kind: 'message', role: 'seller', reply_de: 'Hallo, das Paket geht heute raus.', message: 'Wann versenden Sie?' }, null);
check('buyer message has send button', /^e\|\d+\|send$/.test(datas(mm)[0]), true);
// пакування за замовчуванням 4 → 1 € (лише якщо там 4), один раз
const setSheet = { cells: { 'B5': 4 }, getRange: (a) => ({ getValue: () => setSheet.cells[a], setValue: (v) => { setSheet.cells[a] = v; } }) };
vm.runInContext('ledger_ = function () { return { getSheetByName: (n) => (n === "Налаштування" ? __set : __deals) }; };', Object.assign(ctx, { __set: setSheet }));
ctx.packOne_();
setSheet.cells.B5 = 3; ctx.packOne_();
check('pack default 1 once', [setSheet.cells.B5, store.PACK_ONE], [3, '1']);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
