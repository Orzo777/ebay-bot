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
      if (k === 'getFormula') return () => '';   // 31.10: у тестових клітинках формул немає
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
  LockService: { getScriptLock: () => ({ waitLock() {}, tryLock() { return true; }, releaseLock() {} }) },
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
check('no plain code in GitHub request', ['eBayISAPI', 'isAuthSuccessful', 'i%5E1%23abc'].some((x) => JSON.stringify(gh[gh.length - 1]).includes(x)), false);   // 16.10: «abc» траплялось у випадковому шифрі

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
// 14.10: ✏️ правки перед «🚀» — ціна, назва, опис; після кожної — нова картка з правками; публікація бере їх
ctx.officeCallback({ id: 'q', data: 'c|змінити|1', message: { chat: { id: 7 }, message_id: 62 } });
check('edit help', [/✏️ <b>Змінити оголошення №1<\/b>/.test(last()[0]), /<code>ціна 1 <\/code>/.test(last()[0]),
  tg.some((c) => c[0] === 'editMessageReplyMarkup' && c[1].message_id === 62)], [true, true, false]);
say('ціна 1 139,50');
check('price edit → card with edits', [opened().row, opened().edits], [1, { price: 140 }]);
say('назва 1 Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200 CL16 getestet');
say('опис 1 Mit Originalverpackung.');
check('title + desc kept with price', opened().edits,
  { price: 140, title: 'Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200 CL16 getestet', desc: 'Mit Originalverpackung.' });
const g0 = gh.length;
say('ціна 1 сто');
check('bad price refused', [gh.length, /числом/.test(last()[0])], [g0, true]);
say('назва 1 ' + 'X'.repeat(90));
check('title cut to 80', [JSON.parse(store.SELL_EDITS)[1].title.length, /80 знаків/.test(sent[sent.length - 2][0])], [80, true]);
say('назва 1 Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200 CL16 getestet');
ctx.officeCallback({ id: 'q', data: 'c|так-авто|1', message: { chat: { id: 7 }, message_id: 61 } });
const p = opened();
check('publish carries edits', p.edits.price, 140);
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
check('edits cleared after listing', JSON.parse(store.SELL_EDITS)[1], undefined);
check('listed recorded', [deals.cells['5:11'], /виставлено за 96 € 09.10 \(eBay 1234567890\)/.test(deals.cells['5:' + vm.runInContext('COL.note', ctx)])],
  ['Виставлено', true]);
say('опублікувати 1');
check('already listed refused', /Виставлено/.test(last()[0]), true);

// 16.10: «🔁 Оновити на eBay» — виставлене: картка з номером оголошення, підтвердження, запуск revise з правками, нотатка
say('продати 1');
check('sell card of a listed item knows the eBay id', opened().item_id, '1234567890');
// 17.10: «оригінали N» — лише прапорець; «обробити N» — одразу картка «продати N» (з альбомом оброблених фото)
const ghBefore = gh.length;
say('оригінали 1');
check('raw photos: flag only', [JSON.parse(store.SELL_EDITS)[1].raw, gh.length], [true, ghBefore]);
say('обробити 1');
check('studio again: card dispatched', [JSON.parse(store.SELL_EDITS)[1] && JSON.parse(store.SELL_EDITS)[1].raw, gh.length, opened().row],
  [undefined, ghBefore + 1, 1]);
say('ціна 1 129');
ctx.officeCallback({ id: 'q', data: 'c|оновити|1', message: { chat: { id: 7 }, message_id: 70 } });
check('revise confirm asked, card kept', [/Оновити №1 на eBay: ціна —/.test(last()[0]), last()[1].inline_keyboard[0][0].callback_data,
  tg.some((c) => c[0] === 'editMessageReplyMarkup' && c[1].message_id === 70)], [true, 'c|так-оновити|1', false]);
ctx.officeCallback({ id: 'q', data: 'c|так-оновити|1', message: { chat: { id: 7 }, message_id: 71 } });
const rv = opened();
check('revise dispatched (only the price)', [rv.mode, rv.row, rv.item_id, rv.edits.price, rv.photos.length, rv.rt, rv.parts.join()],
  ['revise', 1, '1234567890', 129, 1, 'v^1.1#RT', 'price']);
