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
// 24.10: відповідь продавця — не реплаєм на картку в ebay_bot (GitHub), а окремим повідомленням з кнопкою на оголошення
// (у проєкті — бот «Облік і продаж» через notify_; тут notify_ ще немає — запасний шлях)
if (gh.length !== 0 || tg.length !== before + 1 || !/s-anzeige\/3528457610/.test(tg[tg.length - 1].reply_markup)) {
  bad++; console.log('!! seller reply with ad link', gh.length, tg.length - before, JSON.stringify(tg[tg.length - 1]));
}

// 23.10: покупець пише щодо НАШОГО оголошення — «🛍 Покупець пише» у бот обліку, не «Відповідь продавця» і не в GitHub
const sentOffice = [], gh0 = gh.length;
ctxRun(`notify_ = function (t, kb) { __so.push([t, kb]); };`, { __so: sentOffice });
const mkB = (id, body, subj) => { const m = { getId: () => id, getPlainBody: () => body, getBody: () => body, getSubject: () => subj };
  return { getMessages: () => [m], addLabel: () => {}, moveToTrash: () => trashed.push(id) }; };
ctxRun(`__threads = [__mkB('d', 'Hallo hätte Interesse', 'Nutzer-Anfrage zu deiner Anzeige „Patriot Viper 4 8GB Kit“')]`, { __mkB: mkB });
ctx.kaFilter();
if (sentOffice.length !== 1 || !/Покупець пише/.test(sentOffice[0][0]) || /Відповідь продавця/.test(sentOffice[0][0]) ||
    !/Patriot Viper/.test(sentOffice[0][0]) || gh.length !== gh0) {
  bad++; console.log('!! buyer message', JSON.stringify(sentOffice), gh.length - gh0);
}
if (!ctx.kaFromBuyer_('Nachricht zu „RAM“', 'x', { from: 'buyer' }) || ctx.kaFromBuyer_('Nachricht zu „RAM“', 'x', { from: 'seller' })) {
  bad++; console.log('!! kaFromBuyer_ by Gemini role');
}
if (!/Схоже на шахрая/.test(ctx.kaBuyerText_('zu deiner Anzeige „X“', 'Schreib mir per WhatsApp', null))) { bad++; console.log('!! buyer scam'); }
// 24.10: «Nur Abholung», а покупець «20 € inkl. Versand?» — без сповіщення; оголошення з доставкою — як звичайно
const so0 = sentOffice.length;
let kaPage = '<div>Nur Abholung</div>';
ctxRun(`UrlFetchApp = { fetch: (u) => ({ getContentText: () => __page(), getResponseCode: () => 200 }) };`, { __page: () => kaPage });
ctxRun(`__threads = [__mkB('e', 'Hallo, wären sie mit 20€ inkl Versand einverstanden? https://www.kleinanzeigen.de/s-anzeige/patriot/3548111222-225-1', 'Nutzer-Anfrage zu deiner Anzeige „Patriot Viper 4“')]`, { __mkB: mkB });
ctx.kaFilter();
if (sentOffice.length !== so0) { bad++; console.log('!! shipping ask on pickup-only should be muted'); }
kaPage = '<div>+ Versand ab 4,89 €</div>';
ctxRun(`__threads = [__mkB('f', 'Würden Sie auch versenden? https://www.kleinanzeigen.de/s-anzeige/x/3548333444-225-1', 'Nutzer-Anfrage zu deiner Anzeige „Corsair“')]`, { __mkB: mkB });
ctx.kaFilter();
if (sentOffice.length !== so0 + 1) { bad++; console.log('!! listing with shipping — buyer message must come'); }
// 24.10: пропозиції на НАШЕ оголошення — пороги як на eBay від ціни на KA (124 €: від 115 погоджуйся, нижче 108 — тихо)
store.GEMINI_API_KEY = 'k';
let gem = null;
ctxRun(`UrlFetchApp = { fetch: (u) => /generativelanguage/.test(u)
  ? { getResponseCode: () => 200, getContentText: () => JSON.stringify({ candidates: [{ content: { parts: [{ text: JSON.stringify(__gem()) }] } }] }) }
  : { getResponseCode: () => 200, getContentText: () => '<span itemprop="price" content="124"></span> + Versand ab 4,89 €' } };`, { __gem: () => gem });
const so1 = sentOffice.length;
gem = { from: 'buyer', message: 'Würden Sie 90 € nehmen?', price: 90, intent: 'counter', listing: 'Corsair' };
ctxRun(`__threads = [__mkB('g', 'Würden Sie 90 € nehmen? https://www.kleinanzeigen.de/s-anzeige/c/3548555666-225-1', 'Nutzer-Anfrage zu deiner Anzeige „Corsair“')]`, { __mkB: mkB });
ctx.kaFilter();
if (sentOffice.length !== so1) { bad++; console.log('!! lowball below threshold must be muted'); }
gem = { from: 'buyer', message: 'Würden Sie 118 € nehmen?', price: 118, intent: 'counter', listing: 'Corsair' };
ctxRun(`__threads = [__mkB('h', 'Würden Sie 118 € nehmen? https://www.kleinanzeigen.de/s-anzeige/c/3548555666-225-1', 'Nutzer-Anfrage zu deiner Anzeige „Corsair“')]`, { __mkB: mkB });
ctx.kaFilter();
if (sentOffice.length !== so1 + 1 || !/Пропонує <b>118 €<\/b> — ✅ не нижче порогу \(від 115 € при ціні 124 €\)/.test(sentOffice[sentOffice.length - 1][0])) {
  bad++; console.log('!! offer above threshold', JSON.stringify(sentOffice[sentOffice.length - 1]));
}
gem = { from: 'buyer', message: '110?', price: 110, intent: 'counter', listing: 'Corsair' };
ctxRun(`__threads = [__mkB('i', '110? https://www.kleinanzeigen.de/s-anzeige/c/3548555666-225-1', 'Nutzer-Anfrage zu deiner Anzeige „Corsair“')]`, { __mkB: mkB });
ctx.kaFilter();
if (!/зустрічна: <b>115 €<\/b> \(ціна 124 €\)/.test(sentOffice[sentOffice.length - 1][0])) { bad++; console.log('!! counter between thresholds'); }

console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
