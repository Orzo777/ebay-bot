// Бот «Помічник» (node tools/test_assist.js): питання → «🤔 Думаю…» + assist.yml (зашифровано), фото й альбоми,
// «нове», історія з GitHub (sealedPost), автопідключення вебхука, маршрут doPost ?bot=assist.
const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');
const signed = (buf) => Array.from(buf).map((b) => (b > 127 ? b - 256 : b));
const store = { TELEGRAM_CHAT_ID: '7', OFFICE_BOT_TOKEN: '123:ABC', TELEGRAM_BOT_TOKEN: 'MAIN', GITHUB_TOKEN: 'g', ASSIST_BOT_TOKEN: 'ASSIST:TOKEN1' };
const tg = [], gh = [];
let triggers = [];
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
    formatDate: () => '2026-10-10',
  },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => (k in store ? store[k] : null),
    setProperty: (k, v) => { store[k] = v; }, deleteProperty: (k) => { delete store[k]; } }) },
  ScriptApp: {
    getProjectTriggers: () => triggers,
    newTrigger: (fn) => ({ timeBased: () => ({ after: (ms) => ({ create: () => { triggers.push({ getHandlerFunction: () => fn, ms }); } }) }) }),
    deleteTrigger: (t) => { triggers = triggers.filter((x) => x !== t); },
  },
  LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
  HtmlService: { createHtmlOutput: (x) => x },
  UrlFetchApp: { fetch: (u, o) => {
    if (/api\.github\.com/.test(u)) { gh.push(JSON.parse(o.payload).inputs); return { getResponseCode: () => 204, getContentText: () => '' }; }
    const m = u.match(/bot([^/]+)\/(\w+)/);
    tg.push([m[1], m[2], (o || {}).payload]);
    const body = m[2] === 'getWebhookInfo' ? { ok: true, result: { url: 'https://script.google.com/macros/s/XYZ/exec?bot=office' } }
      : m[2] === 'sendMessage' ? { ok: true, result: { message_id: 900 + tg.length } } : { ok: true, result: {} };
    return { getResponseCode: () => 200, getContentText: () => JSON.stringify(body) };
  } },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
vm.runInContext('ledgerRows_ = function () { return [{ n: 1, title: "OWC 32GB DDR4 SO-DIMM", status: "Отримано", spent: 50.49 }]; };', ctx);
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };
const say = (m) => ctx.assistMessage(Object.assign({ chat: { id: 7 }, message_id: 1 }, m));
const assistCalls = (method) => tg.filter((c) => c[0] === 'ASSIST:TOKEN1' && c[1] === method);
const opened = () => ctx.unseal_(gh[gh.length - 1], store.OFFICE_BOT_TOKEN);
const photo = (id, extra) => Object.assign({ photo: [{ file_id: id + 's', width: 90, height: 90 }, { file_id: id, file_unique_id: 'u' + id, width: 1280, height: 960, file_size: 1000 }] }, extra || {});

// чужий чат — тиша
ctx.assistMessage({ chat: { id: 99 }, text: 'привіт' });
check('foreign chat ignored', [tg.length, gh.length], [0, 0]);
// /start — довідка
say({ text: '/start' });
check('help', /помічник з перепродажу/.test(assistCalls('sendMessage').pop()[2].text), true);

// питання текстом → «Думаю…» + assist.yml з питанням, обліком, id повідомлення
store.LEDGER_ID = 'L';
say({ text: 'ПК за 160: i5-9400F, 2x8 DDR4, SSD 250 — вигідно?' });
let p = opened();
check('dispatch with question, rows, wait id', [p.q, p.rows[0].title, typeof p.wait, p.photos], ['ПК за 160: i5-9400F, 2x8 DDR4, SSD 250 — вигідно?', 'OWC 32GB DDR4 SO-DIMM', 'number', []]);
check('placeholder shown', /Думаю/.test(assistCalls('sendMessage').pop()[2].text), true);
check('no plain question in GitHub request', JSON.stringify(gh[gh.length - 1]).includes('9400'), false);

// фото без питання → чекає; потім текст → питання з фото
const g0 = gh.length;
say(photo('P1'));
check('photo alone waits', [gh.length, /Фото є \(1\)/.test(assistCalls('sendMessage').pop()[2].text)], [g0, true]);
say({ text: 'а це що за плата, варто брати?' });
check('question gets the photo', [opened().photos, opened().q], [['P1'], 'а це що за плата, варто брати?']);

// альбом: підпис на другому фото — чекаємо тригер, потім одне питання з усіма фото
const g1 = gh.length;
say(photo('A1', { media_group_id: 'M' }));
say(photo('A2', { media_group_id: 'M', caption: 'скільки коштує цей комплект?' }));
say(photo('A3', { media_group_id: 'M' }));
check('album waits for trigger (one)', [gh.length, triggers.length], [g1, 1]);
ctx.assistFlush();
check('album → one question with all photos', [gh.length, opened().photos, opened().q, triggers.length], [g1 + 1, ['A1', 'A2', 'A3'], 'скільки коштує цей комплект?', 0]);

// історія: відповіді з GitHub → ASSIST_HIST (6 останніх) → у наступному питанні; «нове» — очистити
for (let i = 0; i < 8; i++) ctx.sealedPost(ctx.seal_({ kind: 'assist', q: 'q' + i, a: 'a' + i }, '123:ABC', 'n' + i));
check('history kept 6', JSON.parse(store.ASSIST_HIST).map((h) => h.q), ['q2', 'q3', 'q4', 'q5', 'q6', 'q7']);
say({ text: 'а якщо за 130?' });
check('history sent', opened().hist.length, 6);
say({ text: 'нове' });
check('reset', store.ASSIST_HIST, undefined);

// автопідключення: вебхук ?bot=assist один раз
ctx.assistConnect_(); ctx.assistConnect_();
const sets = assistCalls('setWebhook');
check('webhook set once to ?bot=assist', [sets.length, sets[0][2].url], [1, 'https://script.google.com/macros/s/XYZ/exec?bot=assist']);

// doPost ?bot=assist → assistMessage, повтор update_id — ні
let hits = 0;
vm.runInContext('assistMessage = function () { __hit(); };', Object.assign(ctx, { __hit: () => { hits++; } }));
const post = (upd) => ctx.doPost({ postData: { contents: JSON.stringify(upd) }, parameter: { bot: 'assist' } });
post({ update_id: 5, message: { chat: { id: 7 }, text: 'x' } });
post({ update_id: 5, message: { chat: { id: 7 }, text: 'x' } });
check('doPost routes assist once', hits, 1);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
