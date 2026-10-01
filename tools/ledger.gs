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

const VER_LEDGER = '2026-10-02a';   // версія файлу: сторож порівнює з GitHub і нагадує оновити (при зміні файлу — підняти)
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

function amountAfter_(body, labels) {
  const re = new RegExp('(?:' + labels + ')[^\\n\\d]{0,40}?(?:EUR\\s*)?([\\d.]{1,7},\\d{2})', 'i');
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
                  (p.price == null ? '\n⚠️ Суму не знайшов у листі — впиши в таблиці.' : ''));
        }
      } else if (p.kind === 'shipped' || p.kind === 'delivered' || p.kind === 'cancel') {
        row = findRow_(p.id, p.title, true);
        const st = p.kind === 'shipped' ? 'В дорозі' : p.kind === 'delivered' ? 'Отримано' : 'Скасовано';
        if (row) {
          const cur = deals.getRange(row, COL.status).getValue();
          if (cur !== 'Продано' && !(p.kind === 'shipped' && cur === 'Отримано')) deals.getRange(row, COL.status).setValue(st);
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
                  '\nПрибуток дорахує таблиця.');
        } else { what = 'продаж: не знайшов, що це за товар — впиши вручну'; }
      }
    }
    log.appendRow([m.getId(), m.getDate(), p.from || m.getFrom(), m.getSubject(), what, row || '', p.snippet || '']);
  });
  if (full) props.setProperty('LEDGER_SCANNED', '1');
  weeklyIfDue_(props, new Date());
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
  '• продати 3 — готове оголошення для eBay: ціна, пороги Preisvorschlag, назва й опис німецькою ' +
  '(3 — номер у таблиці; «продати» без номера — список того, що на руках)\n' +
  '• виставив 3 [124] — товар №3 уже на eBay (статус «Виставлено», ціну — в нотатки), щоб звіт не нагадував\n' +
  '• звіт — тижневий звіт: прибуток, точність прогнозів, залежаний товар, що дають підписки (сам приходить щопонеділка)\n' +
  'Покупки й продажі з листів eBay/KA записуються самі — сюди прийде повідомлення.';

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
  if (sellCommand_(msg) || listedCommand_(msg) || ledgerCommand(msg)) return;
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
  const m = text.match(/^\/?(купив|купила|продав|продала|облік)(?=\s|$)\s*([\s\S]*)$/i);
  if (!m) return false;
  if (!chat || String(msg.chat.id) !== String(chat) || !props.getProperty('LEDGER_ID')) return true;   // чужий чат — мовчки
  const cmd = m[1].toLowerCase(), rest = m[2] || '';
  if (cmd === 'облік') {
    const s = ledger_().getSheetByName('Підсумок');
    const v = s.getRange('B3:B11').getValues().map(function (r) { return r[0]; });
    notify_('📒 Облік: угод ' + v[0] + ', продано ' + v[1] + ', на руках ' + v[2] + '\nУ товарі: ' + Number(v[4]).toFixed(2) +
            ' €\nПрибуток з проданого: ' + Number(v[6]).toFixed(2) + ' €\n' + ledger_().getUrl());
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