ctx.doPost({ postData: { contents: JSON.stringify({ sealed: ctx.seal_({ kind: 'revised', row: 1, price: 129, item_id: '1234567890' }, '123:ABC', 'n5') }) },
  parameter: { bot: 'office' } });
check('revised noted, edits cleared', [/оновлено на eBay 09.10 \(129 €\)/.test(deals.cells['5:' + vm.runInContext('COL.note', ctx)]),
  JSON.parse(store.SELL_EDITS)[1]], [true, undefined]);
// 26.10: 🚚 / 📦 доставка — своя ціна запам'ятовує, при якій доставці задана; виставлене — 🔁 з частиною «доставка»
check('listed card carries rt (live shipping)', opened().rt, 'v^1.1#RT');
say('ціна 1 163');
ctx.officeCallback({ id: 'q', data: 'c|безкоштовна|1', message: { chat: { id: 7 }, message_id: 73 } });
check('free shipping: card with edits, buttons kept', [opened().edits, tg.some((c) => c[0] === 'editMessageReplyMarkup' && c[1].message_id === 73)],
  [{ price: 163, pship: 'paid', ship: 'free' }, false]);
say('ціна 1 169');
check('price set while free', JSON.parse(store.SELL_EDITS)[1], { price: 169, pship: 'free', ship: 'free' });
say('платна 1');
check('back to paid keeps pship', JSON.parse(store.SELL_EDITS)[1], { price: 169, pship: 'free', ship: 'paid' });
say('доставка 1 безкоштовна');
ctx.officeCallback({ id: 'q', data: 'c|так-оновити|1', message: { chat: { id: 7 }, message_id: 74 } });
check('revise with shipping part', opened().parts.join(), 'price,ship');
say('скинути 1');
say('оновити 3');
ctx.officeCallback({ id: 'q', data: 'c|так-оновити|3', message: { chat: { id: 7 }, message_id: 72 } });
check('not listed — no revise', /ще не виставлено автоматично/.test(last()[0]), true);
// виставив вручну з посиланням → номер оголошення в нотатках → «🔁» працює
deals.cells['7:11'] = 'Перевірено';
say('виставив 3 59 https://www.ebay.de/itm/Kingston-16GB/306123456789?hash=x');
check('manual listing with link stores the id', [/виставлено за 59 € 09.10 \(eBay 306123456789\)/.test(deals.cells['7:' + vm.runInContext('COL.note', ctx)]),
  /запам'ятав/.test(last()[0])], [true, true]);
// 17.10: «🔁» — лише змінене: нічого не змінено → підказка; змінились фото → на eBay лише фото
say('оновити 1');
check('nothing changed — no revise', /нічого не змінено/.test(last()[0]), true);
say('видали фото 1');
check('photos deleted', [JSON.parse(store.PH_1 || '[]').length, /Прибрав 1 фото №1/.test(last()[0]), JSON.parse(store.SELL_EDITS)[1].photos],
  [0, true, true]);
say('оновити 1');
check('photos changed — confirm names photos', /Оновити №1 на eBay: фото —/.test(last()[0]), true);
ctx.officeCallback({ id: 'q', data: 'c|так-оновити|1', message: { chat: { id: 7 }, message_id: 74 } });
check('revise only photos', opened().parts.join(), 'photos');

// 16.10: «🏷 Виставлено» (кнопка) → GitHub listings: ключ eBay + рядки «Виставлено» з нотатками (номер eBay)
say('🏷 Виставлено');
check('listings dispatched', [opened().mode, opened().rt, opened().rows.map((r) => [r.n, /eBay 1234567890/.test(r.note)]),
  /Дивлюся виставлене/.test(last()[0])], ['listings', 'v^1.1#RT', [[1, true], [3, false]], true]);

// 17.10: зміни на eBay → облік (фонова перевірка / «🏷 Виставлено»)
const NOTE = vm.runInContext('COL.note', ctx), STATUS = vm.runInContext('COL.status', ctx);
deals.cells['8:2'] = new Date('2026-10-10T10:00:00Z'); deals.cells['8:3'] = 'G.Skill Ripjaws 32GB DDR4'; deals.cells['8:11'] = 'Перевірено';
deals.cells['8:10'] = 80; deals.last = 8;
const sync = (d, nonce) => ctx.doPost({ postData: { contents: JSON.stringify({ sealed: ctx.seal_(Object.assign({ kind: 'sync' }, d), '123:ABC', nonce) }) },
  parameter: { bot: 'office' } });
sync({ lots: [{ n: 3, id: '306123456789', price: 55 }, { n: 4, id: '306999888777', price: 120 }], ask: [], ended: [] }, 's1');
check('sync: price change + new listing', [/ціна на eBay → 55 € 09.10/.test(deals.cells['7:' + NOTE]), deals.cells['8:' + STATUS],
  /\(eBay 306999888777\)/.test(deals.cells['8:' + NOTE]), /№3: ціна на eBay 59 → 55 €/.test(last()[0]), /№4 G.Skill.*бачу на eBay за 120 €/.test(last()[0])],
  [true, 'Виставлено', true, true, true]);
const before = sent.length;
sync({ lots: [{ n: 3, id: '306123456789', price: 55 }, { n: 4, id: '306999888777', price: 120 }], ask: [], ended: [] }, 's2');
check('sync: nothing new — silent', sent.length, before);
// 19.10: «звірити» — на вимогу; відповідь і коли все збігається
say('звірити');
check('manual sync dispatched', [opened().mode, opened().manual, /Звіряю eBay/.test(last()[0])], ['sync', 1, true]);
sync({ lots: [{ n: 3, id: '306123456789', price: 55 }], ask: [], ended: [], manual: 1, count: 4 }, 's2m');
check('manual sync: all matches', /✅ Звірив з eBay: активних лотів 4 — таблиця збігається/.test(last()[0]), true);
// 24.10: продане, яке eBay показує «Versendet» (етикетка eBay), → облік сам ставить «відправлено» + трек
deals.cells['8:11'] = 'Продано'; deals.cells['8:' + NOTE] = 'виставлено (eBay 336849525826)';
sync({ lots: [], ask: [], ended: [], shipped: [{ item_id: '336849525826', sku: '', track: '354502403646', carrier: 'DHL', date: '2026-10-23' }] }, 'sh1');
check('shipped from eBay', [ctx.events_()[4].sent, ctx.events_()[4].outTrack, /№4 .*Versendet.*354502403646.*записав відправку/.test(last()[0])],
  ['2026-10-23', '354502403646', true]);
const nSh = sent.length;
sync({ lots: [], ask: [], ended: [], shipped: [{ item_id: '336849525826', sku: '', track: '354502403646', carrier: 'DHL', date: '2026-10-23' }] }, 'sh2');
check('shipped once', sent.length, nSh);
deals.cells['8:11'] = 'Перевірено'; deals.cells['8:' + NOTE] = '';
// 19.10: щоденна перевірка цін — порада з кнопкою «⬇️» (підтвердження); та сама порада — один раз
const mk = [{ n: 3, id: '306123456789', price: 169, ship: 6.49, comp: [139, 140, 145], item: 132, profit: 30, floor: false, total: 36, place: 9 }];
sync({ lots: [{ n: 3, id: '306123456789', price: 55 }], ask: [], ended: [], market: mk }, 's2k');
check('market tip', [/📉 <b>Ціни конкурентів[\s\S]*№3.*= <b>175,49 €<\/b> \(9-й за ціною з 36 схожих\).*139, 140, 145 € → постав <b>132 €<\/b> \+ доставка/.test(last()[0]),
  last()[1].inline_keyboard[0][0].callback_data], [true, 'p|3|132']);
const before3 = sent.length;
sync({ lots: [{ n: 3, id: '306123456789', price: 55 }], ask: [], ended: [], market: mk }, 's2l');
check('market tip once', sent.length, before3);
// 26.10: перевірка оголошень — розбіжності раз на нову; виправлене забувається; «звірити» — і коли все збігається
const au = [{ n: 3, where: 'eBay', id: '306123456789', url: 'https://www.ebay.de/itm/306123456789', title: 'Kingston 16GB',
  issues: ['у Herstellernummer 2 номери (A, B) — має бути один'], key: 'k1' }];
sync({ lots: [], ask: [], ended: [], audit: au }, 'au1');
check('audit reported', [/🔎 <b>Перевірка оголошень<\/b>[\s\S]*№3 eBay «Kingston 16GB»[\s\S]*2 номери/.test(last()[0]),
  last()[1].inline_keyboard[0][0].url], [true, 'https://www.ebay.de/itm/306123456789']);
const nAu = sent.length;
sync({ lots: [], ask: [], ended: [], audit: au }, 'au2');
check('audit: same issues once', sent.length, nAu);
sync({ lots: [], ask: [], ended: [], audit: [] }, 'au3');
sync({ lots: [], ask: [], ended: [], audit: au }, 'au4');
check('audit: fixed then broken again → reported again', sent.length, nAu + 1);
sync({ lots: [], ask: [], ended: [], audit: [], manual: 1, count: 3 }, 'au5');
check('manual: all listings match', /Оголошення eBay і KA перевірив/.test(last()[0]), true);
// 31.10: фактичні витрати eBay по замовленню → «Моя пересилка покупцю» / «Комісія eBay»; один раз; прохання «ebay вхід» — раз
deals.cells['8:11'] = 'Продано'; deals.cells['8:' + NOTE] = 'виставлено (eBay 336849011147)';
sync({ lots: [], ask: [], ended: [], fees: [{ order_id: '12-15361-67725', item_id: '336849011147', sku: '', label: 5.79, fees: 9.52 }] }, 'fe1');
check('fees written', [deals.cells['8:' + vm.runInContext('COL.sship', ctx)], deals.cells['8:' + vm.runInContext('COL.comm', ctx)],
  /🧾 №4 .*етикетка 5,79 € · реклама \/ комісія 9,52 €, записав/.test(last()[0])], [5.79, 9.52, true]);
const nFe = sent.length;
sync({ lots: [], ask: [], ended: [], fees: [{ order_id: '12-15361-67725', item_id: '336849011147', sku: '', label: 5.79, fees: 9.52 }] }, 'fe2');
check('fees once', sent.length, nFe);
sync({ lots: [], ask: [], ended: [], fees_auth: 1 }, 'fe3');
check('asks ebay login once', /ebay вхід/.test(last()[0]), true);
const nFa = sent.length;
sync({ lots: [], ask: [], ended: [], fees_auth: 1 }, 'fe4');
check('ebay login asked once', sent.length, nFa);
deals.cells['8:11'] = 'Перевірено'; deals.cells['8:' + NOTE] = '';
sync({ lots: [], ask: [], ended: [], ka_links: [{ n: 3, id: '3551005398' }] }, 'au6');
check('KA ad linked to the ledger', [ctx.events_()[3].ka, /№3 — бачу на Kleinanzeigen \(оголошення 3551005398\), записав/.test(last()[0])],
  ['3551005398', true]);
sync({ lots: [], ask: [{ id: '306555444333', title: 'Corsair LPX 32GB', price: 99, cands: [1, 4] }], ended: [{ n: 1, id: '1234567890' }] }, 's3');
check('sync: ended + ask with buttons', [/№1: оголошення на eBay більше не активне/.test(sent[sent.length - 2][0]), /Це який номер/.test(last()[0]),
  last()[1].inline_keyboard.map((r) => r[0].callback_data)], [true, true, ['L|306555444333|1', 'L|306555444333|4', 'L|306555444333|0']]);
const before2 = sent.length;
sync({ lots: [], ask: [{ id: '306555444333', title: 'Corsair LPX 32GB', price: 99, cands: [1, 4] }], ended: [{ n: 1, id: '1234567890' }] }, 's4');
check('sync: asked once', sent.length, before2);
deals.cells['8:11'] = 'Перевірено'; deals.cells['8:' + NOTE] = '';
ctx.officeCallback({ id: 'q', data: 'L|306555444333|4', message: { chat: { id: 7 }, message_id: 80 } });
check('sync: answer links the row', [deals.cells['8:' + STATUS], /виставлено за 99 € 09.10 \(eBay 306555444333\)/.test(deals.cells['8:' + NOTE])],
  ['Виставлено', true]);
ctx.officeCallback({ id: 'q', data: 'L|306111000111|0', message: { chat: { id: 7 }, message_id: 81 } });
check('sync: not from ledger', /не з обліку/.test(last()[0]), true);
check('sale row by eBay listing id', [ctx.rowByListing_('306555444333'), ctx.rowByListing_('999999999999')], [8, 0]);

// 17.10: «🗂 На руках» → аркуш з фото (GitHub, режим onhand): групи, фото, номер eBay
store.PH_4 = JSON.stringify([{ id: 'f4', u: 'u4' }]);
say('на руках');
const oh = opened();
check('on-hand sheet dispatched', [oh.mode, oh.groups.map((g) => [g.kind, g.items.map((i) => i.n)]), oh.groups[0].items[0].item_id,
  oh.groups[0].items.filter((i) => i.n === 4)[0].photos], ['onhand', [['ebay', [1, 3, 4]]], '1234567890', ['f4']]);

// 18.10: «♻️ Перевиставити» — підтвердження, ціна як на eBay, завершене → «Перевірено» без «більше не активне»
store.LISTED_PRICE = JSON.stringify(Object.assign(JSON.parse(store.LISTED_PRICE || '{}'), { 1: 159 }));
deals.cells['5:11'] = 'Виставлено';
store.PH_1 = JSON.stringify([{ id: 'R1', t: 'photo', u: 'ur1' }]);
say('перевиставити 1');
check('relist confirm', [/Перевиставити №1\? .*за 159 €/.test(last()[0]), last()[1].inline_keyboard[0][0].callback_data], [true, 'c|так-перевист|1']);
ctx.officeCallback({ id: 'q', data: 'c|так-перевист|1', message: { chat: { id: 7 }, message_id: 90 } });
const rl = opened();
check('relist dispatched', [rl.mode, rl.item_id, rl.edits.price, rl.photos.length > 0], ['relist', ctx.itemIdOf_(1), 159, true]);
ctx.doPost({ postData: { contents: JSON.stringify({ sealed: ctx.seal_({ kind: 'ended', row: 1, item_id: rl.item_id }, '123:ABC', 'r1') }) },
  parameter: { bot: 'office' } });
check('ended → Перевірено, no "not active" later', [deals.cells['5:11'], /завершено 09.10 \(перевиставлення\)/.test(deals.cells['5:' + vm.runInContext('COL.note', ctx)]),
  JSON.parse(store.SYNC_ASKED)['E1:' + rl.item_id]], ['Перевірено', true, 1]);

// 18.10: «💌 спостерігачам X €» — підтвердження з ціною → дія watchers → нотатка
deals.cells['5:11'] = 'Виставлено';
ctx.officeCallback({ id: 'q', data: 'w|1|159', message: { chat: { id: 7 }, message_id: 95 } });
check('watchers confirm', [/пропозицію <b>159 €<\/b>/.test(last()[0]), last()[1].inline_keyboard[0][0].callback_data], [true, 'c|так-спостер|1']);
ctx.officeCallback({ id: 'q', data: 'c|так-спостер|1', message: { chat: { id: 7 }, message_id: 96 } });
check('watchers dispatched', [opened().mode, opened().action, opened().price, opened().item_id], ['ebay', 'watchers', 159, ctx.itemIdOf_(1)]);
ctx.doPost({ postData: { contents: JSON.stringify({ sealed: ctx.seal_({ kind: 'offer_sent', row: 1, price: 159, n: 2 }, '123:ABC', 'w1') }) },
  parameter: { bot: 'office' } });
check('offer noted', /пропозиція спостерігачам 159 € 09.10/.test(deals.cells['5:' + vm.runInContext('COL.note', ctx)]), true);
ctx.officeCallback({ id: 'q', data: 'c|так-спостер|1', message: { chat: { id: 7 }, message_id: 97 } });
check('second tap — stale', /застаріла/.test(last()[0]), true);

// 17.10: «отримав N» → фото з оголошення продавця (GitHub, режим got)
deals.cells['8:11'] = 'Оплачено'; deals.cells['8:' + vm.runInContext('COL.id', ctx)] = '306123123123';
say('отримав 4');
check('got → seller photo dispatched', [opened().mode, opened().row, opened().id], ['got', 4, '306123123123']);

// 18.10: 🚀 для такого самого товару (той самий номер моделі), що вже на eBay → «➕ Додати до оголошення» замість дубля
const NOTE2 = vm.runInContext('COL.note', ctx);
[['Corsair Vengeance LPX 32GB (2x16GB) DDR4-2400 CMK32GX4M2A2400C16', 'Виставлено', 'виставлено за 175 € 09.10 (eBay 336849011147)'],
 ['Corsair Vengeance 32GB 2x16GB DDR4 2400 CMK32GX4M2A2400C16 OVP', 'Перевірено', '']].forEach((v, i) => {
  const r = 10 + i;
  deals.cells[r + ':2'] = new Date('2026-10-10T10:00:00Z'); deals.cells[r + ':3'] = v[0]; deals.cells[r + ':4'] = 'RAM';
  deals.cells[r + ':11'] = v[1]; deals.cells[r + ':10'] = 120; deals.cells[r + ':' + NOTE2] = v[2]; deals.last = r;
});
store.LISTED_PRICE = JSON.stringify(Object.assign(JSON.parse(store.LISTED_PRICE || '{}'), { 6: 175 }));
store.PH_7 = JSON.stringify([{ id: 'T7', t: 'photo', u: 'ut7' }]);
const ghN = gh.length;
ctx.officeCallback({ id: 'q', data: 'c|так-авто|7', message: { chat: { id: 7 }, message_id: 100 } });
check('twin found → offer to add, nothing published', [/такий самий товар, як №6 \(CMK32GX4M2A2400C16\)/.test(last()[0]),
  last()[1].inline_keyboard[0][0].callback_data, gh.length], [true, 'c|додати-до|7', ghN]);
check('twin prompt also offers a separate listing', last()[1].inline_keyboard[1][0].callback_data, 'c|окремо|7');
const ghS = gh.length;
ctx.officeCallback({ id: 'q', data: 'c|окремо|7', message: { chat: { id: 7 }, message_id: 103 } });
check('separate → normal publish', [gh.length, opened().mode, opened().row], [ghS + 1, 'publish', 7]);
store.EBAY_PUB = '{}';
ctx.officeCallback({ id: 'q', data: 'c|додати-до|7', message: { chat: { id: 7 }, message_id: 101 } });
check('merge dispatched', [opened().mode, opened().item_id, opened().twin, opened().price], ['merge', '336849011147', 6, 175]);
check('specs are not part numbers', ctx.titleSpec_('Kingston Fury 32GB DDR4-3200 PC4-25600 LPDDR5-6400').parts, []);
ctx.doPost({ postData: { contents: JSON.stringify({ sealed: ctx.seal_({ kind: 'merged', row: 7, item_id: '336849011147', price: 175, twin: 6, qty: 2 },
  '123:ABC', 'm1') }) }, parameter: { bot: 'office' } });
check('merged → №7 listed on the same id', [deals.cells['11:11'], /\(eBay 336849011147\)/.test(deals.cells['11:' + NOTE2])], ['Виставлено', true]);
say('перевиставити 6');
ctx.officeCallback({ id: 'q', data: 'c|так-перевист|6', message: { chat: { id: 7 }, message_id: 102 } });
check('shared listing — no auto relist', /спільне з №7/.test(last()[0]), true);

// 22.10: лот і на KA — продано на KA → з eBay знімається; продано на eBay → нагадати зняти з KA
const lp7 = JSON.parse(store.LISTED_PRICE || '{}')[7];
say('виставив 7 ka 179 https://www.kleinanzeigen.de/s-anzeige/corsair-32gb/3545123456-225-1234');
check('listed on KA', [/на KA за 179 € .*\(KA 3545123456\)/.test(deals.cells['11:' + NOTE2]), ctx.events_()[7].ka,
  JSON.parse(store.LISTED_PRICE || '{}')[7] === lp7, /на Kleinanzeigen за 179 €[\s\S]*з eBay зніму сам/.test(last()[0])],
  [true, '3545123456', true, true]);
ctx.soldElsewhere_(7, 'Kleinanzeigen', true, true);
check('KA sale mail → delist on eBay', [opened().mode, opened().action, opened().item_id, /знімаю з eBay/.test(last()[0])],
  ['ebay', 'delist', '336849011147', true]);
ctx.soldElsewhere_(7, 'Kleinanzeigen', false, true);
check('manual sale → button, nothing dispatched', last()[1].inline_keyboard[0][0].callback_data, 'c|зняти|7');
ctx.soldElsewhere_(7, 'eBay', true, true);
check('eBay sale → remove from KA', [/зніми оголошення на Kleinanzeigen/.test(last()[0]), last()[1].inline_keyboard[0][0].url],
  [true, 'https://www.kleinanzeigen.de/m-meine-anzeigen.html']);
const ghD = gh.length;
ctx.soldElsewhere_(7, 'Kleinanzeigen', true, false);
check('was not listed → nothing', gh.length, ghD);
say('зняти 7');
check('«зняти N»', [opened().action, opened().row], ['delist', 7]);

// 31.10: продаж №5 записався на №4 — «перенести продаж 4 5»: дата, ціна, трек → №5; №4 знову «Виставлено»
const NOTE3 = vm.runInContext("COL.note", ctx), ST3 = vm.runInContext("COL.status", ctx), SP3 = vm.runInContext("COL.sprice", ctx);
deals.cells['9:2'] = new Date('2026-10-12T10:00:00Z'); deals.cells['9:3'] = 'Corsair Vengeance LPX 32GB DDR4 2133'; deals.cells['9:11'] = 'Продано';
deals.cells['9:' + SP3] = 160; deals.cells['9:' + NOTE3] = 'виставлено (eBay 336844624233)';
deals.cells['10:2'] = new Date('2026-10-12T10:00:00Z'); deals.cells['10:3'] = 'Corsair Vengeance 32GB DDR4 2400'; deals.cells['10:11'] = 'Виставлено';
deals.last = 10;
ctx.setEvents_(5, { sent: '2026-10-31', outTrack: '02304112003507', outCarrier: 'DPD' });
say('перенести продаж 5 6');
check('sale moved', [deals.cells['9:' + ST3], deals.cells['9:' + SP3], deals.cells['10:' + ST3], deals.cells['10:' + SP3],
  ctx.events_()[6].outTrack, ctx.events_()[5].outTrack, /Продаж перенесено: №5 → №6.*трек 02304112003507 тепер у №6/.test(last()[0])],
  ['Виставлено', '', 'Продано', 160, '02304112003507', undefined, true]);
// продаж з листа за назвою — двоє схожих → не вгадуємо, питаємо кнопками
deals.cells['10:11'] = 'Виставлено'; deals.cells['9:11'] = 'Виставлено';
const sr = ctx.saleRowByTitle_('Corsair Vengeance LPX 32GB (2x16GB) DDR4');
check('ambiguous sale → candidates', [sr.row, sr.cands.length >= 2, sr.cands.indexOf(5) >= 0], [0, true, true]);

// 31.10: у повідомленнях — назва нашого оголошення eBay (зі звірки), у таблиці — в нотатках один раз
sync({ lots: [{ n: 3, id: '306123456789', price: 55, title: 'Kingston FURY Beast 16GB DDR4-3200 getestet' }], ask: [], ended: [] }, 'lt1');
check('listing title remembered + noted once', [ctx.listTitle_(3, 'x'), (String(deals.cells['7:' + NOTE3]).match(/на eBay: «Kingston FURY Beast 16GB DDR4-3200 getestet»/g) || []).length],
  ['Kingston FURY Beast 16GB DDR4-3200 getestet', 1]);
sync({ lots: [{ n: 3, id: '306123456789', price: 55, title: 'Kingston FURY Beast 16GB DDR4-3200 getestet' }], ask: [], ended: [] }, 'lt2');
check('noted only once', (String(deals.cells['7:' + NOTE3]).match(/на eBay:/g) || []).length, 1);

// ключ відкликано → видалити
ctx.sealedPost(ctx.seal_({ kind: 'ebay_bad' }, '123:ABC', 'n4'));
check('rt removed', store.EBAY_RT, undefined);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
