/**
 * Облік перепродажу — сам заповнює Google-таблицю з листів eBay і Kleinanzeigen (Google Apps Script,
 * працює у ВАШОМУ Google-акаунті, у тому ж проєкті, що й gmail_trigger.gs).
 *
 * Що робить:
 *  • кожні 15 хв читає в Gmail листи eBay/KA про покупки, продажі, відправлення, доставку, скасування;
 *  • покупка → новий рядок у «Угоди»; «відправлено/доставлено» → статус; продаж → ціна й дата продажу
 *    в рядку того самого товару (шукає за словами з назви), прибуток рахує таблиця;
 *  • незрозумілі транзакційні листи → аркуш «Лог листів» (нічого не губиться, видно, що дописати вручну);
 *  • команди в Telegram-боті (лише з вашого чату): «купив 45 OWC 2x16 DDR4», «продав 110 OWC», «облік».
 *
 * Установка (один раз, ~5 хв):
 *   1. script.google.com → ваш проєкт → «+» → «Скрипт» → назва ledger → вставити цей файл → 💾.
 *   2. (Для відповідей у Telegram) ⚙ «Налаштування проєкту» → «Властивості скрипту» → додати
 *      TELEGRAM_BOT_TOKEN і TELEGRAM_CHAT_ID (ті самі, що в секретах GitHub). Без них облік працює, але бот мовчить
 *      і команди «купив/продав» вимкнені (захист: писати в таблицю може лише ваш чат).
 *   3. Угорі вибрати функцію setupLedger → «Виконати» → дозволити доступ (Gmail + Таблиці) → у журналі буде посилання.
 *   4. Щоб команди «купив/продав» працювали, оновіть і Code.gs (gmail_trigger.gs з репозиторію) та
 *      «Розгорнути» → «Керування розгортаннями» → ✏ → «Версія: нова» → «Розгорнути».
 *   5. (Окремий бот «Облік і продаж», щоб не змішувати з картками покупок) BotFather → /newbot → токен у властивість
 *      OFFICE_BOT_TOKEN → у новому боті натиснути «Start» → функція connectOfficeBot → «Виконати».
 */

const VER_LEDGER = '2026-10-04c';   // версія файлу: сторож порівнює з GitHub і нагадує оновити (при зміні файлу — підняти)
const LEDGER_TITLE = 'Облік перепродажу';
const LEDGER_FIRST = 5;          // перший рядок даних в «Угоди»
const EUR_FMT = '#,##0.00 "€";-#,##0.00 "€";"–"';
const PCT_FMT = '0.0%;-0.0%;"–"';
const HEADERS = ['№', 'Дата купівлі', 'Товар (що саме)', 'Категорія', 'Де купив', 'Посилання / продавець', 'Ціна купівлі, €',
  'Пересилка / дорога, €', 'Збір (Sicher bezahlen), €', 'Витрачено разом, €', 'Статус', 'Дата продажу', 'Де продав',
  'Ціна продажу (з пересилкою від покупця), €', 'Моя пересилка покупцю, €', 'Пакування, €', 'Комісія eBay, €',
  'Чистими з продажу, €', 'Прибуток, €', 'Маржа', 'Днів до продажу', 'Нотатки', 'ID (службове)'];
const COL = { n: 1, date: 2, title: 3, cat: 4, src: 5, link: 6, price: 7, ship: 8, fee: 9, spent: 10, status: 11,
  sdate: 12, sto: 13, sprice: 14, sship: 15, pack: 16, comm: 17, net: 18, profit: 19, margin: 20, days: 21, note: 22, id: 23 };

// ------------------------------------------------------------------ створення таблиці
function setupLedger() {
  const props = PropertiesService.getScriptProperties();
  let ss = null;
  if (props.getProperty('LEDGER_ID')) {
    try { ss = SpreadsheetApp.openById(props.getProperty('LEDGER_ID')); } catch (e) { ss = null; }
  }
  if (!ss) {
    ss = SpreadsheetApp.create(LEDGER_TITLE);
    props.setProperty('LEDGER_ID', ss.getId());
    buildLedger_(ss);
  }
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'processLedger') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('processLedger').timeBased().everyMinutes(15).create();
  expensesSheet_(ss);
  processLedger();
  console.log('Облік готовий: ' + ss.getUrl());
}

function buildLedger_(ss) {
  const deals = ss.getSheets()[0];
  deals.setName('Угоди');
  const sum = ss.insertSheet('Підсумок');
  const set = ss.insertSheet('Налаштування');
  const log = ss.insertSheet('Лог листів');

  // Налаштування
  set.getRange('A1').setValue('Налаштування розрахунку').setFontSize(14).setFontWeight('bold');
  set.getRange('A3:C5').setValues([
    ['Комісія eBay за продаж, % від суми', 0.065, 'Як у моделі бота (6,5%). Приватні продавці на eBay.de часто платять 0% — зміни після першого продажу.'],
    ['Фіксований збір eBay за замовлення, €', 0.45, 'Як у моделі бота (0,45 €). Постав 0, якщо не стягують.'],
    ['Пакування за замовчуванням, €', 4, 'Підставляється, коли «Пакування» в «Угоди» порожнє.']]);
  set.getRange('B3').setNumberFormat(PCT_FMT);
  set.getRange('B4:B5').setNumberFormat(EUR_FMT);
  set.getRange('B3:B5').setBackground('#FFF2CC').setFontColor('#0000FF');
  set.getRange('A8').setValue('Списки для меню (можна дописувати):').setFontWeight('bold');
  const lists = [['Категорія', 'Де купив / продав', 'Статус'], ['RAM', 'Kleinanzeigen', 'Оплачено'], ['Консоль', 'eBay', 'В дорозі'],
    ['ПК', 'Самовивіз Гамбург', 'Отримано'], ['Запчастини', 'Інше', 'Перевірено'], ['Інше', '', 'Виставлено'], ['', '', 'Продано'],
    ['', '', 'Повернено'], ['', '', 'Скасовано'], ['', '', 'Проблема']];
  set.getRange(9, 1, lists.length, 3).setValues(lists);
  set.getRange('A9:C9').setFontWeight('bold');
  set.setColumnWidth(1, 330); set.setColumnWidth(2, 140); set.setColumnWidth(3, 560);

  // Угоди
  deals.getRange('A1').setValue('Облік перепродажу — купівлі, продажі, прибуток').setFontSize(14).setFontWeight('bold');
  deals.getRange('A2').setValue('Заповнюється сам із листів eBay/KA (кожні 15 хв) і з команд боту «купив … / продав …». ' +
    'Жовті клітинки можна правити, сірі рахує таблиця.').setFontStyle('italic').setFontColor('#595959');
  deals.getRange(4, 1, 1, HEADERS.length).setValues([HEADERS]).setFontWeight('bold').setFontColor('#FFFFFF')
    .setBackground('#1F3864').setWrap(true).setVerticalAlignment('middle');
  deals.setRowHeight(4, 48);
  const widths = [40, 90, 300, 90, 110, 200, 90, 90, 90, 100, 95, 90, 100, 110, 90, 80, 90, 100, 90, 70, 70, 260, 110];
  widths.forEach(function (w, i) { deals.setColumnWidth(i + 1, w); });
  deals.hideColumns(COL.id);
  deals.setFrozenRows(4);
  deals.setFrozenColumns(3);
  const rule = function (col, from) {
    return SpreadsheetApp.newDataValidation().requireValueInRange(set.getRange(from + '10:' + from + '30'), true)
      .setAllowInvalid(true).build();
  };
  deals.getRange('D5:D1000').setDataValidation(rule(COL.cat, 'A'));
  deals.getRange('E5:E1000').setDataValidation(rule(COL.src, 'B'));
  deals.getRange('M5:M1000').setDataValidation(rule(COL.sto, 'B'));
  deals.getRange('K5:K1000').setDataValidation(rule(COL.status, 'C'));
  const cf = deals.getConditionalFormatRules();
  cf.push(SpreadsheetApp.newConditionalFormatRule().whenNumberGreaterThan(0).setFontColor('#006100')
    .setRanges([deals.getRange('S5:S1000')]).build());
  cf.push(SpreadsheetApp.newConditionalFormatRule().whenNumberLessThan(0).setFontColor('#9C0006')
    .setRanges([deals.getRange('S5:S1000')]).build());
  cf.push(SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo('Продано').setBackground('#C6EFCE')
    .setRanges([deals.getRange('K5:K1000')]).build());
  cf.push(SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied('=OR($K5="Проблема",$K5="Повернено")')
    .setBackground('#FFC7CE').setRanges([deals.getRange('K5:K1000')]).build());
  deals.setConditionalFormatRules(cf);

  // Підсумок
  const R = function (c) { return "'Угоди'!" + c + '5:' + c; };
  sum.getRange('A1').setValue('Підсумок').setFontSize(14).setFontWeight('bold');
  const rows = [
    ['Угод усього', '=COUNTA(' + R('C') + ')', '0'],
    ['Продано', '=COUNTIF(' + R('K') + ',"Продано")', '0'],
    ['Ще не продано', '=B3-B4-COUNTIF(' + R('K') + ',"Повернено")-COUNTIF(' + R('K') + ',"Скасовано")', '0'],
    ['Витрачено на всі купівлі, €', '=SUM(' + R('J') + ')', EUR_FMT],
    ['Гроші в товарі зараз, €', '=SUMIFS(' + R('J') + ',' + R('C') + ',"<>",' + R('K') + ',"<>Продано",' + R('K') +
      ',"<>Повернено",' + R('K') + ',"<>Скасовано")', EUR_FMT],
    ['Виручка з продажів (чистими), €', '=SUM(' + R('R') + ')', EUR_FMT],
    ['Прибуток з проданого, €', '=SUM(' + R('S') + ')', EUR_FMT],
    ['Середня маржа проданого', '=IFERROR(B9/SUMIFS(' + R('J') + ',' + R('K') + ',"Продано"),0)', PCT_FMT],
    ['Середньо днів до продажу', '=IFERROR(AVERAGE(' + R('U') + '),0)', '0.0']];
  rows.forEach(function (r, i) {
    sum.getRange(3 + i, 1).setValue(r[0]);
    sum.getRange(3 + i, 2).setFormula(r[1]).setNumberFormat(r[2]).setFontWeight('bold').setBackground('#F2F2F2');
  });
  sum.getRange('A14:E14').setValues([['Категорія', 'Угод', 'Продано', 'Витрачено, €', 'Прибуток, €']])
    .setFontWeight('bold').setFontColor('#FFFFFF').setBackground('#1F3864');
  ['RAM', 'Консоль', 'ПК', 'Запчастини', 'Інше'].forEach(function (c, i) {
    const r = 15 + i;
    sum.getRange(r, 1).setValue(c);
    sum.getRange(r, 2).setFormula('=COUNTIF(' + R('D') + ',A' + r + ')');
    sum.getRange(r, 3).setFormula('=COUNTIFS(' + R('D') + ',A' + r + ',' + R('K') + ',"Продано")');
    sum.getRange(r, 4).setFormula('=SUMIFS(' + R('J') + ',' + R('D') + ',A' + r + ')').setNumberFormat(EUR_FMT);
    sum.getRange(r, 5).setFormula('=SUMIFS(' + R('S') + ',' + R('D') + ',A' + r + ')').setNumberFormat(EUR_FMT);
  });
  sum.getRange('A22:D22').setValues([['Де купив', 'Угод', 'Витрачено, €', 'Прибуток, €']])
    .setFontWeight('bold').setFontColor('#FFFFFF').setBackground('#1F3864');
  ['Kleinanzeigen', 'eBay', 'Самовивіз Гамбург', 'Інше'].forEach(function (c, i) {
    const r = 23 + i;
    sum.getRange(r, 1).setValue(c);
    sum.getRange(r, 2).setFormula('=COUNTIF(' + R('E') + ',A' + r + ')');
    sum.getRange(r, 3).setFormula('=SUMIFS(' + R('J') + ',' + R('E') + ',A' + r + ')').setNumberFormat(EUR_FMT);
    sum.getRange(r, 4).setFormula('=SUMIFS(' + R('S') + ',' + R('E') + ',A' + r + ')').setNumberFormat(EUR_FMT);
  });
  sum.setColumnWidth(1, 300);

  // Лог листів
  log.getRange('A1:G1').setValues([['ID листа', 'Дата', 'Від кого', 'Тема', 'Що зроблено', 'Рядок', 'Уривок']])
    .setFontWeight('bold');
  log.setFrozenRows(1);
  log.setColumnWidth(4, 360); log.setColumnWidth(7, 500);
}

