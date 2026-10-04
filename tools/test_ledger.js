// Перевірка розбору листів ledger.gs без Google (node tools/test_ledger.js). Теми й тексти — у форматі листів eBay.de / KA.
const fs = require('fs');
const vm = require('vm');
const ctx = {};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(__dirname + '/ledger.gs', 'utf8') + '\nthis.parseMail_ = parseMail_; this.words_ = words_; this.category_ = category_; this.officeToken_ = officeToken_;', ctx);

function mail(from, subject, body, html) {
  return { getFrom: () => from, getSubject: () => subject, getPlainBody: () => body, getBody: () => html || body,
           getDate: () => new Date('2026-09-29T12:00:00Z'), getId: () => 'x' };
}
let bad = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { bad++; console.log('!!', name, 'got', JSON.stringify(got), 'want', JSON.stringify(want)); }
}

let p = ctx.parseMail_(mail('eBay <ebay@ebay.de>', 'Bestellbestätigung für: RAM ddr4 32GB für iMac',
  'Vielen Dank für Ihren Kauf!\nRAM ddr4 32GB für iMac\nArtikelnummer: 317456789012\nArtikelpreis: EUR 45,00\n' +
  'Versand: EUR 4,99\nGesamt: EUR 49,99', '<a href="https://www.ebay.de/itm/317456789012">x</a>'));
check('ebay purchase kind', p.kind, 'purchase');
check('ebay purchase fields', [p.src, p.title, p.id, p.price, p.ship, p.total], ['eBay', 'RAM ddr4 32GB für iMac', '317456789012', 45, 4.99, 49.99]);

// 03.10: новий формат листа eBay — мітки й суми на різних рядках, «Zwischensumme» перед «Versand»; було 58,25 замість 64,44
p = ctx.parseMail_(mail('eBay <ebay@ebay.de>', 'Bestellbestätigung für: Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200',
  'Einzelheiten zu Ihrem Kauf\nWir benachrichtigen Sie, wenn Ihre Bestellung verschickt wird.\n' +
  'Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200 PC4-25600 DIMM 288-Pin XMP\nPreis:\nEUR 64,44\nArtikelnr.:\n860004357101\n' +
  'Bestellnummer:\n02-15262-92402\nVerkäufer:\nbrhi-3585\neBay-Käuferschutz\nEinzelheiten zum Kauf ansehen\n' +
  'Gesamtbetrag:\nZwischensumme\nEUR 64,44\nVersand\nEUR 6,19\nGesamtbetrag wird abgebucht von\nEUR 70,63'));
check('ebay purchase 03.10 format', [p.kind, p.id, p.price, p.ship, p.fee, p.total], ['purchase', '860004357101', 64.44, 6.19, null, 70.63]);
// тема обрізана eBay («…LP...») — повна назва з тексту листа, інакше категорія «Інше» (03.10)
p = ctx.parseMail_(mail('eBay <ebay@ebay.de>', 'Bestellbestätigung für: Corsair Vengeance LP...',
  'Einzelheiten zu Ihrem Kauf\nCorsair Vengeance LPX 32GB (2x16GB) DDR4-3200 PC4-25600 DIMM 288-Pin XMP\nPreis:\nEUR 64,44'));
check('truncated subject title', [p.title, ctx.category_(p.title)],
  ['Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200 PC4-25600 DIMM 288-Pin XMP', 'RAM']);
p = ctx.parseMail_(mail('eBay <ebay@ebay.de>', 'Bestellbestätigung für: Corsair Vengeance LP...', 'Preis: EUR 64,44'));
check('truncated, no body line', p.title, 'Corsair Vengeance LP...');
p = ctx.parseMail_(mail('eBay <ebay@ebay.de>', 'Bestellbestätigung für: RAM', 'Zwischensumme EUR 40,00\nVersand EUR 5,00\nSumme EUR 45,00'));
check('Summe is not Zwischensumme', [p.price, p.ship, p.total], [40, 5, 45]);

p = ctx.parseMail_(mail('eBay <ebay@ebay.de>', 'Ihre Bestellung ist bestätigt: Sony PS5 Slim Disc', 'Gesamtbetrag 1.234,56 €\nVersand 0,00 €'));
check('total only', [p.kind, p.price, p.total], ['purchase', 1234.56, 1234.56]);

p = ctx.parseMail_(mail('eBay <ebay@ebay.de>', 'Sie haben einen Artikel verkauft: OWC 32GB DDR4 iMac', 'Verkaufspreis: EUR 110,00\nGesamt: EUR 116,19'));
check('ebay sale', [p.kind, p.title, p.total], ['sale', 'OWC 32GB DDR4 iMac', 116.19]);

