// Перевірка фільтра шахрайських відповідей KA (node tools/test_kafilter.js)
const fs = require('fs');
const vm = require('vm');
const ctx = {};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(__dirname + '/kafilter.gs', 'utf8') + ';this.r = kaScamReason_; this.t = kaMessageText_;', ctx);
const ctxRun = (code, extra) => vm.runInContext(code, Object.assign(ctx, extra || {}));
const cases = [
  ['Hi, haben Sie pay .Pal? Die ist noch unausgepackt Lg', true],   // справжня відповідь 28.09 (PS5)
  ['Hallo, zahlung per PayPal Freunde und Familie', true],
  ['Ja ist noch da. Schreib mir auf WhatsApp 0151 23456789', true],
  ['Gerne, nur Überweisung möglich', true],
  ['Paypal?', true],
  ['kontakt: max.mustermann@gmail.com', true],
  ['Ich kann auch per PayPal senden', true],
  ['F&F bitte', true],
  ['Schreib mir auf W h a t s A p p', true],
  ['Ja, noch verfügbar. Versand über Sicher bezahlen geht klar.', false],
  ['Hallo, ja ist noch da, lief immer fehlerfrei. Rechnung habe ich nicht.', false],
  ['PayPal Waren und Dienstleistungen ist ok', false],
  ['Du kannst es direkt kaufen, dann reserviere ich', false],
  ['Hi es sind 4×4 GB RAM für den preis passt es', false],
];
let bad = 0;
for (const [s, want] of cases) {
  const got = !!ctx.r(ctx.t(s));
  if (got !== want) { bad++; console.log('!!', s, got); }
}
// чесні відповіді → бот: суть без Gemini, текст картки, один раз на лист
const intents = [['Leider schon verkauft, sorry', 'sold'], ['Ist reserviert bis morgen', 'sold'],
  ['Für 75 € würde ich ihn abgeben', 'counter'], ['Letzter Preis 90 Euro', 'counter'], ['Ja, passt. Kaufen Sie direkt', 'agree'],
  ['Einverstanden, 70 ist ok', 'agree'], ['Ist noch da', 'available'], ['Welche Versandart möchten Sie?', 'question'],
  ['Hallo', 'other']];
for (const [t, want] of intents) { const got = ctx.kaIntent_(t); if (got !== want) { bad++; console.log('!! intent', t, got); } }
let card = ctx.kaDigestText_('Neue Nachricht von Tobias R. zu „2x 16GB Samsung DDR4-3200“', 'Für 75 € würde ich ihn abgeben', null);
if (!/Tobias R\./.test(card) || !/2x 16GB Samsung/.test(card) || !/Пропонує іншу ціну/.test(card) || !/🇩🇪 Für 75/.test(card)) { bad++; console.log('!! card', card); }
card = ctx.kaDigestText_('x', 'raw', { seller: 'Anna', listing: 'PS5 <Slim>', message: 'Ja, 70 passt', uk: 'Так, 70 підходить', intent: 'agree', price: 70 });
if (!/✅ Згоден — 70 €/.test(card) || !/🇺🇦 Так, 70 підходить/.test(card) || !/PS5 &lt;Slim&gt;/.test(card)) { bad++; console.log('!! card2', card); }
// потік kaFilter_: шахрая — у кошик, чесну — один раз у бот
const store = { KA_FILTER_TS: String(Date.now()), TELEGRAM_BOT_TOKEN: 'M', TELEGRAM_CHAT_ID: '7' }, tg = [], trashed = [];
const mk = (id, body) => { const m = { getId: () => id, getPlainBody: () => body, getBody: () => body + ' https://www.kleinanzeigen.de/m-nachrichten.html?conversationId=abc', getSubject: () => 'Nachricht zu „RAM“' };
  return { getMessages: () => [m], addLabel: () => {}, moveToTrash: () => trashed.push(id) }; };
ctxRun(`PropertiesService = { getScriptProperties: () => ({ getProperty: (k) => __store[k] || null, setProperty: (k, v) => { __store[k] = v; } }) };
  GmailApp = { search: () => __threads, getUserLabelByName: () => ({}), createLabel: () => ({}) };
  UrlFetchApp = { fetch: (u, o) => { __tg.push(o.payload); return { getResponseCode: () => 200 }; } };`,
  { __store: store, __tg: tg, __threads: [mk('a', 'Haben Sie PayPal Freunde?'), mk('b', 'Ja, ist noch da. Versand geht.')] });
ctx.kaFilter();
ctx.kaFilter();
if (trashed.join() !== 'a,a' || tg.length !== 1 || !/Ще є/.test(tg[0].text) || !/conversationId=abc/.test(tg[0].reply_markup)) {
  bad++; console.log('!! flow', trashed, tg.length, tg[0] && tg[0].text);
}

// 02.10: «акаунт зламали» і реплай через GitHub
for (const [t, want] of [['Ich habe diese Anzeige nicht erstellt, mein Account wurde gehackt!', 'hacked'],
                         ['Das ist nicht von mir, Betrüger haben mein Konto übernommen', 'hacked']]) {
  const got = ctx.kaIntent_(t); if (got !== want) { bad++; console.log('!! intent', t, got); }
}
const ids = ctx.kaAdIds_('x https://www.kleinanzeigen.de/s-anzeige/ram-ddr4/3528457610-225-1 y kleinanzeigen.de/s-anzeige/3528457610 z');
if (JSON.stringify(ids) !== '["3528457610"]') { bad++; console.log('!! ids', ids); }
const gh = [];
ctxRun(`seal_ = function (d, k, n) { return { blob: 'B:' + JSON.stringify(d).length, mac: 'M', nonce: n }; };
  officeToken_ = function () { return 'O'; }; REPO = 'o/r'; GITHUB_TOKEN = '';
  Utilities = { getUuid: () => 'u-1' };
  UrlFetchApp = { fetch: (u, o) => { if (/github/.test(u)) { __gh.push([u, JSON.parse(o.payload)]); return { getResponseCode: () => 204 }; }
                                     __tg.push(o.payload); return { getResponseCode: () => 200 }; } };`, { __gh: gh });
store.GITHUB_TOKEN = 'g';
const before = tg.length;
ctxRun(`__threads = [__mk('c', 'Ja, 70 passt. https://www.kleinanzeigen.de/s-anzeige/3528457610')]`, { __mk: mk });
ctx.kaFilter();
if (gh.length !== 1 || !/ka_reply\.yml\/dispatches$/.test(gh[0][0]) || tg.length !== before || /70 passt/.test(JSON.stringify(gh[0][1]))) {
  bad++; console.log('!! via github', gh.length, tg.length - before, JSON.stringify(gh[0] && gh[0][1]));
}

console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