function ledger_() {
  return SpreadsheetApp.openById(PropertiesService.getScriptProperties().getProperty('LEDGER_ID'));
}

// ------------------------------------------------------------------ рядки
function writeFormulas_(sh, r) {
  sh.getRange(r, COL.n).setFormula('=IF(C' + r + '="","",ROW()-4)');
  sh.getRange(r, COL.spent).setFormula('=IF(C' + r + '="","",N(G' + r + ')+N(H' + r + ')+N(I' + r + '))');
  const S = "'Налаштування'!";
  sh.getRange(r, COL.comm).setFormula('=IF(N' + r + '="","",ROUND(N' + r + '*' + S + '$B$3+' + S + '$B$4,2))');
  sh.getRange(r, COL.net).setFormula('=IF(N' + r + '="","",N' + r + '-Q' + r + '-N(O' + r + ')-IF(P' + r + '="",' + S + '$B$5,P' + r + '))');
  sh.getRange(r, COL.profit).setFormula('=IF(OR(N' + r + '="",C' + r + '=""),"",R' + r + '-J' + r + ')');
  sh.getRange(r, COL.margin).setFormula('=IF(S' + r + '="","",IF(J' + r + '=0,"",S' + r + '/J' + r + '))');
  sh.getRange(r, COL.days).setFormula('=IF(OR(B' + r + '="",L' + r + '=""),"",L' + r + '-B' + r + ')');
  sh.getRange(r, COL.date).setNumberFormat('dd.mm.yyyy');
  sh.getRange(r, COL.sdate).setNumberFormat('dd.mm.yyyy');
  [COL.price, COL.ship, COL.fee, COL.spent, COL.sprice, COL.sship, COL.pack, COL.comm, COL.net, COL.profit]
    .forEach(function (c) { sh.getRange(r, c).setNumberFormat(EUR_FMT); });
  sh.getRange(r, COL.margin).setNumberFormat(PCT_FMT);
  [COL.n, COL.spent, COL.comm, COL.net, COL.profit, COL.margin, COL.days]
    .forEach(function (c) { sh.getRange(r, c).setBackground('#F2F2F2'); });
}

function nextRow_(sh) {
  const vals = sh.getRange(LEDGER_FIRST, COL.title, Math.max(sh.getLastRow() - LEDGER_FIRST + 1, 1), 1).getValues();
  for (let i = vals.length - 1; i >= 0; i--) if (vals[i][0] !== '') return LEDGER_FIRST + i + 1;
  return LEDGER_FIRST;
}

function addPurchase_(d) {
  const sh = ledger_().getSheetByName('Угоди');
  const r = nextRow_(sh);
  sh.getRange(r, COL.date).setValue(d.date || new Date());
  sh.getRange(r, COL.title).setValue(d.title || '(без назви)');
  sh.getRange(r, COL.cat).setValue(category_(d.title));
  sh.getRange(r, COL.src).setValue(d.src || '');
  if (d.link) sh.getRange(r, COL.link).setValue(d.link);
  if (d.price != null) sh.getRange(r, COL.price).setValue(d.price);
  sh.getRange(r, COL.ship).setValue(d.ship != null ? d.ship : '');
  sh.getRange(r, COL.fee).setValue(d.fee != null ? d.fee : (d.src === 'eBay' ? 0 : ''));
  sh.getRange(r, COL.status).setValue(d.status || 'Оплачено');
  if (d.id) sh.getRange(r, COL.id).setValue(d.id);
  if (d.note) sh.getRange(r, COL.note).setValue(d.note);
  writeFormulas_(sh, r);
  return r;
}

function words_(s) {
  return (String(s || '').toLowerCase().match(/[a-zа-яіїєäöüß0-9]{3,}/g) || [])
    .filter(function (w) { return ['mit', 'und', 'für', 'neu', 'ram', 'the', 'купив', 'продав'].indexOf(w) < 0; });
}

// Рядок того самого товару: за ID (відправлення/доставка) або за спільними словами назви (продаж власного лота)
function findRow_(id, title, openOnly) {
  const sh = ledger_().getSheetByName('Угоди');
  const n = Math.max(sh.getLastRow() - LEDGER_FIRST + 1, 0);
  if (!n) return 0;
  const data = sh.getRange(LEDGER_FIRST, 1, n, COL.id).getValues();
  if (id) for (let i = 0; i < n; i++) if (String(data[i][COL.id - 1]) === String(id)) return LEDGER_FIRST + i;
  const want = words_(title);
  let best = 0, bestScore = 0;
  for (let i = 0; i < n; i++) {
    const st = data[i][COL.status - 1];
    if (!data[i][COL.title - 1] || (openOnly && ['Продано', 'Повернено', 'Скасовано'].indexOf(st) >= 0)) continue;
    const have = words_(data[i][COL.title - 1]);
    const score = want.filter(function (w) { return have.indexOf(w) >= 0; }).length;
    if (score > bestScore) { best = LEDGER_FIRST + i; bestScore = score; }
  }
  return bestScore >= 2 || (bestScore === 1 && want.length === 1) ? best : 0;
}

