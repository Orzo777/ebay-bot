// Автопублікація на eBay (node tools/test_publish.js): «ebay вхід», код з адреси, ключ назад з GitHub (sealedPost),
// кнопка «🚀» → підтвердження → запуск, захист від повторного натискання, «виставлено» в облік.
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

// Python → JS: той самий вектор, що в test_sell.py (PY_VECTOR) — sell.post_back шифрує, sealedPost розшифровує
check('python vector', ctx.unseal_({ blob: '0c8A4OJb57wcp3jldRyU2WGjqaHFc/fY8aoTHvgmlJJqabu2mlrMKIAOPlI/JAOifwxfrtw2zL8pfxBWURIS4Kb9',
  mac: 'f184639636b9b2f66b20581ff6e831cc', nonce: 'n0nce2' }, '123:ABC'), { kind: 'listed', row: 3, price: 96, item_id: '1234567890' });
let threw = false;
try { ctx.unseal_({ blob: 'AAAA', mac: '00', nonce: 'x' }, '123:ABC'); } catch (e) { threw = true; }
check('bad mac rejected', threw, true);

// без ключа: «🚀» → просить підключити, нічого не запускає
say('авто 1');
check('not connected', [/ebay вхід/.test(last()[0]), gh.length], [true, 0]);
say('продати 1');
check('sell card: ebay flag false before connect', opened().ebay, false);

// «ebay вхід» → GitHub auth_url
say('ebay вхід');
check('auth_url dispatched', [opened().mode, /посилання/.test(last()[0])], ['auth_url', true]);
// вставлена адреса з кодом → auth_code, повідомлення з кодом видалено
const url = 'https://signin.ebay.de/ws/eBayISAPI.dll?ThirdPartyAuthSucessFailure&isAuthSuccessful=true&code=v%5E1.1%23i%5E1%23abc&expires_in=299';
say(url, 41);
check('auth_code dispatched with url', [opened().mode, opened().code], ['auth_code', url]);
check('message with code deleted', tg.some((c) => c[0] === 'deleteMessage' && c[1].message_id === 41), true);
check('no plain code in GitHub request', JSON.stringify(gh[gh.length - 1]).includes('abc'), false);

// підроблений sealed (інший ключ) — ігнор; справжній — ключ збережено + перевірка без публікації на №1 (Перевірено, з фото)
store.PH_1 = JSON.stringify([{ id: 'P1', t: 'photo', u: 'u1', k: '1x1:1' }]);
const fake = ctx.seal_({ kind: 'ebay_rt', rt: 'EVIL' }, 'other', 'n1');
ctx.doPost({ postData: { contents: JSON.stringify({ sealed: fake }) }, parameter: { bot: 'office' } });
check('forged ignored', store.EBAY_RT, undefined);
const n0 = gh.length;
ctx.doPost({ postData: { contents: JSON.stringify({ sealed: ctx.seal_({ kind: 'ebay_rt', rt: 'v^1.1#RT', exp: 47304000 }, '123:ABC', 'n2') }) },
  parameter: { bot: 'office' } });
check('rt stored', store.EBAY_RT, 'v^1.1#RT');
check('auto dry check on №1', [gh.length, opened().mode, opened().dry, opened().row, opened().rt], [n0 + 1, 'publish', true, 1, 'v^1.1#RT']);
check('told connected', sent.some((x) => /eBay підключено/.test(x[0])), true);

// картка «продати» тепер з кнопкою (прапорець ebay), назва з «продати N <назва>» — для автопублікації
say('продати 1 Corsair Vengeance LPX 2x16GB DDR4 3200 CMK32GX4M2B3200C16');
check('sell card: ebay flag true', opened().ebay, true);

// «🚀» → підтвердження з кнопкою; «✅ Так» (callback) → publish з фото, назвою з картки, ціною купівлі
ctx.officeCallback({ id: 'q', data: 'c|авто|1', message: { chat: { id: 7 }, message_id: 60 } });
check('confirm asked, card buttons kept', [/Виставити №1 на eBay автоматично/.test(last()[0]),
  last()[1].inline_keyboard[0][0].callback_data, tg.some((c) => c[0] === 'editMessageReplyMarkup' && c[1].message_id === 60)],
  [true, 'c|так-авто|1', false]);
ctx.officeCallback({ id: 'q', data: 'c|так-авто|1', message: { chat: { id: 7 }, message_id: 61 } });
const p = opened();
check('publish dispatched', [p.mode, p.dry, p.row, p.title, p.cost, p.photos.length, p.rt],
  ['publish', false, 1, 'Corsair Vengeance LPX 2x16GB DDR4 3200 CMK32GX4M2B3200C16', 70.63, 1, 'v^1.1#RT']);
check('confirm button removed', tg.some((c) => c[0] === 'editMessageReplyMarkup' && c[1].message_id === 61), true);
const n1 = gh.length;
ctx.officeCallback({ id: 'q', data: 'c|так-авто|1', message: { chat: { id: 7 }, message_id: 61 } });
check('second tap within 15 min → no second listing', [gh.length, /уже виставляється/.test(last()[0])], [n1, true]);

// відмови: продане, без фото
say('опублікувати 2');
check('sold refused', [gh.length, /Продано/.test(last()[0])], [n1, true]);
say('опублікувати 3');
check('no photos refused', [gh.length, /немає фото/.test(last()[0])], [n1, true]);

// GitHub повернув «виставлено» → статус, ціна й номер eBay у нотатках
ctx.doPost({ postData: { contents: JSON.stringify({ sealed: ctx.seal_({ kind: 'listed', row: 1, price: 96, item_id: '1234567890' }, '123:ABC', 'n3') }) },
  parameter: { bot: 'office' } });
check('listed recorded', [deals.cells['5:11'], /виставлено за 96 € 09.10 \(eBay 1234567890\)/.test(deals.cells['5:' + vm.runInContext('COL.note', ctx)])],
  ['Виставлено', true]);
say('опублікувати 1');
check('already listed refused', /Виставлено/.test(last()[0]), true);

// ключ відкликано → видалити
ctx.sealedPost(ctx.seal_({ kind: 'ebay_bad' }, '123:ABC', 'n4'));
check('rt removed', store.EBAY_RT, undefined);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
