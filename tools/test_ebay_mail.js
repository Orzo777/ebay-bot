// Листи eBay про продаж → бот (node tools/test_ebay_mail.js): питання, пропозиції, повернення, виплати
const fs = require('fs');
const vm = require('vm');
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O', GEMINI_API_KEY: 'G' };
const sent = [];
let gemini = [], mails = [];
const pad = (x) => ('0' + x).slice(-2);
function sheet() {
  const cells = {};
  const s = { cells, last: 4, rows: [], getLastRow: () => s.last, appendRow: (r) => { s.rows.push(r); } };
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
const deals = sheet(), log = sheet();
[['Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200', 'Продано', 164.49, '2026-10-12'], ['OWC 32GB DDR4 SO-DIMM', 'Продано', 120, '2026-10-01'],
 ['SK Hynix 2x16GB ECC UDIMM', 'Перевірено', '', '']].forEach((v, i) => {
  const r = 5 + i;
  deals.cells[r + ':2'] = new Date('2026-09-28T10:00:00Z'); deals.cells[r + ':3'] = v[0]; deals.cells[r + ':11'] = v[1];
  deals.cells[r + ':14'] = v[2]; deals.cells[r + ':12'] = v[3] ? new Date(v[3] + 'T10:00:00Z') : ''; deals.last = r;
});
const msg = (id, subject, body, html) => ({ getId: () => id, getFrom: () => 'eBay <ebay@ebay.de>', getSubject: () => subject,
  getPlainBody: () => body, getBody: () => html || body, getDate: () => new Date('2026-10-13T10:00:00Z') });
const ctx = {
  console: { log: () => {} },
  Utilities: { formatDate: (d, tz, f) => ({ 'yyyy-MM-dd': d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) }[f]) },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => (k in store ? store[k] : null), setProperty: (k, v) => { store[k] = v; } }) },
  GmailApp: { search: () => mails.map((m) => ({ getMessages: () => [m] })) },
  UrlFetchApp: { fetch: (u) => {
    const g = gemini.shift();
    return { getResponseCode: () => (g ? 200 : 503), getContentText: () => JSON.stringify({ candidates: [{ content: { parts: [{ text: JSON.stringify(g) }] } }] }) };
  } },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs', 'kafilter.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