p = ctx.parseMail_(mail('eBay <ebay@ebay.de>', 'Ihr Artikel wurde versandt: RAM ddr4 32GB für iMac', 'Artikelnummer: 317456789012'));
check('shipped', [p.kind, p.id], ['shipped', '317456789012']);
p = ctx.parseMail_(mail('eBay <ebay@ebay.de>', 'Ihr Paket wurde zugestellt', '/itm/317456789012'));
check('delivered', [p.kind, p.id], ['delivered', '317456789012']);
p = ctx.parseMail_(mail('eBay <ebay@ebay.de>', 'Bestellung storniert: RAM', ''));
check('cancel', p.kind, 'cancel');
p = ctx.parseMail_(mail('eBay <ebay@ebay.de>', 'Neue Angebote für Sie: DDR5', ''));
check('ad skipped', !!p.skip, true);
p = ctx.parseMail_(mail('eBay <ebay@ebay.de>', 'Glückwunsch! Sie haben gewonnen: Nintendo Switch OLED', 'Preis: EUR 95,00'));
check('auction won', [p.kind, p.price], ['purchase', 95]);

p = ctx.parseMail_(mail('Kleinanzeigen <noreply@kleinanzeigen.de>', 'Du hast „Corsair Vengeance 32GB DDR5" gekauft',
  'Kaufpreis 150,00 €\nVersand 6,19 €\nKäuferschutz 5,50 €\nGesamt 161,69 €\nhttps://www.kleinanzeigen.de/s-anzeige/corsair/123-225-1'));
check('ka purchase', [p.kind, p.src, p.title, p.price, p.ship, p.fee], ['purchase', 'Kleinanzeigen', 'Corsair Vengeance 32GB DDR5', 150, 6.19, 5.5]);
p = ctx.parseMail_(mail('Kleinanzeigen <noreply@kleinanzeigen.de>', 'Dein Artikel „OWC 32GB" wurde gekauft', 'Kaufpreis 110,00 €'));
check('ka sale', [p.kind, p.title], ['sale', 'OWC 32GB']);
p = ctx.parseMail_(mail('Kleinanzeigen <noreply@kleinanzeigen.de>', 'Neue Anzeigen für „ddr5 32gb"', ''));
check('ka alert skipped', !!p.skip, true);

check('category', [ctx.category_('OWC 2x16 DDR4'), ctx.category_('PS5 Slim'), ctx.category_('Dell Optiplex'), ctx.category_('Lampe')],
  ['RAM', 'Консоль', 'ПК', 'Інше']);
// команди з Telegram: підміняємо Google-сервіси
const added = [], said = [];
ctx.PropertiesService = { getScriptProperties: () => ({ getProperty: (k) => ({ TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L' })[k] }) };
vm.runInContext('addPurchase_ = function (d) { __added.push(d); return 5; }; notify_ = function (t) { __said.push(t); };', Object.assign(ctx, { __added: added, __said: said }));
const cmd = (text, chat) => vm.runInContext('ledgerCommand(__m)', Object.assign(ctx, { __m: { text: text, chat: { id: chat || 7 } } }));
check('buy handled', cmd('купив 45 OWC 2x16 DDR4'), true);
check('buy fields', [added[0].price, added[0].title, added[0].src], [45, 'OWC 2x16 DDR4', 'Kleinanzeigen']);
cmd('купив OWC 2x16 за 45,50 ebay');
check('buy price after "за"', [added[1].price, added[1].title, added[1].src], [45.5, 'OWC 2x16', 'eBay']);
cmd('Купив 30 € Kingston 16GB самовивіз');
check('pickup', [added[2].price, added[2].src, added[2].status], [30, 'Самовивіз Гамбург', 'Отримано']);
check('foreign chat ignored', [cmd('купив 45 X', 999), added.length], [true, 3]);
check('not a command', cmd('https://www.kleinanzeigen.de/s-anzeige/x/1-225-1'), false);
check('no price', [cmd('купив OWC'), /спершу сума/.test(said[said.length - 1])], [true, true]);

// другий бот «Облік і продаж»
check('office token', [ctx.officeToken_({ getProperty: (k) => ({ OFFICE_BOT_TOKEN: 'O', TELEGRAM_BOT_TOKEN: 'M' })[k] }),
                        ctx.officeToken_({ getProperty: (k) => ({ TELEGRAM_BOT_TOKEN: 'M' })[k] })], ['O', 'M']);
const office = (text, chat) => vm.runInContext('officeMessage(__m)', Object.assign(ctx, { __m: { text: text, chat: { id: chat || 7 } } }));
let n = said.length;
office('допомога', 999);
check('office foreign chat silent', said.length, n);
office('допомога');
check('office help', /облік і продаж/i.test(said[said.length - 1]), true);
office('https://www.kleinanzeigen.de/s-anzeige/x/1-225-1');
check('office link not evaluated', /Не зрозумів/.test(said[said.length - 1]), true);
office('купив 20 Kingston 8GB');
check('office ledger command', added[added.length - 1].price, 20);

console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
