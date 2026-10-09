// Кнопки в боті «Облік і продаж» і «на руках» (node tools/test_buttons.js)
const fs = require('fs');
const vm = require('vm');
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O', TELEGRAM_BOT_TOKEN: 'M' };
const tg = [], sent = [], fetched = [];
const pad = (x) => ('0' + x).slice(-2);
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
[['G.Skill Ripjaws 2x16GB DDR4 SO-DIMM', 'Kleinanzeigen', 'В дорозі', 61],
 ['Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200', 'eBay', 'Отримано', 70.63],
 ['SK Hynix 2x16GB DDR4 ECC UDIMM', 'eBay', 'Перевірено', 65.69],
 ['OWC 32GB DDR4 SO-DIMM', 'eBay', 'Продано', 45]].forEach((v, i) => {
  const r = 5 + i;
  deals.cells[r + ':2'] = new Date('2026-10-01T10:00:00Z'); deals.cells[r + ':3'] = v[0]; deals.cells[r + ':4'] = 'RAM';
  deals.cells[r + ':5'] = v[1]; deals.cells[r + ':11'] = v[2]; deals.cells[r + ':10'] = v[3]; deals.last = r;
});
let now = { 'yyyy-MM-dd': '2026-10-09' };
const ctx = {
  console: { log: () => {} },
  Utilities: { formatDate: (d, tz, f) => (Math.abs(d.getTime() - Date.now()) < 60000 ? now[f]
    : { 'yyyy-MM-dd': d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) }[f]) },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => (k in store ? store[k] : null),
    setProperty: (k, v) => { store[k] = v; }, deleteProperty: (k) => { delete store[k]; } }) },
  LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
  HtmlService: { createHtmlOutput: (x) => x },
  UrlFetchApp: { fetch: (u, o) => {
    fetched.push([u, o]);
    const method = u.split('/').pop().split('?')[0];
    if (/api\.telegram\.org/.test(u)) tg.push([method, (o || {}).payload]);
    const body = method === 'getWebhookInfo' ? { ok: true, result: { url: 'https://script.google.com/macros/s/X/exec?bot=office' } } : { ok: true, result: {} };
    return { getResponseCode: () => 200, getContentText: () => JSON.stringify(body) };
  } },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs', 'kafilter.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
