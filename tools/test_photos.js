// Фото товару в боті «Облік і продаж» (node tools/test_photos.js): збереження до рядка, Gemini — наклейка / MemTest86
const fs = require('fs');
const vm = require('vm');
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O', GEMINI_API_KEY: 'G' };
const tg = [], sent = [];
let gemini = [];   // черга відповідей Gemini (об'єкти)
let nextMid = 500;
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
[['OWC 32GB (2x16GB) DDR4-2400 SO-DIMM für iMac OWC2400DDR4S16G', 'Отримано'],
 ['Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200 CMK32GX4M2B3200C16', 'Отримано'],
 ['2x16GB SK Hynix DDR4 2666 MHz PC4-2666V (HMA82GU7CJR8N) ECC UDIMM', 'Отримано'],
 ['PS5 Slim Disc', 'Продано']].forEach((v, i) => {
  const r = 5 + i;
  deals.cells[r + ':2'] = new Date('2026-10-01T10:00:00Z'); deals.cells[r + ':3'] = v[0]; deals.cells[r + ':4'] = 'RAM';
  deals.cells[r + ':5'] = 'eBay'; deals.cells[r + ':11'] = v[1]; deals.last = r;
});
const ctx = {
  console: { log: () => {} },
  Utilities: { base64Encode: () => 'AAA', getUuid: () => 'ab-cd',
    formatDate: (d, tz, f) => ({ 'yyyy-MM-dd': d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) }[f]) },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => (k in store ? store[k] : null),
    setProperty: (k, v) => { store[k] = v; }, deleteProperty: (k) => { delete store[k]; } }) },
  UrlFetchApp: { fetch: (u, o) => {
    if (/generativelanguage/.test(u)) {
      const g = gemini.shift();
      return { getResponseCode: () => (g ? 200 : 503),
        getContentText: () => JSON.stringify({ candidates: [{ content: { parts: [{ text: JSON.stringify(g) }] } }] }) };
    }
    if (/\/getFile\?/.test(u)) return { getContentText: () => JSON.stringify({ result: { file_path: 'photos/x.jpg' } }) };
    if (/api\.telegram\.org\/file\//.test(u)) return { getBlob: () => ({ getContentType: () => 'image/jpeg', getBytes: () => [1] }) };
    const method = u.split('/').pop();
    tg.push([method, o.payload]);
    const ok = method !== 'editMessageText' || store.EDIT_FAIL !== '1';
    return { getResponseCode: () => (ok ? 200 : 400),
      getContentText: () => JSON.stringify(method === 'sendMessage' ? { ok: true, result: { message_id: ++nextMid } } : { ok }) };
  } },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs', 'kafilter.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
vm.runInContext(`ledger_ = function () { return { getSheetByName: () => __deals }; };
  notify_ = function (t) { __sent.push(t); };`, Object.assign(ctx, { __deals: deals, __sent: sent }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };
const say = (m) => vm.runInContext('officeMessage(__m)', Object.assign(ctx, { __m: Object.assign({ chat: { id: 7 } }, m) }));
let sizeSeq = 1000;
const photo = (id, mid, extra, u, size) => Object.assign({ message_id: mid, photo: [{ file_id: id + '_s' },
  { file_id: id, file_unique_id: u || 'U' + id, width: 1280, height: 960, file_size: size || ++sizeSeq }] }, extra || {});
const asks = () => tg.filter((c) => c[0] === 'sendMessage' && /До якого товару/.test(c[1].text)).length;
const calls = (m) => tg.filter((c) => c[0] === m);
const lastSummary = () => { const c = tg.filter((x) => x[0] === 'sendMessage' || x[0] === 'editMessageText').pop(); return c ? c[1].text : ''; };

// наклейка проти назви покупки
const chk = (t, l) => { const c = ctx.labelCheck_(t, l); return [c.ok.length > 0, c.bad]; };
check('Corsair label matches', chk('Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200 CMK32GX4M2B3200C16',
  { part_number: 'CMK32GX4M2B3200C16', gb_per_module: 16, ddr: 'DDR4', form: 'desktop' }), [true, []]);
check('OWC laptop label matches', chk('OWC 32GB (2x16GB) DDR4-2400 SO-DIMM für iMac', { gb_per_module: 16, ddr: 'DDR4', form: 'laptop' }), [true, []]);
check('8GB sticker on 2x16 → mismatch', chk('Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200', { gb_per_module: 8, ddr: 'DDR4' })[1].length, 1);
check('desktop sticker for SO-DIMM purchase → mismatch', /ноутбучну/.test(chk('OWC 32GB (2x16GB) DDR4 SO-DIMM', { form: 'desktop' })[1][0]), true);
check('registered → warning', /серверна/.test(chk('SK Hynix 2x16GB DDR4', { registered: true, ddr: 'DDR4' })[1][0]), true);
check('DDR5 sticker on DDR4 purchase', /DDR5, а купував DDR4/.test(chk('Kingston 2x16GB DDR4', { ddr: 'DDR5' })[1][0]), true);
const ts = ctx.titleSpec_('OWC 32GB (16GB x 2) PC4-19200 DDR4-2400 Kit');
check('«16GB x 2» parsed', [ts.per, ts.modules, ts.ddr], [16, 2, '4']);

// 1. екран MemTest86 з підписом «2», 0 помилок, прохід завершено → «Перевірено»
gemini = [{ kind: 'memtest', memtest: { errors: 0, pass_done: true, result: null, ram: 'DDR4 2134MT/s x2 Corsair CMK32GX4M2B3200C16', gb: 29.9 } }];
say(photo('F1', 11, { caption: '2', media_group_id: 'A' }));
check('memtest → Перевірено', deals.cells['6:11'], 'Перевірено');
check('photo stored to row 2', JSON.parse(store.PH_2).map((x) => [x.id, x.u]), [['F1', 'UF1']]);
check('user photo message removed from chat', calls('deleteMessage').some((c) => c[1].message_id === 11), true);
check('summary', /№2.*фото: 1[\s\S]*✅ MemTest86: 0 помилок.*«Перевірено»/.test(lastSummary()), true);
const mid = JSON.parse(store.PHS_2).mid;

// 2. друге фото того ж альбому (без підпису) — наклейка; підсумок РЕДАГУЄТЬСЯ, не нове повідомлення
gemini = [{ kind: 'ram_label', label: { brand: 'Corsair', part_number: 'CMK32GX4M2B3200C16', gb_per_module: 16, ddr: 'DDR4', form: 'desktop' } }];
const sends0 = calls('sendMessage').length;
say(photo('F2', 12, { media_group_id: 'A' }));
check('album photo → same row', JSON.parse(store.PH_2).length, 2);
check('summary edited in place', [calls('sendMessage').length, calls('editMessageText').pop()[1].message_id], [sends0, mid]);
check('summary has both lines', /фото: 2[\s\S]*✅ Наклейка збігається: модель CMK32GX4M2B3200C16[\s\S]*✅ MemTest86/.test(lastSummary()), true);

// 3. помилки → «Проблема»
gemini = [{ kind: 'memtest', memtest: { errors: 3, pass_done: false, result: null } }];
say(photo('F3', 13, { caption: '3' }));
check('errors → Проблема', [deals.cells['7:11'], /⛔ MemTest86: <b>3 помилок/.test(lastSummary())], ['Проблема', true]);

// 4. прохід не завершено — статус не чіпаємо
deals.cells['5:11'] = 'Отримано';
gemini = [{ kind: 'memtest', memtest: { errors: 0, pass_done: false, result: null } }];
say(photo('F4', 14, { caption: '1' }));
check('unfinished pass → no status change', [deals.cells['5:11'], /⏳/.test(lastSummary())], ['Отримано', true]);

// 5. фото без номера, на руках кілька товарів → питає; відповідь «1» прикріплює
gemini = [{ kind: 'other' }];
say(photo('F5', 15));
check('single photo without number → asks once', [asks(), JSON.parse(store.PH_PENDING).length], [1, 1]);
const askMid = Number(store.PH_ASK);
say({ message_id: 16, text: '1' });
check('pending attached to №1, number message and question removed', [JSON.parse(store.PH_1).map((x) => x.id), store.PH_PENDING,
      calls('deleteMessage').some((c) => c[1].message_id === 16), calls('deleteMessage').some((c) => c[1].message_id === askMid), store.PH_ASK],
      [['F4', 'F5'], undefined, true, true, undefined]);

// 6. Gemini недоступний — фото все одно збережене
gemini = [];
say(photo('F6', 17, { caption: '1' }));
check('Gemini down → still stored', JSON.parse(store.PH_1).length, 3);

// 7. «фото 2» → альбом; «фото 9» → підказка
say({ message_id: 18, text: 'фото 2' });
const mg = calls('sendMediaGroup').pop();
check('album of 2', [JSON.parse(mg[1].media).map((m) => m.media), JSON.parse(mg[1].media)[0].caption], [['F1', 'F2'], '📸 №2']);
say({ message_id: 19, text: 'фото 9' });
check('no photos hint', /фото ще немає/.test(sent[sent.length - 1]), true);

// 8. «продати 2» передає фото у sell.yml (зашифровано разом із назвою)
let sealed = null;
vm.runInContext('seal_ = function (d) { __sealed.v = d; return { blob: "b", mac: "m", nonce: "n" }; };', Object.assign(ctx, { __sealed: { set v(x) { sealed = x; } } }));
store.GITHUB_TOKEN = 'g';
say({ message_id: 20, text: 'продати 2' });
check('sell gets photos', sealed && sealed.photos.map((p) => p.id), ['F1', 'F2']);

// 10. 08.10: альбом прийшов НЕ по порядку — фото без підпису раніше за підписане: без питання, обидва до №3
const asks0 = asks();
gemini = [{ kind: 'other' }, { kind: 'other' }];
say(photo('B2', 30, { media_group_id: 'B' }));
check('album sibling first → waits silently', [asks(), JSON.parse(store.PH_PENDING).length], [asks0, 1]);
say(photo('B1', 31, { caption: '3', media_group_id: 'B' }));
check('captioned photo pulls the waiting sibling', [JSON.parse(store.PH_3).map((x) => x.id).sort(), store.PH_PENDING, asks()],
      [['B1', 'B2', 'F3'], undefined, asks0]);

// 11. дублікати: те саме фото ще раз (той самий file_unique_id) і вдруге завантажене (той самий розмір) — не зберігаємо, без Gemini
gemini = [{ kind: 'other' }];
say(photo('B1again', 32, { caption: '3' }, 'UB1'));
say(photo('B2reup', 33, { caption: '3' }, 'Unew', JSON.parse(store.PH_3).find((x) => x.id === 'B2').k.split(':')[1] * 1));
check('duplicates not stored, Gemini not spent, messages removed', [JSON.parse(store.PH_3).length, gemini.length,
      calls('deleteMessage').some((c) => c[1].message_id === 32) && calls('deleteMessage').some((c) => c[1].message_id === 33)], [3, 1, true]);
gemini = [];

// 12. уже збережені дублікати (до виправлення) чистяться
store.PH_4 = JSON.stringify([{ id: 'X1', t: 'photo', u: 'UX' }, { id: 'X2', t: 'photo', u: 'UX' }, { id: 'X1', t: 'photo' }, { id: 'X3', t: 'photo', u: 'UY' }]);
check('old duplicates cleaned', ctx.cleanPhotos_(4).map((x) => x.id), ['X1', 'X3']);

// 13. альбом зовсім без підпису — питаємо з processLedger, коли сусід із підписом так і не прийшов (≥ 2 хв)
const asks1 = asks();
say(photo('C1', 40, { media_group_id: 'C' }));
say(photo('C2', 41, { media_group_id: 'C' }));
ctx.photoPendingCheck_();
check('fresh album → not yet', asks(), asks1);
const pend = JSON.parse(store.PH_PENDING); pend.forEach((x) => { x.ts -= 3 * 60000; }); store.PH_PENDING = JSON.stringify(pend);
ctx.photoPendingCheck_(); ctx.photoPendingCheck_();
check('after 2 min → asks once', asks(), asks1 + 1);
delete store.PH_PENDING; delete store.PH_ASK;

// 9. не своє — не перехоплюємо: текст «3» без фото, що чекають, іде далі як звичайно
check('bare number without pending → not a photo answer', vm.runInContext('photoNumber_(__m)', Object.assign(ctx, { __m: { text: '3', chat: { id: 7 } } })), false);
// 09.10: звіт MemTest86 файлом (HTML з флешки)
const rep = '<html><body><h2>MemTest86 Report</h2><table><tr><td>Test Result:</td><td>PASS</td></tr>' +
  '<tr><td>Errors:</td><td>0</td></tr><tr><td>Passes completed:</td><td>1</td></tr>' +
  '<tr><td>Part Number:</td><td>CMK32GX4M2B3200C16</td></tr></table></body></html>';
const p1 = ctx.parseMemtest_(rep);
check('parse report', [p1.memtest.result, p1.memtest.errors, p1.memtest.pass_done, p1.memtest.ram], ['PASS', 0, true, 'CMK32GX4M2B3200C16']);
check('parse FAIL', ctx.parseMemtest_('Test Result: FAIL ... Errors: 12').memtest.errors, 12);
check('not a report', ctx.parseMemtest_('hello world'), null);
deals.cells['8:11'] = 'Отримано';
vm.runInContext('UrlFetchApp.fetch = (function (orig) { return function (u, o) { if (/\\/file\\//.test(u)) return { getContentText: () => __rep, getBlob: () => ({}) }; return orig(u, o); }; })(UrlFetchApp.fetch);',
  Object.assign(ctx, { __rep: rep }));
say({ message_id: 60, caption: '4', document: { file_id: 'D1', file_name: 'MemTest86-Report-20261009.html', mime_type: 'text/html' } });
check('report → status Перевірено + summary', [deals.cells['8:11'], /№4[\s\S]*✅ MemTest86: 0 помилок, CMK32GX4M2B3200C16 → статус «Перевірено»/.test(lastSummary())], ['Перевірено', true]);
check('report message removed', calls('deleteMessage').some((c) => c[1].message_id === 60), true);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