function category_(t) {
  t = String(t || '');
  if (/ddr\s?[345]|\bram\b|so-?dimm|arbeitsspeicher|\bdimm\b/i.test(t)) return 'RAM';
  if (/ps\s?5|playstation|xbox|switch|konsole/i.test(t)) return 'Консоль';
  if (/\bpc\b|computer|rechner|optiplex|thinkcentre|prodesk/i.test(t)) return 'ПК';
  return 'Інше';
}

// ------------------------------------------------------------------ розбір листів
// Лише листи-угоди (у темі), а не сповіщення підписок KA — їх сотні на день
const LEDGER_QUERY = '(from:ebay OR from:kleinanzeigen) newer_than:45d subject:(bestellbestätigung OR bestellung OR gekauft ' +
  'OR verkauft OR versandt OR versendet OR verschickt OR unterwegs OR zugestellt OR geliefert OR storniert OR abgebrochen ' +
  'OR erstattet OR rückerstattung OR gewonnen OR bestätigt OR zahlung) -subject:"Suchauftrag" -subject:"neue Anzeigen"';
const KINDS = [   // порядок важливий: скасування раніше за «куплено»
  ['cancel', /storniert|abgebrochen|rückerstattung|erstattet|zurückerstattet|cancel+ed|refund/i],
  ['delivered', /zugestellt|geliefert|delivered/i],
  ['shipped', /versandt|verschickt|versendet|ist unterwegs|auf dem weg|shipped/i],
  ['sale', /(?:wurde|haben|hast|ist)\s+verkauft|artikel verkauft|wurde gekauft|hat .{0,60}gekauft|sold/i],
  ['purchase', /bestellbestätigung|bestellung .{0,20}bestätigt|(?:sie haben|du hast) .{0,80}(?:gekauft|gewonnen)|kauf bestätigt|zahlung (?:bestätigt|erfolgreich)|order confirmed|you (?:bought|won)/i]];

function money_(s) {
  const m = String(s).match(/([\d.]{1,7},\d{2})/);
  return m ? Number(m[1].replace(/\./g, '').replace(',', '.')) : null;
}

// Мітка — цілим словом («Summe» не в «Zwischensumme», «Versand» не в «Versandt»); сума — на тому ж або наступному рядку
// («Preis:\nEUR 64,44» у листі eBay 03.10), але не через іншу мітку («Gesamtbetrag:\nZwischensumme EUR 64,44» — це не разом).
// 03.10: «Summe» спіймало Zwischensumme 64,44 як «разом», ціна вийшла 64,44 − 6,19 = 58,25 замість 64,44.
function amountAfter_(body, labels) {
  const re = new RegExp('(?:^|[^a-zäöüß])(?:' + labels + ')(?![a-zäöüß])' +
                        '(?:(?!zwischensumme|versand|preis|summe|gebühr|gesamt)[^\\d]){0,40}?(?:EUR\\s*)?([\\d.]{1,7},\\d{2})', 'i');
  const m = body.match(re);
  return m ? money_(m[1]) : null;
}