vm.runInContext(`ledger_ = function () { return { getSheetByName: () => __deals }; };
  notify_ = function (t, mk, mode) { __sent.push([t, mk, mode]); };`, Object.assign(ctx, { __deals: deals, __sent: sent }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };

// питання покупця → переклад + чернетка відповіді + кнопка в eBay
mails = [msg('q1', 'Frage zu Ihrem Artikel: Corsair Vengeance LPX 32GB', 'Hallo, laufen die Riegel mit XMP auf 3200? Gruß Tom',
  '<a href="https://www.ebay.de/mesg/ViewMessageDetail?id=1&amp;x=2">Antworten</a>')];
gemini = [{ kind: 'question', role: 'seller', item: 'Corsair Vengeance LPX 32GB', who: 'Tom', message: 'Laufen die Riegel mit XMP auf 3200?',
  uk: 'Питає, чи працює пам\'ять на 3200 з XMP.', reply_de: 'Hallo Tom, ja, die Module laufen mit XMP auf 3200 MHz. Viele Grüße' }];
const done = {};
check('one mail processed', ctx.processEbayMail_(log, done), 1);
let t = sent[sent.length - 1];
check('question card', [/❓ <b>Питання покупця<\/b>/.test(t[0]), /Tom: «Laufen/.test(t[0]), /🇺🇦 Питає/.test(t[0]), /<code>Hallo Tom, ja/.test(t[0]), t[2]],
      [true, true, true, true, 'HTML']);
check('button to eBay message (unescaped &)', t[1].inline_keyboard[0][0].url, 'https://www.ebay.de/mesg/ViewMessageDetail?id=1&x=2');
check('not twice', ctx.processEbayMail_(log, done), 0);

// пропозиція ціни (продавець)
mails = [msg('o1', 'Sie haben einen Preisvorschlag erhalten', 'Preisvorschlag: EUR 140,00 für Corsair ...')];
gemini = [{ kind: 'offer', role: 'seller', item: 'Corsair Vengeance LPX 32GB', amount: 140, uk: 'Покупець пропонує 140 €.' }];
ctx.processEbayMail_(log, done);
check('offer card', /🤝 <b>Пропозиція ціни<\/b>[\s\S]*140\.00 €[\s\S]*eBay прийме \/ відхилить сам/.test(sent[sent.length - 1][0]), true);

// повернення → що робити й строк
mails = [msg('r1', 'Der Käufer möchte einen Artikel zurückgeben', 'Grund: Artikel defekt. Bitte innerhalb von 3 Werktagen antworten.')];
gemini = [{ kind: 'return', role: 'seller', item: 'OWC 32GB', uk: 'Покупець каже, що несправна.', deadline: '3 робочі дні' }];
ctx.processEbayMail_(log, done);
check('return card', /↩️ <b>Повернення<\/b>[\s\S]*⏰ Строк: 3 робочі дні[\s\S]*3 робочих днів/.test(sent[sent.length - 1][0]), true);

// виплата → до останнього проданого без виплати; комісія ≈ 0% → підказка
mails = [msg('p1', 'Ihre Auszahlung wurde gesendet', 'Wir haben Ihre Auszahlung in Höhe von EUR 164,49 gesendet.')];
gemini = [{ kind: 'payout', role: 'seller', amount: 164.49, uk: 'Виплата 164,49 €.' }];
ctx.processEbayMail_(log, done);
t = sent[sent.length - 1][0];
check('payout matched to latest sale', [/164\.49 € — за №1 «Corsair/.test(t), /≈ <b>0\.0%<\/b>[\s\S]*комісії немає/.test(t)], [true, true]);
check('note written + event', [/виплата eBay 164\.49/.test(deals.cells['5:22']), JSON.parse(store.LEDGER_EVENTS)[1].payout], [true, 164.49]);
// друга виплата → наступний продаж (OWC), реальна комісія 6,5%
mails = [msg('p2', 'Auszahlung', 'Auszahlung EUR 112,20')];
gemini = [];   // Gemini недоступний — сума з тексту, тип з теми
ctx.processEbayMail_(log, done);
check('fallback payout w/o Gemini → next sale, fee shown', /112\.20 € — за №2 «OWC[\s\S]*≈ <b>6\.5%<\/b>[\s\S]*повернемо/.test(sent[sent.length - 1][0]), true);

// не наше (реклама з «Anfrage» у темі, Gemini каже other) — без повідомлення, але в лог
const n0 = sent.length;
mails = [msg('x1', 'Ihre Anfrage zu eBay Plus', 'Werbung')];
gemini = [{ kind: 'other' }];
ctx.processEbayMail_(log, done);
check('other → silent, logged', [sent.length, log.rows.some((r) => r[0] === 'x1')], [n0, true]);
// етикетка доставки → «Моя пересилка покупцю» останнього проданого без неї
mails = [msg('l1', 'Ihr Versandetikett', 'DHL Paket. Betrag: EUR 5,49')];
gemini = [{ kind: 'label', role: 'seller', amount: 5.49 }];
ctx.processEbayMail_(log, done);
check('label → sship of latest sale', [deals.cells['5:15'], /5\.49 € — записав у №1/.test(sent[sent.length - 1][0])], [5.49, true]);
mails = [msg('l2', 'Versandetikett gekauft', 'Betrag: EUR 6,19')];
gemini = [];
ctx.processEbayMail_(log, done);
check('second label → next sale (fallback w/o Gemini)', deals.cells['6:15'], 6.19);
// 10.10: виплата за мінусом етикетки (eBay віднімає її з виплати) — це не комісія: 120 − 6,19 = 113,81 → 0%
const ev = JSON.parse(store.LEDGER_EVENTS); delete ev[2].payout; store.LEDGER_EVENTS = JSON.stringify(ev);
mails = [msg('p3', 'Ihre Auszahlung wurde gesendet', 'Auszahlung EUR 113,81')];
gemini = [{ kind: 'payout', role: 'seller', amount: 113.81 }];
ctx.processEbayMail_(log, done);
t = sent[sent.length - 1][0];
check('payout minus label → 0% fee', [/мінус етикетка 6\.19 €/.test(t), /≈ <b>0\.0%<\/b>/.test(t)], [true, true]);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
