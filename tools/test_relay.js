// 03.11 (node tools/test_relay.js): листи з запасної скриньки (research/backup_mail.py) → Apps Script → ті самі обробники,
// що й для Gmail (облік, фільтр чатів KA, сторож); без пересланих листів — рівно як було; MAIL_SOURCE = relay — Gmail не читаємо
const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');
const signed = (buf) => Array.from(buf).map((b) => (b > 127 ? b - 256 : b));
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O', LEDGER_SCANNED: '1', EXPENSES_V1: '1', FIX1103: '1', FIX1103B: '1',
  KA_FILTER_TS: String(Date.now() - 3600e3), GITHUB_TOKEN: 't' };
const sent = [], cache = {};
let gmailCalls = 0, gmailThreads = [], fetched = [];

function sheet() {
  const cells = {};
  const s = { cells, last: 4, getLastRow: () => s.last, appendRow: (r) => { s.last++; r.forEach((v, i) => { cells[s.last + ':' + (i + 1)] = v; }); } };
  s.getRange = (r, c, n, w) => {
    n = n || 1; w = w || 1;
    const rng = new Proxy({}, { get: (_, k) => {
      if (k === 'setValue' || k === 'setFormula') return (v) => { cells[r + ':' + c] = v; s.last = Math.max(s.last, r); return rng; };
      if (k === 'getFormula') return () => '';
      if (k === 'getValue') return () => (cells[r + ':' + c] !== undefined ? cells[r + ':' + c] : '');
      if (k === 'getValues') return () => Array.from({ length: n }, (_, i) => Array.from({ length: w }, (_, j) => {
        const v = cells[(r + i) + ':' + (c + j)]; return v === undefined ? '' : v; }));
      return () => rng;
    } });
    return rng;
  };
  return s;
}
const deals = sheet(), log = sheet();
log.last = 1;
const ctx = {
  console: { log: () => {} },
  Utilities: { formatDate: (d) => d.toISOString().slice(0, 10), getUuid: () => 'u', Charset: { UTF_8: 'utf8' },
    computeHmacSha256Signature: (msg, key) => signed(crypto.createHmac('sha256', key).update(msg, 'utf8').digest()),
    newBlob: (x) => (typeof x === 'string' ? { getBytes: () => signed(Buffer.from(x, 'utf8')) }
      : { getDataAsString: () => Buffer.from(x.map((b) => b & 255)).toString('utf8') }),
    base64Encode: (bytes) => Buffer.from(bytes.map((b) => b & 255)).toString('base64'),
    base64Decode: (s) => signed(Buffer.from(s, 'base64')) },
  LockService: { getScriptLock: () => ({ waitLock() {}, tryLock() { return true; }, releaseLock() {} }) },
  SpreadsheetApp: { flush() {} },
  UrlFetchApp: { fetch: (u, o) => { fetched.push([u, o]); return { getResponseCode: () => 204, getContentText: () => '{}', getHeaders: () => ({}) }; } },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => (k in store ? store[k] : null),
    setProperty: (k, v) => { store[k] = String(v); }, deleteProperty: (k) => { delete store[k]; } }) },
  CacheService: { getScriptCache: () => ({ put: (k, v) => { if (v.length > 100000) throw new Error('too big'); cache[k] = v; },
    getAll: (keys) => Object.fromEntries(keys.filter((k) => k in cache).map((k) => [k, cache[k]])) }) },
  GmailApp: { search: () => { gmailCalls++; return gmailThreads; },
    getUserLabelByName: () => { throw new Error('Gmail labels must not be touched for relayed mail'); } },
  ContentService: { MimeType: { JSON: 'json' }, createTextOutput: (t) => ({ setMimeType: () => ({ json: JSON.parse(t) }) }) },
  HtmlService: { createHtmlOutput: (t) => ({ html: t }) },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs', 'kafilter.gs', 'watchdog.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