function parseMail_(msg) {
  const subject = msg.getSubject() || '';
  const from = msg.getFrom() || '';
  const body = msg.getPlainBody() || '';
  const html = msg.getBody() || '';
  const src = /ebay/i.test(from) ? 'eBay' : /kleinanzeigen/i.test(from) ? 'Kleinanzeigen' : 'Інше';
  let kind = null;
  for (let i = 0; i < KINDS.length; i++) if (KINDS[i][1].test(subject)) { kind = KINDS[i][0]; break; }
  if (!kind) return { skip: true };   // реклама, повідомлення, «Preisvorschlag» тощо — не угода
  const idm = (html + ' ' + body).match(/\/itm\/(?:[^\/\s"'<>]*\/)?(\d{10,14})/) ||
              body.match(/(?:Artikelnummer|Artikel-?Nr\.?|Item number)[:\s#]*(\d{10,14})/i);
  const kam = (html + ' ' + body).match(/https?:\/\/(?:www\.)?kleinanzeigen\.de\/s-anzeige\/[^\s"'<>]+/);
  let title = (subject.match(/[„"«]([^“"»]{4,})[“"»]/) || [])[1] ||
              (subject.indexOf(':') >= 0 ? subject.slice(subject.indexOf(':') + 1)
               : subject.replace(/^.*?(?:bestätigung|bestätigt|verkauft|gekauft|versandt|versendet|zugestellt|storniert)\s+(?:für\s+)?/i, ''));
  title = title.replace(/\s+/g, ' ').trim().slice(0, 150);
  // eBay обрізає назву в темі («Corsair Vengeance LP...», 03.10 — без «DDR4» категорія вийшла «Інше», а «продати N» не
  // впізнав би товар): повна назва — рядок тексту листа, що починається так само
  const cut = title.match(/^(.{8,}?)\s*(?:\.\.\.|…)$/);
  if (cut) {
    const full = body.split('\n').map(function (s) { return s.replace(/\s+/g, ' ').trim(); })
      .filter(function (s) { return s.length > cut[1].length && s.indexOf(cut[1]) === 0; })[0];
    if (full) title = full.slice(0, 150);
  }
  const total = amountAfter_(body, 'Gesamtbetrag|Gesamtsumme|Bestellsumme|Gesamt|Summe|Total');
  const ship = amountAfter_(body, 'Verpackung und Versand|Versandkosten|Versand');
  let price = amountAfter_(body, 'Artikelpreis|Kaufpreis|Verkaufspreis|Sofort-Kaufen-Preis|Preis');
  const fee = amountAfter_(body, 'Käuferschutz|Servicegebühr|Gebühr');
  if (price == null && total != null) price = Math.round((total - (ship || 0) - (fee || 0)) * 100) / 100;
  return { kind: kind, src: src, title: title, id: idm ? idm[1] : '', link: idm ? 'https://www.ebay.de/itm/' + idm[1] : (kam ? kam[0] : ''),
           price: price, ship: ship, fee: fee, total: total, date: msg.getDate(), subject: subject, from: from,
           snippet: body.replace(/\s+/g, ' ').slice(0, 300) };
}

function processLedger() {
  const ss = ledger_();
  const log = ss.getSheetByName('Лог листів');
  const deals = ss.getSheetByName('Угоди');
  const done = {};
  if (log.getLastRow() > 1) log.getRange(2, 1, log.getLastRow() - 1, 1).getValues().forEach(function (r) { done[r[0]] = 1; });
  const msgs = [];
  // Перший прохід — 45 днів; далі лише розмови з листами за останні 2 доби (Gmail-ліміт Google, 01.10)
  const props = PropertiesService.getScriptProperties();
  const full = !props.getProperty('LEDGER_SCANNED');
  GmailApp.search(LEDGER_QUERY + (full ? '' : ' newer_than:2d'), 0, full ? 100 : 30).forEach(function (th) {
    th.getMessages().forEach(function (m) { if (!done[m.getId()]) msgs.push(m); });
  });
  msgs.sort(function (a, b) { return a.getDate() - b.getDate(); });   // спершу давніші: купівля раніше за доставку
  msgs.forEach(function (m) {
    const p = parseMail_(m);
    let what = 'пропущено (не угода)', row = '';
    if (!p.skip) {
      if (p.kind === 'purchase') {
        row = findRow_(p.id, null, false);
        if (!row && !p.id) {   // без номера — той самий товар за назвою, куплений протягом 3 днів (другий лист про те саме замовлення)
          const r2 = findRow_(null, p.title, true);
          const d2 = r2 ? deals.getRange(r2, COL.date).getValue() : null;
          if (d2 && Math.abs(p.date - new Date(d2)) < 3 * 86400000) row = r2;
        }
        if (row) { what = 'купівля вже є'; }
        else {
          row = addPurchase_({ date: p.date, title: p.title, src: p.src, link: p.link, price: p.price, ship: p.ship,
                               fee: p.fee, id: p.id, status: 'Оплачено' });
          what = 'купівля записана';
          notify_('📒 Записав покупку №' + (row - LEDGER_FIRST + 1) + ': ' + p.title + (p.price != null ? ' — ' + p.price + ' €' : '') +
                  '\nКоли отримаєш і перевіриш — «продати ' + (row - LEDGER_FIRST + 1) + '»' +
                  (p.price == null ? '\n⚠️ Суму не знайшов у листі — впиши в таблиці.' : '') +
                  (/ps\s?5|playstation\s?5/i.test(p.title) ? '\n💡 Цю PS5 можна спробувати продати на Amazon (вживане, тариф Einzelanbieter) — ' +
                   'тест з 03.10: там може бути +40–60 €. Напиши Claude «перевір PS5 на Amazon».' : ''));
        }
      } else if (p.kind === 'shipped' || p.kind === 'delivered' || p.kind === 'cancel') {
        row = findRow_(p.id, p.title, true);
        const st = p.kind === 'shipped' ? 'В дорозі' : p.kind === 'delivered' ? 'Отримано' : 'Скасовано';
        if (row) {
          const cur = deals.getRange(row, COL.status).getValue();
          if (cur !== 'Продано' && !(p.kind === 'shipped' && cur === 'Отримано')) {
            deals.getRange(row, COL.status).setValue(st);
            if (p.kind !== 'cancel') markEvent_(row - LEDGER_FIRST + 1, p.kind === 'shipped' ? 'shipped' : 'got', iso_(p.date));
            if (p.kind === 'delivered' && cur !== 'Отримано') gotNotice_(row);
          }
          what = 'статус → ' + st;
        } else { what = 'не знайшов рядок для «' + st + '»'; }
      } else if (p.kind === 'sale') {
        row = findRow_(null, p.title, true);
        if (row) {
          deals.getRange(row, COL.sdate).setValue(p.date);
          deals.getRange(row, COL.sto).setValue(p.src);
          if (p.total != null || p.price != null) deals.getRange(row, COL.sprice).setValue(p.total != null ? p.total : p.price);
          deals.getRange(row, COL.status).setValue('Продано');
          what = 'продаж записано';
          notify_('💰 Продано: ' + p.title + (p.total || p.price ? ' — ' + (p.total || p.price) + ' €' : '') +
                  '\nПрибуток дорахує таблиця.\n📮 Відправ у строк з лота (зазвичай 1–3 робочі дні) і завантаж трек-номер в eBay. ' +
                  'Відправив — «відправив ' + (row - LEDGER_FIRST + 1) + '».');
        } else { what = 'продаж: не знайшов, що це за товар — впиши вручну'; }
      }
    }
    log.appendRow([m.getId(), m.getDate(), p.from || m.getFrom(), m.getSubject(), what, row || '', p.snippet || '']);
  });
  if (full) props.setProperty('LEDGER_SCANNED', '1');
  if (!props.getProperty('EXPENSES_V1')) { expensesSheet_(ss); props.setProperty('EXPENSES_V1', '1'); }   // аркуш одразу видно
  weeklyIfDue_(props, new Date());
  remindersIfDue_(props, new Date());
}

// Щопонеділка з 9:00 за Берліном — один раз на тиждень (ключ — дата понеділка)
function weeklyIfDue_(props, now) {
  const day = Utilities.formatDate(now, 'Europe/Berlin', 'u'), hour = Number(Utilities.formatDate(now, 'Europe/Berlin', 'H'));
  const key = Utilities.formatDate(now, 'Europe/Berlin', 'yyyy-MM-dd');
  if (day !== '1' || hour < 9 || props.getProperty('WEEKLY_SENT') === key) return false;
  if (weeklyReport()) props.setProperty('WEEKLY_SENT', key);
  return true;
}

// ------------------------------------------------------------------ Telegram
// Облік і продаж — в окремому боті «Облік і продаж» (OFFICE_BOT_TOKEN), щоб не змішувати з картками покупок.
// Поки другого бота немає — пише в основний, як раніше.
const OFFICE_KEYBOARD = { keyboard: [[{ text: 'облік' }, { text: 'продати' }, { text: 'звіт' }, { text: 'допомога' }]], resize_keyboard: true,
  is_persistent: true };
const OFFICE_HELP = 'Тут облік і продаж (картки покупок — в основному боті).\n' +
  '• купив 45 OWC 2x16 DDR4 — записати покупку (додай ebay / самовивіз, якщо не KA)\n' +
  '• продав 110 OWC — записати продаж\n' +
  '• облік — підсумок і посилання на таблицю\n' +
  '• витрата 50 стенд для тесту RAM — витрата не на товар (обладнання, пакування, пересилка); прибуток після витрат — ' +
  'у «Підсумку»\n' +
  '• продати 3 — готове оголошення для eBay: ціна, пороги Preisvorschlag, назва й опис німецькою ' +
  '(3 — номер у таблиці; «продати» без номера — список того, що на руках)\n' +
  '• виставив 3 [124] — товар №3 уже на eBay (статус «Виставлено», ціну — в нотатки), щоб звіт не нагадував\n' +
  '• звіт — тижневий звіт: прибуток, точність прогнозів, залежаний товар, що дають підписки (сам приходить щопонеділка)\n' +
  '• отримав 3 / перевірив 3 / проблема 3 — посилка №3 прийшла / протестована / щось не так (покажу, як заявити)\n' +
  '• відправив 3 — проданий №3 відправлено покупцю\n' +
  '• нагадай 20.10 текст — нагадаю того дня; нагадування — що заплановано\n' +
  'Покупки й продажі з листів eBay/KA записуються самі — сюди прийде повідомлення. Щодня о 10:00 — що треба зробити ' +
  '(посилка не йде, не перевірено, не відправлено, заплановане).';

function officeToken_(props) {
  return props.getProperty('OFFICE_BOT_TOKEN') || props.getProperty('TELEGRAM_BOT_TOKEN');
}

function notify_(text, markup) {
  const props = PropertiesService.getScriptProperties();
  const tok = officeToken_(props), chat = props.getProperty('TELEGRAM_CHAT_ID');
  if (!tok || !chat) return null;
  const payload = { chat_id: chat, text: text, disable_web_page_preview: 'true' };
  if (markup) payload.reply_markup = JSON.stringify(markup);
  return UrlFetchApp.fetch('https://api.telegram.org/bot' + tok + '/sendMessage', {
    method: 'post', payload: payload, muteHttpExceptions: true });
}

/** Усе, що пишуть боту «Облік і продаж» (doPost у Code.gs з ?bot=office). Посилання тут не оцінюються. */
function officeMessage(msg) {
  const chat = PropertiesService.getScriptProperties().getProperty('TELEGRAM_CHAT_ID');
  if (!chat || String(msg.chat.id) !== String(chat)) return;   // чужий чат — мовчки
  if (sellCommand_(msg) || listedCommand_(msg) || statusCommand_(msg) || todoCommand_(msg) || ledgerCommand(msg)) return;
  if (/^\/?(звіт|report)(?=\s|$)/i.test(String(msg.text || '').trim())) {
    notify_(weeklyReport() ? '⏳ Готую звіт — приблизно хвилина.' : '⚠️ Не зміг запустити звіт (GitHub).');
    return;
  }
  if (/^\/?(допомога|help|start)/i.test(String(msg.text || '').trim())) { notify_(OFFICE_HELP, OFFICE_KEYBOARD); return; }
  notify_('Не зрозумів. ' + OFFICE_HELP + '\n\nОголошення для оцінки — кидай в основний бот.', OFFICE_KEYBOARD);
}

/**
 * Один раз після створення бота «Облік і продаж»: прив'язує його до цього ж вебзастосунку (адресу бере
 * з основного бота, нічого копіювати не треба) і надсилає вітання з кнопками.
 */
function connectOfficeBot() {
  const props = PropertiesService.getScriptProperties();
  const main = props.getProperty('TELEGRAM_BOT_TOKEN'), office = props.getProperty('OFFICE_BOT_TOKEN');
  if (!office) throw new Error('Додай у «Властивості скрипту» OFFICE_BOT_TOKEN — токен нового бота від BotFather.');
  if (office === main) throw new Error('OFFICE_BOT_TOKEN збігається з основним — потрібен токен НОВОГО бота.');
  const info = JSON.parse(UrlFetchApp.fetch('https://api.telegram.org/bot' + main + '/getWebhookInfo').getContentText());
  const base = String((info.result || {}).url || '').split('?')[0];
  if (!/^https:\/\/script\.google\.com\/macros\/s\/.+\/exec$/.test(base)) throw new Error('Основний бот не прив\'язаний до вебзастосунку: ' + base);
  const r = UrlFetchApp.fetch('https://api.telegram.org/bot' + office + '/setWebhook', {
    method: 'post', muteHttpExceptions: true,
    payload: { url: base + '?bot=office', allowed_updates: '["message"]', drop_pending_updates: 'true' } });
  console.log('webhook: ' + r.getContentText());
  const s = notify_('✅ Бот «Облік і продаж» підключено.\n\n' + OFFICE_HELP, OFFICE_KEYBOARD);
  if (s && s.getResponseCode() === 403) console.log('Бот не може написати першим: відкрий його в Telegram і натисни «Start», потім запусти ще раз.');
  else console.log('готово — перевір Telegram');
}

/**
 * Команди з Telegram (викликає doPost у Code.gs). true — команду оброблено, в GitHub не пересилати.
 *   купив 45 OWC 2x16 DDR4 [ebay|ka|самовивіз]   продав 110 OWC [ebay|ka]   облік
 */
function ledgerCommand(msg) {
  const props = PropertiesService.getScriptProperties();
  const chat = props.getProperty('TELEGRAM_CHAT_ID');
  const text = String(msg.text || '').trim();
  // \b у JS не працює з кирилицею — межа слова через (?=\s|$)
  const m = text.match(/^\/?(купив|купила|продав|продала|облік|витрата|витратив|витратила)(?=\s|$)\s*([\s\S]*)$/i);
  if (!m) return false;
  if (!chat || String(msg.chat.id) !== String(chat) || !props.getProperty('LEDGER_ID')) return true;   // чужий чат — мовчки
  const cmd = m[1].toLowerCase(), rest = m[2] || '';
  if (cmd === 'облік') {
    const s = ledger_().getSheetByName('Підсумок');
    const v = s.getRange('B3:B11').getValues().map(function (r) { return r[0]; });
    const exp = expenseTotal_();
    notify_('📒 Облік: угод ' + v[0] + ', продано ' + v[1] + ', на руках ' + v[2] + '\nУ товарі: ' + Number(v[4]).toFixed(2) +
            ' €\nПрибуток з проданого: ' + Number(v[6]).toFixed(2) + ' €' +
            (exp ? '\nВитрати (обладнання, пакування…): ' + exp.toFixed(2) + ' € → прибуток після витрат: ' +
             (Number(v[6]) - exp).toFixed(2) + ' €' : '') + '\n' + ledger_().getUrl());
    return true;
  }
  // сума — першим словом («купив 45 OWC 2x16») або після «за» («купив OWC 2x16 за 45»), а не «2» з «2x16»
  const pm = rest.match(/^(\d+(?:[.,]\d{1,2})?)\s*(?:€|eur\w*|євро)?(?=\s|$)/i) ||
             rest.match(/(?:^|\s)(?:за|for|für)\s+(\d+(?:[.,]\d{1,2})?)\s*(?:€|eur\w*|євро)?(?=\s|$)/i);
  if (!pm) { notify_('Напиши так: «' + cmd + ' 45 OWC 2x16 DDR4» — спершу сума, далі назва.'); return true; }
  const amount = Number(pm[1].replace(',', '.'));
  let title = (rest.slice(0, pm.index) + ' ' + rest.slice(pm.index + pm[0].length)).replace(/\s+/g, ' ').trim();
  const src = /\bebay\b/i.test(title) ? 'eBay' : /самовивіз|abhol/i.test(title) ? 'Самовивіз Гамбург' : 'Kleinanzeigen';
  title = title.replace(/(^|\s)(ebay|ka|kleinanzeigen|самовивіз)(?=\s|$)/ig, ' ').replace(/\s+/g, ' ').trim();
  if (cmd.indexOf('витрат') === 0) {
    const what = (rest.slice(0, pm.index) + ' ' + rest.slice(pm.index + pm[0].length)).replace(/\s+/g, ' ').trim() || '(без назви)';
    const cat = expenseCategory_(what);
    addExpense_({ title: what, amount: amount, cat: cat });
    notify_('🧾 Записав витрату: ' + what + ' — ' + amount + ' € (' + cat + ').\nУсього витрат: ' + expenseTotal_().toFixed(2) +
            ' € — у «Підсумку» прибуток після витрат.');
    return true;
  }
  if (cmd.indexOf('купи') === 0) {
    const r = addPurchase_({ title: title || '(без назви)', src: src, price: amount, fee: src === 'Kleinanzeigen' ? '' : 0,
                             status: src === 'Самовивіз Гамбург' ? 'Отримано' : 'Оплачено', note: 'з Telegram' });
    notify_('📒 Записав покупку в рядок ' + (r - 4) + ': ' + title + ' — ' + amount + ' €' +
            (src === 'Kleinanzeigen' ? '\nПересилку й збір Sicher bezahlen допиши в таблиці, якщо були.' : ''));
  } else {
    const row = findRow_(null, title, true);
    if (!row) { notify_('Не знайшов у обліку непроданий товар зі словами «' + title + '». Уточни назву або впиши вручну.'); return true; }
    const sh = ledger_().getSheetByName('Угоди');
    sh.getRange(row, COL.sdate).setValue(new Date());
    sh.getRange(row, COL.sto).setValue(src);
    sh.getRange(row, COL.sprice).setValue(amount);
    sh.getRange(row, COL.status).setValue('Продано');
    SpreadsheetApp.flush();
    const profit = sh.getRange(row, COL.profit).getValue();
    notify_('💰 Записав продаж: ' + sh.getRange(row, COL.title).getValue() + ' — ' + amount + ' €' +
            (profit !== '' ? '\nПрибуток ≈ ' + Number(profit).toFixed(2) + ' €' : ''));
  }
  return true;
}


// ------------------------------------------------------------------ «продати N» (01.10)
// Ціну купівлі передаємо в GitHub зашифрованою: репозиторій публічний, а параметри запуску видно в журналі.
// Ключ — токен бота «Облік і продаж» (той самий є в секретах GitHub як OFFICE_BOT_TOKEN). Пара — research/sell.py.
function hmacHex_(key, msg) {
  return Utilities.computeHmacSha256Signature(msg, key, Utilities.Charset.UTF_8)
    .map(function (b) { return ('0' + (b & 255).toString(16)).slice(-2); }).join('');
}

function seal_(data, key, nonce) {
  const raw = Utilities.newBlob(JSON.stringify(data)).getBytes();
  let ks = [];
  for (let i = 0; ks.length < raw.length; i++) ks = ks.concat(Utilities.computeHmacSha256Signature(nonce + ':' + i, key, Utilities.Charset.UTF_8));
  const blob = Utilities.base64Encode(raw.map(function (b, i) { return b ^ ks[i]; }));
  return { blob: blob, mac: hmacHex_(key, 'mac:' + nonce + ':' + blob).slice(0, 32), nonce: nonce };
}

function sellCommand_(msg) {
  const m = String(msg.text || '').trim().match(/^\/?(?:продати|продаж)(?=\s|$)\s*(\d{1,4})?(?=\s|$)\s*([\s\S]*)$/i);
  if (!m) return false;
  const props = PropertiesService.getScriptProperties();
  if (String(msg.chat.id) !== String(props.getProperty('TELEGRAM_CHAT_ID')) || !props.getProperty('LEDGER_ID')) return true;
  const sh = ledger_().getSheetByName('Угоди');
  const last = sh.getLastRow();
  const rows = last >= LEDGER_FIRST ? sh.getRange(LEDGER_FIRST, 1, last - LEDGER_FIRST + 1, COL.id).getValues() : [];
  const open = function (v) { return v[COL.title - 1] && ['Продано', 'Повернено', 'Скасовано'].indexOf(v[COL.status - 1]) < 0; };
  let idx = -1;
  const extra = (m[2] || '').trim();
  if (m[1]) idx = Number(m[1]) - 1;
  else if (extra) idx = findRow_(null, extra, true) - LEDGER_FIRST;
  if (!m[1] && !extra) {
    const list = rows.map(function (v, i) { return open(v) ? '№' + (i + 1) + ' ' + v[COL.title - 1] + ' — ' + v[COL.status - 1] : ''; })
      .filter(Boolean);
    notify_(list.length ? 'Що продаємо? Напиши «продати N»:\n' + list.slice(-15).join('\n') : 'На руках нічого немає — усе продано.');
    return true;
  }
  if (idx < 0 || idx >= rows.length || !rows[idx][COL.title - 1]) {
    notify_(m[1] ? 'У таблиці немає №' + m[1] + '. Напиши «продати» — покажу список.' : 'Не знайшов «' + extra + '» серед непроданого.');
    return true;
  }
  const v = rows[idx];
  if (!open(v)) { notify_('№' + (idx + 1) + ' «' + v[COL.title - 1] + '» — статус «' + v[COL.status - 1] + '», не продаю.'); return true; }
  const title = m[1] && extra ? extra : String(v[COL.title - 1]);
  const cost = Number(v[COL.spent - 1]) || Number(v[COL.price - 1]) || null;
  const key = officeToken_(props);
  const sealed = seal_({ row: idx + 1, title: title, cost: cost }, key, Utilities.getUuid().replace(/-/g, ''));
  const token = props.getProperty('GITHUB_TOKEN') || GITHUB_TOKEN;
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/sell.yml/dispatches', {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ ref: 'main', inputs: sealed }) });
  notify_(r.getResponseCode() === 204
    ? '⏳ Готую оголошення для №' + (idx + 1) + ' «' + title + '» — приблизно хвилина.'
    : '⚠️ Не зміг запустити підготовку оголошення (GitHub ' + r.getResponseCode() + ').');
  return true;
}


// ------------------------------------------------------------------ тижневий звіт (01.10)
// Рядки обліку → зашифровано (як «продати») → GitHub weekly_report.yml → research/weekly_report.py → бот «Облік і продаж».
function iso_(v) { return v instanceof Date ? Utilities.formatDate(v, 'Europe/Berlin', 'yyyy-MM-dd') : (v || ''); }
function num_(v) { return v === '' || v == null || isNaN(Number(v)) ? null : Math.round(Number(v) * 100) / 100; }

function ledgerRows_() {
  const sh = ledger_().getSheetByName('Угоди');
  const last = sh.getLastRow();
  if (last < LEDGER_FIRST) return [];
  return sh.getRange(LEDGER_FIRST, 1, last - LEDGER_FIRST + 1, COL.id).getValues()
    .map(function (v, i) {
      return { n: i + 1, date: iso_(v[COL.date - 1]), title: String(v[COL.title - 1]).slice(0, 90), cat: v[COL.cat - 1],
        src: v[COL.src - 1], spent: num_(v[COL.spent - 1]) != null ? num_(v[COL.spent - 1]) : num_(v[COL.price - 1]),
        status: v[COL.status - 1], sdate: iso_(v[COL.sdate - 1]), sprice: num_(v[COL.sprice - 1]), net: num_(v[COL.net - 1]),
        profit: num_(v[COL.profit - 1]) };
    })
    .filter(function (r) { return r.title; });
}

function weeklyReport() {
  const props = PropertiesService.getScriptProperties();
  if (!props.getProperty('LEDGER_ID')) return false;
  let rows = ledgerRows_();
  // вхідні параметри GitHub ≤ 65 000 символів: усі непродані + останні продажі
  while (rows.length > 20 && JSON.stringify(rows).length > 40000) {
    const i = rows.findIndex(function (r) { return ['Продано', 'Повернено', 'Скасовано'].indexOf(r.status) >= 0; });
    if (i < 0) break;
    rows.splice(i, 1);
  }
  const sealed = seal_({ rows: rows, today: iso_(new Date()) }, officeToken_(props), Utilities.getUuid().replace(/-/g, ''));
  const token = props.getProperty('GITHUB_TOKEN') || GITHUB_TOKEN;
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/weekly_report.yml/dispatches', {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ ref: 'main', inputs: sealed }) });
  console.log('тижневий звіт: GitHub ' + r.getResponseCode());
  return r.getResponseCode() === 204;
}

// «виставив 3 124» → статус «Виставлено» (+ ціна в нотатки) — для тижневого звіту: що на продажу, а що лежить
function listedCommand_(msg) {
  const m = String(msg.text || '').trim().match(/^\/?(?:виставив|виставила)(?=\s|$)\s*(\d{1,4})?(?:\s+(\d+(?:[.,]\d{1,2})?))?/i);
  if (!m) return false;
  const props = PropertiesService.getScriptProperties();
  if (String(msg.chat.id) !== String(props.getProperty('TELEGRAM_CHAT_ID')) || !props.getProperty('LEDGER_ID')) return true;
  if (!m[1]) { notify_('Напиши так: «виставив 3 124» — номер у таблиці і ціна (ціну можна не писати).'); return true; }
  const sh = ledger_().getSheetByName('Угоди');
  const row = Number(m[1]) + LEDGER_FIRST - 1;
  const title = row <= sh.getLastRow() ? sh.getRange(row, COL.title).getValue() : '';
  if (!title) { notify_('У таблиці немає №' + m[1] + '.'); return true; }
  const st = sh.getRange(row, COL.status).getValue();
  if (['Продано', 'Повернено', 'Скасовано'].indexOf(st) >= 0) { notify_('№' + m[1] + ' — статус «' + st + '», не змінюю.'); return true; }
  sh.getRange(row, COL.status).setValue('Виставлено');
  if (m[2]) {
    const note = String(sh.getRange(row, COL.note).getValue() || '');
    sh.getRange(row, COL.note).setValue((note ? note + '; ' : '') + 'виставлено за ' + m[2].replace(',', '.') + ' € ' +
      Utilities.formatDate(new Date(), 'Europe/Berlin', 'dd.MM'));
  }
  notify_('🏷 №' + m[1] + ' «' + title + '» — виставлено' + (m[2] ? ' за ' + m[2] + ' €' : '') + '. Коли продаси — «продав ' +
          (m[2] || 'ціна') + ' ' + String(title).split(' ').slice(0, 2).join(' ') + '» (або лист eBay запише сам).');
  return true;
}


// ------------------------------------------------------------------ захист покупок і нагадування (04.10)
// Посилка не йде → вчасно відкрити запит; прийшла → протестувати ДО підтвердження отримання (KA) і відгуку; продано →
// відправити; плюс заплановані справи. Строки: KA «Sicher bezahlen» — проблему можна заявити лише 10 днів від відправки,
// через 14 днів гроші самі йдуть продавцю; eBay — запит «Artikel nicht erhalten» / «nicht wie beschrieben» до 30 днів
// після останньої очікуваної дати доставки. Дати подій і що вже нагадано — у властивості LEDGER_EVENTS (рядок → {...}).
const LEDGER_TODO = [   // [дата, текст, повторювати кожні N днів]
  ['2026-10-15', 'HDD: повторний замір цін (8 ТБ росли +15–18% за тиждень на 03.10). Напиши Claude: «заміряй HDD».'],
  ['2026-10-17', 'ПК-бот: два тижні з 03.10 — перевірити живі картки (розбір, ціни деталей). Напиши Claude: «перевір ПК-бот».'],
  ['2026-10-25', 'Звірити базові ціни з реальними продажами (замір Terapeak 23–26.09 старіє, сторож бачить лише оголошення). ' +
    'Увійди в eBay у вбудованому браузері й напиши Claude: «звір ціни з продажами».', 30],
  ['2026-11-15', 'Thule-кріплення: сезонний замір (листопад–грудень). Напиши Claude: «заміряй Thule».']];
const SALES_UP = 8;   // продажів за 30 днів → час для варіанта А (бот публікує оголошення) і податкових порогів

function days_(from, to) { return Math.round((Date.parse(to) - Date.parse(from)) / 86400000); }
function addDays_(iso, n) {
  const d = new Date(Date.parse(iso) + n * 86400000);
  return ('0' + d.getUTCDate()).slice(-2) + '.' + ('0' + (d.getUTCMonth() + 1)).slice(-2);
}
function events_() { return JSON.parse(PropertiesService.getScriptProperties().getProperty('LEDGER_EVENTS') || '{}'); }
function saveEvents_(ev) { PropertiesService.getScriptProperties().setProperty('LEDGER_EVENTS', JSON.stringify(ev)); }
function markEvent_(n, key, val) {
  const ev = events_();
  ev[n] = ev[n] || {};
  if (key === 'rem') { ev[n].rem = ev[n].rem || {}; ev[n].rem[val] = 1; } else ev[n][key] = val;
  saveEvents_(ev);
}

function testTip_(cat) {
  return {
    'RAM': 'MemTest86 з флешки або TestMem5/OCCT у Windows — хоча б 1 повний прохід; наклейки = назва (обсяг, DDR, U/S, ' +
      'без R/E); у BIOS або CPU-Z — повний обсяг і частота',
    'Консоль': 'увімкни, онови систему, перевір диск (якщо є), контролер, вентилятор і шум; серійник на коробці = на консолі; ' +
      'акаунт продавця видалено',
    'ПК': 'стрес-тест 15 хв (OCCT або FurMark), температури, усі порти; збіг деталей з описом (CPU-Z, GPU-Z)'
  }[cat] || 'перевір, що все працює і відповідає опису (фото, модель, комплект)';
}

// Строк, до якого ще можна заявити проблему: KA — 10 днів від відправки (дата відправки невідома — від покупки, обережніше)
function claimLine_(r, e) {
  if (r.src === 'Kleinanzeigen') {
    return 'Проблему в «Sicher bezahlen» можна заявити лише до ' + addDays_(e.shipped || r.date, 10) +
      ' (10 днів від ' + (e.shipped ? 'відправки' : 'покупки') + '). Не підтверджуй отримання, поки не перевірив — після ' +
      'підтвердження гроші одразу йдуть продавцю.';
  }
  return 'Якщо не так — запит «nicht wie beschrieben» у Mein eBay → Käufe (до 30 днів після доставки). Відгук — лише після перевірки.';
}

function rowByN_(n) { return ledgerRows_().filter(function (r) { return r.n === n; })[0] || null; }

function gotNotice_(row) {
  const r = rowByN_(row - LEDGER_FIRST + 1);
  if (!r || r.src === 'Самовивіз Гамбург') return;
  const e = events_()[r.n] || {};
  notify_('📦 №' + r.n + ' «' + r.title + '» отримано. Протестуй протягом 1–2 днів: ' + testTip_(r.cat) + '.\n' + claimLine_(r, e) +
          '\nПеревірив — «перевірив ' + r.n + '», щось не так — «проблема ' + r.n + '».');
  markEvent_(r.n, 'rem', 'test0');
}

/** Чисті правила: рядки обліку + події → {lines, marks: [[n, ключ]], got: [n, ...] (без дати отримання — ставимо сьогодні)}. */
function dueReminders_(rows, ev, today) {
  const lines = [], marks = [], got = [];
  const once = function (n, key, text) {
    if (((ev[n] || {}).rem || {})[key]) return false;
    lines.push(text); marks.push([n, key]); return true;
  };
  rows.forEach(function (r) {
    const e = ev[r.n] || {}, rem = e.rem || {}, name = '№' + r.n + ' «' + String(r.title).slice(0, 60) + '»';
    if (!r.date || r.src === 'Самовивіз Гамбург') return;
    if (r.status === 'Оплачено' || r.status === 'В дорозі') {
      const base = e.shipped || r.date, d = days_(base, today), from = e.shipped ? 'від відправки' : 'від покупки';
      const tail = ' Уже отримав — «отримав ' + r.n + '».';
      if (r.src === 'Kleinanzeigen') {
        if (d >= 8) once(r.n, 'ka8', '⚠️ ' + name + ' — досі не отримано (' + d + ' дн. ' + from + '). Проблему в «Sicher bezahlen» ' +
          'можна заявити лише до ' + addDays_(base, 10) + ', а на 14-й день гроші самі підуть продавцю. Посилки немає — «Problem melden» ' +
          'у KA зараз.' + tail);
        else if (d >= 5 && !rem.ka8) once(r.n, 'ka5', '📦 ' + name + ' — ' + d + ' дн. ' + from + ', посилки ще немає. Глянь трек; ' +
          'якщо не відправлено або трек стоїть — напиши продавцю (строк для проблеми — до ' + addDays_(base, 10) + ').' + tail);
      } else {
        if (d >= 20) once(r.n, 'eb20', '⚠️ ' + name + ' — ' + d + ' дн. без доставки. Відкрий «Artikel nicht erhalten» у Mein eBay → ' +
          'Käufe (строк — 30 днів після останньої очікуваної дати доставки, далі захист пропадає).' + tail);
        else if (d >= 10 && !rem.eb20) once(r.n, 'eb10', '📦 ' + name + ' — ' + d + ' дн. ' + from + ', не доставлено. Глянь трек; ' +
          'стоїть — напиши продавцю, після очікуваної дати доставки можна відкрити «Artikel nicht erhalten».' + tail);
      }
    } else if (r.status === 'Отримано') {
      if (!e.got) got.push(r.n);
      const d = e.got ? days_(e.got, today) : 0, act = ' Перевірив — «перевірив ' + r.n + '», не так — «проблема ' + r.n + '».';
      if (!rem.test0) once(r.n, 'test0', '🧪 ' + name + ' отримано — протестуй: ' + testTip_(r.cat) + '. ' + claimLine_(r, e) + act);
      else if (d >= 2) once(r.n, 'test2', '🧪 ' + name + ' отримано ' + d + ' дн. тому, ще не перевірено. ' + claimLine_(r, e) + act);
    } else if (r.status === 'Продано' && r.sdate && !e.sent) {
      const d = days_(r.sdate, today), act = ' Відправив — «відправив ' + r.n + '».';
      if (d < 1 || d > 7) return;
      if (d >= 2 && rem.ship1) once(r.n, 'ship2', '📮 ' + name + ' продано ' + d + ' дн. тому — досі не відправлено? Запізнення = погана ' +
        'оцінка і ризик скасування.' + act);
      else if (!rem.ship1) once(r.n, 'ship1', '📮 ' + name + ' продано — відправ і завантаж трек-номер в eBay.' + act);
    }
  });
  const sold30 = rows.filter(function (r) { return r.status === 'Продано' && r.sdate && days_(r.sdate, today) <= 30; }).length;
  if (sold30 >= SALES_UP) once('_', 'salesup', '📈 Продажі пішли: ' + sold30 + ' за 30 днів. Час для двох відкладених речей — бот сам ' +
    'публікує оголошення eBay (варіант А) і податкові пороги (DAC7: 30 продажів або 2 000 € на рік). Напиши Claude.');
  return { lines: lines, marks: marks, got: got };
}

/** Заплановані справи (LEDGER_TODO + «нагадай») на сьогодні → {lines, marks}. Повторювані — раз на кожен період. */
function dueTodos_(todos, ev, today) {
  const lines = [], marks = [], done = (ev._ || {}).rem || {};
  todos.forEach(function (t) {
    const d = days_(t[0], today);
    if (d < 0 || (!t[2] && d > 14)) return;   // давнє одноразове (напр., після перерви) — не засипаємо
    const key = 'todo:' + t[0] + ':' + String(t[1]).slice(0, 24) + (t[2] ? ':' + Math.floor(d / t[2]) : '');
    if (!done[key]) { lines.push('📅 ' + t[1]); marks.push(['_', key]); }
  });
  return { lines: lines, marks: marks };
}

function userTodos_() { return JSON.parse(PropertiesService.getScriptProperties().getProperty('USER_TODO') || '[]'); }

// Щодня з 10:00 за Берліном — одне повідомлення «На сьогодні», лише якщо є що робити
function remindersIfDue_(props, now) {
  const key = Utilities.formatDate(now, 'Europe/Berlin', 'yyyy-MM-dd');
  if (Number(Utilities.formatDate(now, 'Europe/Berlin', 'H')) < 10 || props.getProperty('REMIND_SENT') === key) return false;
  props.setProperty('REMIND_SENT', key);   // спершу ставимо — помилка нижче не засипле повторами кожні 15 хв
  const rows = ledgerRows_(), ev = events_();
  const a = dueReminders_(rows, ev, key), b = dueTodos_(LEDGER_TODO.concat(userTodos_()), ev, key);
  a.got.forEach(function (n) { ev[n] = ev[n] || {}; ev[n].got = key; });
  a.marks.concat(b.marks).forEach(function (m) {
    ev[m[0]] = ev[m[0]] || {}; ev[m[0]].rem = ev[m[0]].rem || {}; ev[m[0]].rem[m[1]] = 1;
  });
  // закриті рядки більше не потрібні (ліміт властивості 9 КБ)
  rows.forEach(function (r) {
    if (ev[r.n] && (['Повернено', 'Скасовано'].indexOf(r.status) >= 0 ||
        (r.status === 'Продано' && (ev[r.n].sent || (r.sdate && days_(r.sdate, key) > 7))))) delete ev[r.n];
  });
  saveEvents_(ev);
  const lines = a.lines.concat(b.lines);
  if (lines.length) notify_('📋 На сьогодні:\n\n' + lines.join('\n\n'));
  return lines.length > 0;
}

// «отримав 3» / «перевірив 3» / «проблема 3» / «відправив 3»
function statusCommand_(msg) {
  const m = String(msg.text || '').trim().match(/^\/?(отримав|отримала|перевірив|перевірила|проблема|відправив|відправила)(?=\s|$)\s*(\d{1,4})?/i);
  if (!m) return false;
  const props = PropertiesService.getScriptProperties();
  if (String(msg.chat.id) !== String(props.getProperty('TELEGRAM_CHAT_ID')) || !props.getProperty('LEDGER_ID')) return true;
  const cmd = m[1].toLowerCase().replace(/ла$/, 'в');
  if (!m[2]) { notify_('Напиши з номером у таблиці: «' + cmd + ' 3».'); return true; }
  const n = Number(m[2]), r = rowByN_(n);
  if (!r) { notify_('У таблиці немає №' + n + '.'); return true; }
  const sh = ledger_().getSheetByName('Угоди'), row = n + LEDGER_FIRST - 1, today = iso_(new Date());
  const e = events_()[n] || {}, name = '№' + n + ' «' + r.title + '»';
  if (cmd === 'відправив') {
    if (r.status !== 'Продано') { notify_(name + ' — статус «' + r.status + '», а «відправив» — для проданого.'); return true; }
    markEvent_(n, 'sent', today);
    notify_('📮 ' + name + ' — відправлено, більше не нагадую.');
    return true;
  }
  if (['Продано', 'Повернено', 'Скасовано'].indexOf(r.status) >= 0) { notify_(name + ' — статус «' + r.status + '», не змінюю.'); return true; }
  if (cmd === 'отримав') {
    sh.getRange(row, COL.status).setValue('Отримано');
    markEvent_(n, 'got', today);
    if (r.src === 'Самовивіз Гамбург') notify_('📦 ' + name + ' — отримано.');
    else { gotNotice_(row); }
  } else if (cmd === 'перевірив') {
    sh.getRange(row, COL.status).setValue('Перевірено');
    markEvent_(n, 'tested', today);
    notify_('✅ ' + name + ' перевірено. ' + (r.src === 'Kleinanzeigen' ? 'Тепер можна підтвердити отримання в KA. ' : 'Можна залишити відгук. ') +
            'Продати — «продати ' + n + '».');
  } else {
    sh.getRange(row, COL.status).setValue('Проблема');
    notify_('🚩 ' + name + ' — статус «Проблема». Що робити:\n' + (r.src === 'Kleinanzeigen'
      ? '• Не підтверджуй отримання. У KA → «Sicher bezahlen» → замовлення → «Problem melden» — до ' + addDays_(e.shipped || r.date, 10) +
        ' (10 днів від ' + (e.shipped ? 'відправки' : 'покупки') + '); фото/відео дефекту, номер треку.\n• Паралельно напиши продавцю — ' +
        'часто погоджуються на повернення.'
      : '• Mein eBay → Käufe → цей товар → «Artikel zurückgeben» / «Problem melden» → «nicht wie beschrieben» (до 30 днів ' +
        'після доставки); фото/відео дефекту. Продавець має 3 робочі дні, далі можна попросити eBay втрутитися.\n' +
        '• Відгук — лише після вирішення.') + '\nЦе не юридична порада — лише як працює захист покупця.');
  }
  return true;
}

// «нагадай 20.10 текст» → нагадування того дня о 10:00; «нагадування» — список запланованого
function todoCommand_(msg) {
  const text = String(msg.text || '').trim();
  const list = /^\/?нагадування(?=\s|$)/i.test(text);
  const m = text.match(/^\/?нагадай(?=\s|$)\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s+([\s\S]{2,300})$/i);
  if (!list && !/^\/?нагадай(?=\s|$)/i.test(text)) return false;
  const props = PropertiesService.getScriptProperties();
  if (String(msg.chat.id) !== String(props.getProperty('TELEGRAM_CHAT_ID'))) return true;
  const today = iso_(new Date());
  if (list) {
    const ev = events_(), done = (ev._ || {}).rem || {};
    const items = LEDGER_TODO.concat(userTodos_()).filter(function (t) { return t[2] || days_(t[0], today) <= 0; })
      .map(function (t) {
        const next = t[2] && days_(t[0], today) > 0 ? iso_(new Date(Date.parse(t[0]) + Math.ceil(days_(t[0], today) / t[2]) * t[2] * 86400000)) : t[0];
        return [next, t[1] + (t[2] ? ' (кожні ' + t[2] + ' дн.)' : '')];
      })
      .sort(function (a, b) { return a[0] < b[0] ? -1 : 1; });
    notify_(items.length ? '📅 Заплановано:\n' + items.map(function (t) { return '• ' + addDays_(t[0], 0) + ' — ' + t[1]; }).join('\n')
      : 'Нічого не заплановано. Додай: «нагадай 20.10 текст».');
    return true;
  }
  if (!m) { notify_('Напиши так: «нагадай 20.10 забрати посилку» — дата і текст.'); return true; }
  let y = m[3] ? Number(m[3].length === 2 ? '20' + m[3] : m[3]) : Number(today.slice(0, 4));
  let iso = y + '-' + ('0' + m[2]).slice(-2) + '-' + ('0' + m[1]).slice(-2);
  if (isNaN(Date.parse(iso)) || Number(m[2]) > 12 || Number(m[1]) > 31) { notify_('Не зрозумів дату «' + m[1] + '.' + m[2] + '».'); return true; }
  if (!m[3] && iso < today) iso = (y + 1) + iso.slice(4);
  const todos = userTodos_().filter(function (t) { return days_(t[0], today) > -15; });   // давні прибираємо
  todos.push([iso, m[4].trim()]);
  props.setProperty('USER_TODO', JSON.stringify(todos.slice(-40)));
  notify_('📅 Нагадаю ' + addDays_(iso, 0) + (iso.slice(0, 4) !== today.slice(0, 4) ? '.' + iso.slice(0, 4) : '') + ' о 10:00: ' + m[4].trim());
  return true;
}


// ------------------------------------------------------------------ витрати (04.10)
// Обладнання (стенд для тесту пам'яті), пакування, пересилка — не товар: окремий аркуш «Витрати», а в «Підсумку» —
// «Витрати» і «Прибуток після витрат». Команда в будь-якому з ботів: «витрата 50 стенд MSI для тесту RAM».
const EXP_SHEET = 'Витрати', EXP_FIRST = 3, EXP_LABEL = 'Витрати (обладнання, пакування, пересилка), €';

function expenseCategory_(t) {
  t = String(t || '');
  if (/пакуван|коробк|плівк|скотч|пупир|karton|luftpolster|verpack/i.test(t)) return 'Пакування';
  if (/пошт|пересил|dhl|hermes|dpd|gls|porto|марк/i.test(t)) return 'Пересилка';
  return 'Обладнання';
}

/** Аркуш «Витрати» (створює, якщо немає) + два рядки в «Підсумку» під основними цифрами. */
function expensesSheet_(ss) {
  let sh = ss.getSheetByName(EXP_SHEET);
  if (sh) return sh;
  sh = ss.insertSheet(EXP_SHEET);
  sh.getRange('A1').setValue('Витрати — обладнання, пакування, пересилка (не товар)').setFontSize(14).setFontWeight('bold');
  sh.getRange(2, 1, 1, 5).setValues([['Дата', 'Що', 'Сума, €', 'Категорія', 'Нотатки']]).setFontWeight('bold')
    .setFontColor('#FFFFFF').setBackground('#1F3864');
  [90, 320, 90, 110, 260].forEach(function (w, i) { sh.setColumnWidth(i + 1, w); });
  sh.setFrozenRows(2);
  sh.getRange('A3:A1000').setNumberFormat('dd.mm.yyyy');
  sh.getRange('C3:C1000').setNumberFormat(EUR_FMT);
  const sum = ss.getSheetByName('Підсумок');
  if (sum) {
    // одразу під «Середньо днів до продажу» (рядок 11); зайнято (таблицю правили вручну) — нижче за все
    const r = sum.getRange(12, 1).getValue() === '' && sum.getRange(13, 1).getValue() === '' ? 12 : sum.getLastRow() + 2;
    sum.getRange(r, 1).setValue(EXP_LABEL);
    sum.getRange(r, 2).setFormula("=SUM('" + EXP_SHEET + "'!C" + EXP_FIRST + ':C)').setNumberFormat(EUR_FMT)
      .setFontWeight('bold').setBackground('#F2F2F2');
    sum.getRange(r + 1, 1).setValue('Прибуток після витрат, €');
    sum.getRange(r + 1, 2).setFormula('=B9-B' + r).setNumberFormat(EUR_FMT).setFontWeight('bold').setBackground('#F2F2F2');
  }
  return sh;
}

function addExpense_(d) {
  const sh = expensesSheet_(ledger_());
  const n = Math.max(sh.getLastRow() - EXP_FIRST + 1, 1);
  const vals = sh.getRange(EXP_FIRST, 2, n, 1).getValues();
  let r = EXP_FIRST;
  for (let i = vals.length - 1; i >= 0; i--) if (vals[i][0] !== '') { r = EXP_FIRST + i + 1; break; }
  sh.getRange(r, 1, 1, 5).setValues([[d.date || new Date(), d.title, d.amount, d.cat || expenseCategory_(d.title), d.note || '']]);
  return r;
}

function expenseTotal_() {
  const sh = ledger_().getSheetByName(EXP_SHEET);
  if (!sh || sh.getLastRow() < EXP_FIRST) return 0;
  return Math.round(sh.getRange(EXP_FIRST, 3, sh.getLastRow() - EXP_FIRST + 1, 1).getValues()
    .reduce(function (a, r) { return a + (Number(r[0]) || 0); }, 0) * 100) / 100;
}
