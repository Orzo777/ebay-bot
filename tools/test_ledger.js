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

// 11.10: справжній лист eBay про продаж — сума під «Verkauft:», а «48 Stunden» і «Verkaufte Stückzahl: 1» — не ціна
p = ctx.parseMail_(mail('eBay <ebay@ebay.de>', '🎉 Artikel verkauft - Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200 CMK32...',
  'Gute Neuigkeiten - Ihr Artikel wurde verkauft!\nBestellstatus prüfen\nHallo i1_dev,\nIhr Käufer hat noch nicht bezahlt. Wir erinnern ihn ' +
  'daran, innerhalb von 48 Stunden zu bezahlen.\nCorsair Vengeance LPX 32GB (2x16GB) DDR4 3200 CMK32...\nVerkauft:\nEUR 149,00\n' +
  'Verkauft am:\n09. Okt. 2026\nKäufer:\nberwald08\nVerkaufte Stückzahl:\n1'));
check('ebay sale 11.10 format', [p.kind, p.price, p.total], ['sale', 149, null]);
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
// 11.10: справжня перша покупка з KA (Direkt kaufen): лист PayPal з цінами + лист KA без цін
const PP = 'Hello, Ivan\nYour purchase details\nPayment from\nIvan\nPayment to\nkleinanzeigen.de GmbH\nTransaction ID\n35L97\t' +
  'Transaction date\n11 October 2026\nTotal purchase amount\t96,54 € EUR\nInvoice ID\tQ27\nShipping address\nIvan\nHamburg\nGermany\n' +
  '2x Corsair Vengeance LPX DDR4 RAM 32GB (2x16GB) 2133MHz\t90,00 € EUR\nItem no: 1 | 90,00 € EUR\nVersand\t1,99 € EUR\n' +
  'Item no: 1 | 1,99 € EUR\nKäuferschutz\t4,55 € EUR\nItem no: 1 | 4,55 € EUR\nSubtotal\t96,54 € EUR\nTotal discount\t0,00 € EUR\n' +
  'Total amount\t96,54 € EUR';
p = ctx.parseMail_(mail('"service@paypal.de" <service@paypal.de>', 'You authorized a payment', PP));
check('paypal → ka purchase', [p.kind, p.src, p.title, p.price, p.ship, p.fee, p.total],
  ['purchase', 'Kleinanzeigen', '2x Corsair Vengeance LPX DDR4 RAM 32GB (2x16GB) 2133MHz', 90, 1.99, 4.55, 96.54]);
p = ctx.parseMail_(mail('"service@paypal.de" <service@paypal.de>', 'You authorized a payment', PP.replace('\t90,00', '\n90,00')));
check('paypal, price on next line', [p.title, p.price], ['2x Corsair Vengeance LPX DDR4 RAM 32GB (2x16GB) 2133MHz', 90]);
p = ctx.parseMail_(mail('"service@paypal.de" <service@paypal.de>', 'You authorized a payment', PP.replace('kleinanzeigen.de GmbH', 'Amazon EU')));
check('paypal for another shop — skipped', !!p.skip, true);
p = ctx.parseMail_(mail('Kleinanzeigen <noreply@kleinanzeigen.de>', 'Dein Kauf wurde bestätigt!',
  'Dein Kauf wurde bestätigt!\nTransaktion ID: 717d3ac0\nDer Verkäufer hat deinen Kauf des Artikels "2x Corsair Vengeance LPX DDR4 RAM ' +
  '32GB (2x16GB) 2133MHz" über "Direkt kaufen" bestätigt.\nAls Nächstes versendet der Verkäufer den Artikel.\n' +
  'Du bist durch den Kleinanzeigen-Käuferschutz abgesichert. Die Zahlung wird über Adyen abgewickelt.'));
check('ka «Kauf bestätigt»', [p.kind, p.src, p.title, p.price, p.fee],
  ['purchase', 'Kleinanzeigen', '2x Corsair Vengeance LPX DDR4 RAM 32GB (2x16GB) 2133MHz', null, null]);
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
cmd('купив 120 Corsair LPX amazon');
check('amazon channel (24.10)', [added[3].src, added[3].title], ['Amazon', 'Corsair LPX']);
check('foreign chat ignored', [cmd('купив 45 X', 999), added.length], [true, 4]);
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
check('office help = menu', /☰ Меню — обери розділ/.test(said[said.length - 1]), true);
// 13.10: кнопки клавіатури з емодзі → ті самі команди
const realLC = ctx.ledgerCommand;
vm.runInContext('ledgerCommand = function (m) { __said.push("LC:" + m.text); return true; };', ctx);
office('📒 Облік');
check('keyboard alias', said[said.length - 1], 'LC:облік');
ctx.ledgerCommand = realLC;
office('☰ Меню');
check('menu button', /☰ Меню — обери розділ/.test(said[said.length - 1]), true);
office('https://www.kleinanzeigen.de/s-anzeige/x/1-225-1');
check('office link not evaluated', /Не зрозумів/.test(said[said.length - 1]), true);
// 16.10: міні-хелпер — описки й фрази → команда мовчки; дві можливі — перепитує; решта тексту без змін
const fz = (x) => JSON.stringify(ctx.fuzzyCmd_(x));
check('typos → command', [fz('внставлено'), fz('посилкі'), fz('забарв 3'), fz('нп руках'), fz('Назав 3 Corsair LPX 2x16')],
  ['{"text":"виставлено"}', '{"text":"посилки"}', '{"text":"отримав 3"}', '{"text":"на руках"}', '{"text":"назва 3 Corsair LPX 2x16"}']);
check('phrases → command', [fz('що на продажі?'), fz('де посилки'), fz('скільки заробив'), fz('що ти вмієш')],
  ['{"text":"виставлено"}', '{"text":"посилки"}', '{"text":"облік"}', '{"text":"меню"}']);
check('ambiguous → ask', ctx.fuzzyCmd_('продат 3').ambiguous.sort(), ['продав 3', 'продати 3']);
check('not a command', [ctx.fuzzyCmd_('привіт як справи'), ctx.fuzzyCmd_('https://www.kleinanzeigen.de/s-anzeige/x/1'), ctx.fuzzyCmd_('продати 3')],
  [null, null, null]);
const said0 = said.length;
vm.runInContext('ledgerCommand = function (m) { if (m.text === "облік") { __said.push("LC:" + m.text); return true; } return false; };', ctx);
office('облiк');   // латинська «i» — описка
check('typo runs silently', [said.length - said0, said[said.length - 1]], [1, 'LC:облік']);
office('продат 3');
check('ambiguous asks', /Не впевнений.*«продав 3» чи «продати 3»|Не впевнений.*«продати 3» чи «продав 3»/.test(said[said.length - 1]), true);
ctx.ledgerCommand = realLC;
office('купив 20 Kingston 8GB');
check('office ledger command', added[added.length - 1].price, 20);

console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