vm.runInContext('ledger_ = function () { return { getSheetByName: function (n) { return n === "Лог листів" ? __log : __d; } }; };' +
  'notify_ = function (t, k) { __sent.push([t, k]); }; packCheck_ = function () {}; tgOffice_ = function () { return {}; };' +
  'listingsWatch_ = function () {}; parcelsWatch_ = function () {}; photoRetry_ = function () {};',
  Object.assign(ctx, { __d: deals, __log: log, __sent: sent }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };
const post = (data, nonce) => ctx.doPost({ postData: { contents: JSON.stringify({ sealed: ctx.seal_(data, 'O', nonce) }) }, parameter: { bot: 'office' } });
const ago = (min) => new Date(Date.now() - min * 60000).toISOString();

// 0) без пересланих листів — той самий масив, що від Gmail (нічого не змінилось)
gmailThreads = [{ id: 'g1' }];
check('no relay → Gmail result as is', ctx.mailSearch_('q', 0, 5, 'ledger', 0) === gmailThreads, true);
gmailThreads = [];

// 1) зашифрований запит з листами → кеш; той самий лист удруге — не дублюється; відповідь — JSON
const buy = { id: '<b1@ebay>', from: 'eBay <ebay@ebay.de>', to: 'bot@example.org', subject: 'Ihre Bestellung ist bestätigt: Kingston Fury 2x16GB DDR4',
  date: ago(30), text: 'Gesamtbetrag 95,00 €\nVersand 0,00 €\nhttps://www.ebay.de/itm/306111222333', html: '' };
const scam = { id: '<k1@ka>', from: 'Kleinanzeigen <noreply@kleinanzeigen.de>', to: 'bot@example.org',
  subject: 'Nutzer-Anfrage zu deiner Anzeige „Suche Arbeitsspeicher“', date: ago(20), text: 'Hallo, zahle per PayPal Freunde und Familie, schreib mir auf WhatsApp', html: '' };
const honest = { id: '<k2@ka>', from: 'Kleinanzeigen <noreply@kleinanzeigen.de>', to: 'bot@example.org',
  subject: 'Neue Nachricht zu „Corsair LPX 32GB“', date: ago(10), text: 'Hallo, ist der RAM noch da? Ich würde ihn abholen.', html: '' };
const alert = { id: '<a1@ka>', from: 'Kleinanzeigen <noreply@kleinanzeigen.de>', to: 'bot@example.org',
  subject: 'Neue Treffer zu deiner Suche „ddr4 32gb“', date: ago(5), text: '', html: '' };
let out = post({ kind: 'mail', mails: [buy, scam, honest, alert] }, 'r1');
check('stored 4', out.json, { relay: 4 });
out = post({ kind: 'mail', mails: [buy] }, 'r2');
check('same mail again — not stored', out.json, { relay: 0 });
check('kinds', [ctx.mailSearch_('q', 0, 5, 'ledger', 0).length, ctx.mailSearch_('q', 0, 5, 'kamsg', 0).length,
  ctx.mailSearch_('q', 0, 5, 'ka', 0).length, ctx.mailSearch_('q', 0, 5, 'carrier', 0).length], [1, 2, 1, 0]);
check('since filter', ctx.mailSearch_('q', 0, 5, 'kamsg', Date.now() - 15 * 60000).length, 1);
check('newest first', ctx.mailSearch_('q', 0, 5, 'kamsg', 0)[0].getMessages()[0].getSubject(), honest.subject);

// 2) облік: пересланий лист про покупку записується так само, як з Gmail (лог — за id «relay:…»)
ctx.processLedger();
check('purchase from relayed mail', [deals.cells['5:3'], deals.cells['5:7'], deals.cells['5:11']], ['Kingston Fury 2x16GB DDR4', 95, 'Оплачено']);
check('logged once', [log.cells['2:1'], log.last], ['relay:<b1@ebay>', 2]);
ctx.processLedger();
check('not twice', log.last, 2);

// 3) фільтр чатів KA: скам — «в кошик» (без міток Gmail), чесне — у бот один раз
ctx.kaFilter();
check('scam trashed, not shown again', [JSON.parse(store.RELAY_TRASH), ctx.mailSearch_('q', 0, 5, 'kamsg', 0).length], [['<k1@ka>'], 1]);
check('honest digested', JSON.parse(store.KA_DIGESTED || '[]').indexOf('relay:<k2@ka>') >= 0, true);

// 4) сторож: найновіший лист KA — з пересланих, якщо там новіший
const wd = ctx.mailSearch_(vm.runInContext('WD_KA_QUERY', ctx), 0, 1, 'ka', Date.now() - 3 * 86400e3);
check('watchdog sees relayed KA mail', Math.round((Date.now() - Math.max.apply(null, wd.map((t) => t.getLastMessageDate().getTime()))) / 60000), 5);

// 5) MAIL_SOURCE = relay (проєкт на основному акаунті): Gmail не читається ні обробниками, ні check()
store.MAIL_SOURCE = 'relay';
gmailCalls = 0;
ctx.mailSearch_('q', 0, 5, 'ledger', 0);
ctx.processLedger();
ctx.kaFilter();
ctx.check();
check('Gmail untouched in relay mode', [gmailCalls, !!store.LAST_CHECK_OK], [0, true]);
delete store.MAIL_SOURCE;

// 6) Telegram і запит стану — як було
check('telegram update still html', ctx.doPost({ postData: { contents: JSON.stringify({ update_id: 5 }) }, parameter: {} }).html, 'ok');

console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