vm.runInContext(`ledger_ = function () { return { getSheetByName: () => __deals }; };
  notify_ = function (t, mk) { __sent.push([t, mk]); };`, Object.assign(ctx, { __deals: deals, __sent: sent }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };
const datas = (mk) => (mk ? mk.inline_keyboard.flat().map((b) => b.callback_data || b.url) : []);

// кнопки: по 2 в ряд, callback_data коротше 64 байт
const kb = ctx.btns_([['перевірив', 2], ['проблема', 2], ['продати', 123]]);
check('rows of 2', kb.inline_keyboard.map((r) => r.length), [2, 1]);
check('callback data', datas(kb), ['c|перевірив|2', 'c|проблема|2', 'c|продати|123']);
check('callback ≤ 64 bytes', datas(kb).every((d) => Buffer.byteLength(d) <= 64), true);
check('digest: one main action per row', datas(ctx.digestButtons_([[1, 'ka5'], [2, 'test0'], [2, 'test2'], [6, 'ship1'], ['_', 'todo:x'], [9, 'out7']])),
      ['c|отримав|1', 'c|перевірив|2', 'c|відправив|6']);
check('digest without actions → no keyboard', ctx.digestButtons_([['_', 'todo:x']]), null);

// натискання «✅ Перевірив №2»: статус, кнопки прибрано, відповідь з кнопкою «продати»
const cb = (data, chat) => ({ id: 'q1', data, message: { chat: { id: chat || 7 }, message_id: 55 } });
ctx.officeCallback(cb('c|перевірив|2'));
check('status set', deals.cells['6:11'], 'Перевірено');
check('callback answered + buttons removed', [tg.some((c) => c[0] === 'answerCallbackQuery'),
      tg.some((c) => c[0] === 'editMessageReplyMarkup' && c[1].message_id === 55)], [true, true]);
check('reply has «продати №2» and feedback hint', [/✅ №2 .*перевірено[\s\S]*Bewertung/.test(sent[sent.length - 1][0]), datas(sent[sent.length - 1][1])],
      [true, ['c|продати|2']]);

// чужий чат і сміття в callback_data — нічого не робить
const n0 = sent.length;
deals.cells['5:11'] = 'В дорозі';
ctx.officeCallback(cb('c|отримав|1', 99));
ctx.officeCallback(cb('c|rm -rf|1'));
check('foreign chat / bad data ignored', [sent.length, deals.cells['5:11']], [n0, 'В дорозі']);

// «на руках»
vm.runInContext('officeMessage(__m)', Object.assign(ctx, { __m: { text: 'на руках', chat: { id: 7 } } }));
const oh = sent[sent.length - 1];
check('on hand: 3 open rows, sold excluded', [/На руках: 3/.test(oh[0]), /OWC/.test(oh[0]), /№3 .*\n\s+Перевірено.*можна продавати/.test(oh[0])], [true, false, true]);
check('on hand buttons', datas(oh[1]), ['c|отримав|1', 'c|продати|2', 'c|продати|3']);

// вебхуки: один раз дозволити натискання, без скидання черги
ctx.enableButtons_(); ctx.enableButtons_();
const sets = fetched.filter((f) => /setWebhook/.test(f[0]));
check('setWebhook for both bots once, keeps url, no drop', [sets.length, sets.every((f) => /callback_query/.test(f[1].payload.allowed_updates)
      && f[1].payload.url && !('drop_pending_updates' in f[1].payload))], [2, true]);

// doPost: натискання в боті обліку → officeCallback; в основному боті — ігнор
let called = 0;
vm.runInContext('officeCallback = function () { __hit(); };', Object.assign(ctx, { __hit: () => { called++; } }));
const post = (upd, office) => ctx.doPost({ postData: { contents: JSON.stringify(upd) }, parameter: office ? { bot: 'office' } : {} });
post({ update_id: 1, callback_query: { id: 'x', data: 'c|отримав|1', message: { chat: { id: 7 }, message_id: 1 } } }, true);
post({ update_id: 2, callback_query: { id: 'y', data: 'c|отримав|1', message: { chat: { id: 7 }, message_id: 1 } } }, false);
post({ update_id: 1, callback_query: { id: 'x', data: 'c|отримав|1', message: { chat: { id: 7 }, message_id: 1 } } }, true);   // повтор Telegram
check('doPost routes office callbacks once', called, 1);
// «✅ Купив (самовивіз)» на картці основного бота → рядок в обліку, кнопка зникає, повтор — «уже в обліку»
const cardText = ['🟢 бери', 'DDR4 …', '', 'MSI A320 A Pro, AMD Ryzen 3 3200G'].join('\n');   // офсети Telegram — у UTF-16, як у JS
const mcq = { id: 'm1', data: 'b|3531265450|50', message: { chat: { id: 7 }, message_id: 77,
  text: cardText, entities: [{ type: 'bold', offset: 3, length: 4 },
    { type: 'italic', offset: cardText.indexOf('MSI'), length: 'MSI A320 A Pro, AMD Ryzen 3 3200G'.length }],
  reply_markup: { inline_keyboard: [[{ text: '🔗', url: 'https://k' }], [{ text: '✅ Купив (самовивіз)', callback_data: 'b|3531265450|50' }]] } } };
const rows0 = deals.last;
ctx.mainCallback(mcq);
check('row added', [deals.last, deals.cells[deals.last + ':3'], deals.cells[deals.last + ':7'], deals.cells[deals.last + ':11'], deals.cells[deals.last + ':23']],
      [rows0 + 1, 'MSI A320 A Pro, AMD Ryzen 3 3200G', 50, 'Отримано', 'ka:3531265450']);
const edit = tg.filter((c) => c[0] === 'editMessageReplyMarkup').pop();
check('buy button removed, link kept', JSON.parse(edit[1].reply_markup).inline_keyboard, [[{ text: '🔗', url: 'https://k' }]]);
check('office bot told + test buttons', [/📒 Записав покупку №5 \(самовивіз\)/.test(sent[sent.length - 1][0]), datas(sent[sent.length - 1][1])],
      [true, ['c|перевірив|5', 'c|проблема|5']]);
ctx.mainCallback(mcq);
check('second press → no duplicate', deals.last, rows0 + 1);
ctx.mainCallback(Object.assign({}, mcq, { message: Object.assign({}, mcq.message, { chat: { id: 99 } }) }));
check('foreign chat → nothing', deals.last, rows0 + 1);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
