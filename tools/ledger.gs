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

const VER_LEDGER = '2026-10-16f';   // версія файлу: сторож порівнює з GitHub і нагадує оновити (при зміні файлу — підняти)
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

// 13.10: перемикач «комерційний акаунт eBay» (Kleingewerbe) — ПОКИ ВИМКНЕНО; вмикати разом з BUSINESS_SELLER
// у research/ram_alert.py. Комісія (5% вживане ×1,19 + 0,95 €) пишеться лише в нові продажі — старі приватні не міняються;
// «облік» показує податок на прибуток (~30%).
const BUSINESS_SELLER = false, BIZ_FEE_PCT = 0.0595, BIZ_FEE_FIXED = 0.95, TAX_RATE = 0.30;
function business_() { return BUSINESS_SELLER; }

/** Продаж записано → з комерційним акаунтом комісія eBay саме цього рядка (без «Налаштування», щоб не чіпати старі). */
function saleFee_(sh, row, src) {
  if (!business_() || !/ebay/i.test(String(src || ''))) return;   // KA / самовивіз — без комісії eBay
  sh.getRange(row, COL.comm).setFormula('=IF(N' + row + '="","",ROUND(N' + row + '*' + BIZ_FEE_PCT + '+' + BIZ_FEE_FIXED + ',2))');
}

const FEE_NOTE = 'Приватний продавець на eBay.de: продаж у Німеччині без комісії (довідка eBay, 09.10.2026). ' +
  'Якщо виплата буде менша за ціну — впиши реальну комісію.';

/** 09.10: комісію eBay прибрано (0%) — у вже створеній таблиці міняємо лише старі значення 6,5% / 0,45 €, один раз. */
function feeZero_() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('FEE_ZERO') === '1') return;
  const set = ledger_().getSheetByName('Налаштування');
  if (!set) return;
  const v = set.getRange('B3:B4').getValues();
  if (Math.abs(Number(v[0][0]) - 0.065) < 1e-9) set.getRange('B3').setValue(0);
  if (Math.abs(Number(v[1][0]) - 0.45) < 1e-9) set.getRange('B4').setValue(0);
  set.getRange('C3:C4').setValues([[FEE_NOTE], [FEE_NOTE]]);
  props.setProperty('FEE_ZERO', '1');
}

const PACK_NOTE = 'Підставляється, коли «Пакування» в «Угоди» порожнє. 1 € — коробка з дому, пакет від планок, скотч (10.10).';

/** 10.10: пакування за замовчуванням 4 → 1 € у вже створеній таблиці (лише якщо там досі 4), один раз. */
function packOne_() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('PACK_ONE') === '1') return;
  const set = ledger_().getSheetByName('Налаштування');
  if (!set) return;
  if (Number(set.getRange('B5').getValue()) === 4) set.getRange('B5').setValue(1);
  set.getRange('C5').setValue(PACK_NOTE);
  props.setProperty('PACK_ONE', '1');
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
    ['Комісія eBay за продаж, % від суми', 0, FEE_NOTE],
    ['Фіксований збір eBay за замовлення, €', 0, FEE_NOTE],
    ['Пакування за замовчуванням, €', 1, PACK_NOTE]]);
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
// 11.10: KA «Direkt kaufen» — лист KA «Dein Kauf wurde bestätigt!» без ціни; ціна, пересилка й Käuferschutz — лише в листі
// PayPal «You authorized a payment» (Payment to: kleinanzeigen.de GmbH). PayPal-листи про інші покупки пропускаємо.
const LEDGER_QUERY = '(from:ebay OR from:kleinanzeigen OR from:paypal) newer_than:45d subject:(bestellbestätigung ' +
  'OR bestellung OR gekauft OR verkauft OR versandt OR versendet OR verschickt OR unterwegs OR zugestellt OR geliefert OR storniert ' +
  'OR abgebrochen OR erstattet OR rückerstattung OR gewonnen OR bestätigt OR zahlung OR authorized OR autorisiert OR payment) ' +
  '-subject:"Suchauftrag" -subject:"neue Anzeigen"';
const KINDS = [   // порядок важливий: скасування раніше за «куплено»
  ['cancel', /storniert|abgebrochen|rückerstattung|erstattet|zurückerstattet|cancel+ed|refund/i],
  ['delivered', /zugestellt|geliefert|delivered/i],
  ['shipped', /versandt|verschickt|versendet|ist unterwegs|auf dem weg|shipped/i],
  ['sale', /(?:wurde|haben|hast|ist)\s+verkauft|artikel verkauft|wurde gekauft|hat .{0,60}gekauft|sold/i],
  ['purchase', /bestellbestätigung|bestellung .{0,20}bestätigt|(?:sie haben|du hast) .{0,80}(?:gekauft|gewonnen)|kauf (?:wurde )?bestätigt|zahlung (?:bestätigt|erfolgreich)|order confirmed|you (?:bought|won)|authori[sz]ed a payment|zahlung autorisiert/i]];

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
  const pp = /paypal/i.test(from);
  if (pp && !/kleinanzeigen\.de\s*gmbh/i.test(body + ' ' + html)) return { skip: true };   // PayPal — лише оплати на KA
  const src = /ebay/i.test(from) ? 'eBay' : /kleinanzeigen/i.test(from) || pp ? 'Kleinanzeigen' : 'Інше';
  let kind = null;
  for (let i = 0; i < KINDS.length; i++) if (KINDS[i][1].test(subject)) { kind = KINDS[i][0]; break; }
  if (!kind) return { skip: true };   // реклама, повідомлення, «Preisvorschlag» тощо — не угода
  // назва товару в тексті: KA «Kauf des Artikels "…"», PayPal — рядок «назва   90,00 € EUR» над «Item no: 1»
  const kaItem = body.match(/Kauf des Artikels\s+["„“]([^"“”\n]{4,150})["“”]/i);
  const ppItem = pp ? body.match(/(?:^|\n)[ \t]*([^\n\t]{4,150}?)[ \t\n]+([\d.]{1,7},\d{2})\s*€?\s*EUR\s*\n\s*(?:Item no|Artikel)/i) : null;
  const idm = (html + ' ' + body).match(/\/itm\/(?:[^\/\s"'<>]*\/)?(\d{10,14})/) ||
              body.match(/(?:Artikelnummer|Artikel-?Nr\.?|Item number)[:\s#]*(\d{10,14})/i);
  const kam = (html + ' ' + body).match(/https?:\/\/(?:www\.)?kleinanzeigen\.de\/s-anzeige\/[^\s"'<>]+/);
  let title = (subject.match(/[„"«]([^“"»]{4,})[“"»]/) || [])[1] ||
              (subject.indexOf(':') >= 0 ? subject.slice(subject.indexOf(':') + 1)
               : subject.replace(/^.*?(?:bestätigung|bestätigt|verkauft|gekauft|versandt|versendet|zugestellt|storniert)\s+(?:für\s+)?/i, ''));
  if (kaItem || ppItem) title = (kaItem || ppItem)[1];
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
  // 11.10: лист eBay про продаж — «Verkauft:\nEUR 149,00» (мітки «Preis» там немає, ціна продажу лишалась порожня)
  let price = ppItem ? money_(ppItem[2]) : amountAfter_(body, 'Artikelpreis|Kaufpreis|Verkaufspreis|Sofort-Kaufen-Preis|Preis|Verkauft');
  const fee = amountAfter_(body, 'Käuferschutz|Servicegebühr|Gebühr');
  if (price == null && total != null) price = Math.round((total - (ship || 0) - (fee || 0)) * 100) / 100;
  return { kind: kind, src: src, title: title, id: idm ? idm[1] : '', link: idm ? 'https://www.ebay.de/itm/' + idm[1] : (kam ? kam[0] : ''),
           price: price, ship: ship, fee: fee, total: total, date: msg.getDate(), subject: subject, from: from,
           track: kind === 'shipped' || kind === 'delivered' ? trackFrom_(subject + '\n' + body) : null,
           snippet: body.replace(/\s+/g, ' ').slice(0, 300) };
}

function processLedger() {
  try { feeZero_(); } catch (e) { console.log('feeZero_: ' + e.message); }
  try { packOne_(); } catch (e) { console.log('packOne_: ' + e.message); }
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
          const adId = kaAdId_(p.link);
          if (p.src === 'Kleinanzeigen' && adId) kaCloseDispatch_([adId], 'куплено');   // переписку з продавцем — з чату (06.10)
          const pn = row - LEDGER_FIRST + 1;
          const ptext = '📒 Записав покупку №' + pn + ': ' + p.title + (p.price != null ? ' — ' + p.price + ' €' : '') +
                  (p.price == null ? '\n⚠️ Суму не знайшов у листі — впиши в таблиці.' : '') +
                  (/ps\s?5|playstation\s?5/i.test(p.title) ? '\n💡 PS5 можна спробувати продати на Amazon (+40–60 €) — напиши Claude.' : '');
          const pkb = btns_([['отримав', pn]]);
          planAfter_(notify_(ptext, pkb), pn, p.title, p.price, ptext, pkb);
        }
      } else if ((p.kind === 'shipped' || p.kind === 'delivered') && p.track && rowByTrack_(p.track.num, 'outTrack')) {
        const on = rowByTrack_(p.track.num, 'outTrack');   // наша посилка покупцю, а не купівля (06.10)
        row = on + LEDGER_FIRST - 1;
        what = outParcel_(on, p.kind === 'delivered' ? 'delivered' : 'transit', p.date);
      } else if (p.kind === 'shipped' || p.kind === 'delivered' || p.kind === 'cancel') {
        row = findRow_(p.id, p.title, true);
        const st = p.kind === 'shipped' ? 'В дорозі' : p.kind === 'delivered' ? 'Отримано' : 'Скасовано';
        if (row) {
          const cur = deals.getRange(row, COL.status).getValue();
          if (cur !== 'Продано' && !(p.kind === 'shipped' && cur === 'Отримано')) {
            deals.getRange(row, COL.status).setValue(st);
            if (p.kind !== 'cancel') markEvent_(row - LEDGER_FIRST + 1, p.kind === 'shipped' ? 'shipped' : 'got', iso_(p.date));
            if (p.track) setEvents_(row - LEDGER_FIRST + 1, { track: p.track.num, carrier: p.track.carrier });
            if (p.kind === 'shipped' && cur !== 'В дорозі') shippedNotice_(row, p.track);
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
          saleFee_(deals, row, p.src || 'eBay');
          deals.getRange(row, COL.status).setValue('Продано');
          what = 'продаж записано';
          notify_('💰 Продано №' + (row - LEDGER_FIRST + 1) + ': ' + p.title + (p.total || p.price ? ' — ' + (p.total || p.price) + ' €' : '') +
                  '\n📮 Відправ за 1–3 робочі дні, потім «відправив ' + (row - LEDGER_FIRST + 1) + ' трек» або фото квитанції з підписом «' +
                  (row - LEDGER_FIRST + 1) + '» — трек піде в eBay, покупцю — подяка.', btns_([['відправив', row - LEDGER_FIRST + 1]]));
          if (/ebay/i.test(String(p.src || ''))) feedbackToEbay_(row - LEDGER_FIRST + 1, p.title);
        } else { what = 'продаж: не знайшов, що це за товар — впиши вручну'; }
      }
    }
    log.appendRow([m.getId(), m.getDate(), p.from || m.getFrom(), m.getSubject(), what, row || '', p.snippet || '']);
  });
  try { processCarriers_(log, done); } catch (e) { console.log('перевізники: ' + e); }
  try { parcelsWatch_(); } catch (e) { console.log('посилки: ' + e); }
  try { processEbayMail_(log, done); } catch (e) { console.log('листи eBay: ' + e); }
  try { photoPendingCheck_(); } catch (e) { console.log('фото: ' + e); }
  try { fixAlbum1010_(); } catch (e) { console.log('fixAlbum1010_: ' + e); }
  try { fixSalePrice1011_(); } catch (e) { console.log('fixSalePrice1011_: ' + e); }
  try { summaryNoCancelled1011_(); } catch (e) { console.log('summaryNoCancelled1011_: ' + e); }
  try { moneyBlock1011_(); } catch (e) { console.log('moneyBlock1011_: ' + e); }
  try { keyboard1013_(); } catch (e) { console.log('keyboard1013_: ' + e); }
  try { keyboard1016_(); } catch (e) { console.log('keyboard1016_: ' + e); }
  try { tidySheet1013_(); } catch (e) { console.log('tidySheet1013_: ' + e); }
  try { assistConnect_(); } catch (e) { console.log('assistConnect_: ' + e); }
  try { enableButtons_(); } catch (e) { console.log('кнопки: ' + e); }
  if (full) props.setProperty('LEDGER_SCANNED', '1');
  if (!props.getProperty('EXPENSES_V1')) { expensesSheet_(ss); props.setProperty('EXPENSES_V1', '1'); }   // аркуш одразу видно
  props.setProperty('LEDGER_OK', String(Date.now()));   // 09.10: сторож бачить, що облік живий (раніше падав би мовчки)
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
// 13.10 (крок 8 — простіше): головний екран — те, чим користувач користується найчастіше (посилки, облік, продати, звіт);
// замість довідки на 20 рядків — «☰ Меню» з розділами кнопками, у кожному 3–5 рядків і кнопки дій. Текстові команди — ті самі.
// 16.10: + «🏷 Виставлено» — що зараз на продажу, з посиланнями
const OFFICE_KEYBOARD = { keyboard: [[{ text: '📦 Посилки' }, { text: '🏷 Виставлено' }, { text: '📒 Облік' }],
  [{ text: '💶 Продати' }, { text: '🗂 На руках' }, { text: '📊 Звіт' }, { text: '☰ Меню' }]],
  resize_keyboard: true,
  is_persistent: true };
const KEY_ALIAS = { '📦 посилки': 'посилки', '🏷 виставлено': 'виставлено', '📒 облік': 'облік', '💶 продати': 'продати', '📊 звіт': 'звіт',
  '🗂 на руках': 'на руках', '☰ меню': 'меню' };
// кнопки дій у меню: k|<команда> (лише ці)
const MENU_ACTIONS = { 'посилки': '📦 Посилки', 'виставлено': '🏷 Виставлено', 'облік': '📒 Облік', 'продати': '💶 Продати', 'звіт': '📊 Звіт', 'на руках': '🗂 На руках',
  'нагадування': '⏰ Нагадування' };
const MENU = {
  buy: ['🛒 Купівля', 'Картки угод — в ebay_bot (ПК — у pc_bot); покупки з листів eBay / KA записуються самі.\n' +
    '• купив 45 OWC 2x16 DDR4 — записати вручну (додай «ebay» або «самовивіз», якщо не KA)\n' +
    '• отримав 3 — посилка №3 прийшла\n' +
    '• фото з підписом 3 — наклейки й екран MemTest86; без помилок → «Перевірено»\n' +
    '• перевірив 3 / проблема 3 — протестовано / щось не так (підкажу, як заявити)\n' +
    '• межі — межі цін у підписках KA', ['на руках']],
  sell: ['💶 Продаж', '• продати — що вже можна продавати; продати 3 — готове оголошення (ціна, назва, опис німецькою) ' +
    'і кнопка «🚀 Виставити на eBay»; ✏️ — змінити ціну, назву, опис чи фото («ціна 3 145»); уже виставлене — 🔁 Оновити на eBay\n' +
    '• відправив 3 трек — трек піде в eBay сам, покупцю — подяка\n' +
    '• виставив 3 124 — якщо виставив сам\n' +
    '• виставлено — що зараз на продажу: ціна, дні, перегляди, спостерігачі, посилання (eBay; KA — «Мої оголошення»)\n' +
    '• питання й пропозиції ціни від покупців приходять сюди з кнопками відповіді', ['продати', 'виставлено']],
  parcels: ['📦 Посилки', '• посилки — де кожна посилка до тебе й від тебе, коли очікується, чи дійшла\n' +
    '• сам напишу, коли посилка чекає у відділенні або доставлена (перевіряю раз на 3 години, 8–21)\n' +
    '• трек 3 номер — додати трек (KA шле трек лише в чат)', ['посилки']],
  money: ['📒 Гроші', '• облік — прибуток, гроші зараз, товар на руках і посилання на таблицю\n' +
    '• продав 110 OWC — записати продаж вручну (продажі eBay записуються самі)\n' +
    '• витрата 14 перехідник DDR4 — витрата не на товар\n' +
    '• звіт — тижневий звіт (сам приходить щопонеділка)', ['облік', 'звіт']],
  other: ['⚙️ Інше', '• нагадай 20.10 текст — нагадаю того дня; нагадування — список\n' +
    '• фото 3 — показати фото №3; перенеси фото 1 2 — якщо потрапили не туди\n' +
    '• ebay вхід — підключити eBay (ключ діє ~18 місяців)\n' +
    '• щодня о 10:00 — «☀️ Ранковий огляд»: гроші, посилки й що зробити сьогодні\n' +
    'Боти: ebay_bot — угоди RAM · pc_bot — ПК на розбір · retail_helper — питання й поради · цей — облік і продаж.',
    ['нагадування']] };
const MENU_ORDER = ['buy', 'sell', 'parcels', 'money', 'other'];
const OFFICE_HELP = '☰ Меню — обери розділ. Найчастіше потрібне — на кнопках унизу.';

function menuMarkup_() {
  const b = MENU_ORDER.map(function (k) { return { text: MENU[k][0], callback_data: 'm|' + k }; });
  return { inline_keyboard: [[b[0], b[1]], [b[2], b[3]], [b[4]]] };
}

function sectionMarkup_(key) {
  const acts = MENU[key][2].map(function (c) { return { text: MENU_ACTIONS[c], callback_data: 'k|' + c }; });
  return { inline_keyboard: [acts, [{ text: '← Меню', callback_data: 'm|menu' }]] };
}

/** m|розділ — показати розділ меню в тому ж повідомленні; k|команда — виконати дію з меню. */
function menuCallback_(cq, chat) {
  const m = String(cq.data || '').match(/^([mk])\|(.+)$/);
  if (!m) return false;
  const from = cq.message && cq.message.chat ? cq.message.chat.id : '';
  tgOffice_('answerCallbackQuery', { callback_query_id: cq.id });
  if (!chat || String(from) !== String(chat)) return true;
  if (m[1] === 'k') {
    if (MENU_ACTIONS[m[2]]) officeMessage({ text: m[2], chat: { id: from }, message_id: 0 });
    return true;
  }
  const key = MENU[m[2]] ? m[2] : 'menu';
  tgOffice_('editMessageText', { chat_id: chat, message_id: cq.message.message_id,
    text: key === 'menu' ? OFFICE_HELP : MENU[key][0] + '\n\n' + MENU[key][1],
    reply_markup: JSON.stringify(key === 'menu' ? menuMarkup_() : sectionMarkup_(key)) });
  return true;
}

function officeToken_(props) {
  return props.getProperty('OFFICE_BOT_TOKEN') || props.getProperty('TELEGRAM_BOT_TOKEN');
}

function notify_(text, markup, mode) {
  const props = PropertiesService.getScriptProperties();
  const tok = officeToken_(props), chat = props.getProperty('TELEGRAM_CHAT_ID');
  if (!tok || !chat) return null;
  const payload = { chat_id: chat, text: text, disable_web_page_preview: 'true' };
  if (mode) payload.parse_mode = mode;
  if (markup) payload.reply_markup = JSON.stringify(markup);
  return UrlFetchApp.fetch('https://api.telegram.org/bot' + tok + '/sendMessage', {
    method: 'post', payload: payload, muteHttpExceptions: true });
}

// 14.10: «Забрав 2» не спрацювало — синоніми першого слова → команда (у всіх ботах, де приймаємо команди обліку)
const CMD_SYNONYMS = { 'забрав': 'отримав', 'забрала': 'отримав', 'забрали': 'отримав', 'прийшло': 'отримав', 'прийшла': 'отримав',
  'прийшов': 'отримав', 'дійшло': 'отримав', 'дійшла': 'отримав', 'отримала': 'отримав', 'отримали': 'отримав',
  'протестував': 'перевірив', 'протестувала': 'перевірив', 'перевірила': 'перевірив', 'тест': 'перевірив',
  'надіслав': 'відправив', 'надіслала': 'відправив', 'відіслав': 'відправив', 'відправила': 'відправив' };

// 16.10 (користувач: «міні-хелпер — розумів, що я мав на увазі, і мовчки виконував»): без ШІ й без запитів — описки
// (відстань Левенштейна 1–2 до слова команди) і звичайні фрази → команда. Лише коли інакше було б «Не зрозумів»;
// дві можливі команди («продат 3»: продати / продав) — не вгадуємо, перепитуємо.
const CMD_WORDS = ['посилки', 'виставлено', 'облік', 'продати', 'звіт', 'меню', 'нагадування', 'межі', 'отримав', 'перевірив',
  'проблема', 'відправив', 'трек', 'виставив', 'ціна', 'назва', 'опис', 'скинути', 'змінити', 'фото', 'купив', 'продав',
  'витрата', 'нагадай', 'кроки', 'допомога'];
const CMD_PHRASES = [[/^(?:що|які|покажи)?\s*(?:на\s+продаж[^\s?]*|виставлен[^\s?]*|мої\s+лоти|лоти|мої\s+оголошення)\??$/i, 'виставлено'],
  [/^(?:де|що\s+з)\s+(?:мої\s+)?(?:посилк[^\s?]*|пакунк[^\s?]*|парсел[^\s?]*)\??$|^трекінг$/i, 'посилки'],
  [/^(?:скільки\s+(?:я\s+)?(?:заробив|заробила|грошей)|гроші|баланс|прибуток|фінанси)\??$/i, 'облік'],
  [/^(?:що\s+(?:в\s+мене|є|на\s+руках)|склад|залишки)\??$/i, 'на руках'],
  [/^(?:статистика|тижневий\s+звіт)\??$/i, 'звіт'],
  [/^(?:що\s+ти\s+вмієш|команди|що\s+вмієш|help me)\??$/i, 'меню']];

function lev_(a, b) {
  const m = a.length, n = b.length;
  if (Math.abs(m - n) > 2) return 9;
  // відстань з переставленими сусідніми літерами як однією помилкою («назав» → «назва»)
  const d = [];
  for (let i = 0; i <= m; i++) { d[i] = [i]; for (let j = 1; j <= n; j++) d[i][j] = i ? 0 : j; }
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
    }
  }
  return d[m][n];
}

/** Описка / фраза → {text: виправлена команда} або {ambiguous: [команди]}; null — не схоже на команду. */
function fuzzyCmd_(text) {
  const o = String(text || '').trim().replace(/\s+/g, ' '), t = o.toLowerCase();   // регістр решти тексту (назва, опис) — як написав
  if (!t || t.length > 120) return null;
  for (let i = 0; i < CMD_PHRASES.length; i++) if (CMD_PHRASES[i][0].test(t)) return { text: CMD_PHRASES[i][1] };
  const parts = t.match(/^\/?(\S+)(?:\s+(\S+))?([\s\S]*)$/);
  if (!parts) return null;
  const two = parts[2] ? parts[1] + ' ' + parts[2] : '';
  if (two && !/\d/.test(two) && lev_(two, 'на руках') <= 2) return { text: 'на руках' + parts[3] };
  const w = parts[1], rest = o.slice(t.indexOf(w) + w.length);
  if (w.length < 4 || CMD_WORDS.indexOf(w) >= 0) return null;
  const words = CMD_WORDS.concat(Object.keys(CMD_SYNONYMS));
  const hits = {};
  words.forEach(function (c) {
    if (lev_(w, c) <= (c.length > 5 ? 2 : 1)) hits[CMD_SYNONYMS[c] || c] = 1;
  });
  const list = Object.keys(hits);
  if (list.length === 1) return { text: list[0] + rest };
  return list.length > 1 ? { ambiguous: list.map(function (c) { return c + rest; }) } : null;
}

/** «Забрав 2» → «отримав 2»; решта тексту без змін. */
function canonCmd_(text) {
  const t = String(text || '').trim(), m = t.match(/^\/?(\S+)([\s\S]*)$/);
  const syn = m && CMD_SYNONYMS[m[1].toLowerCase()];
  return syn && /^\s+\d{1,4}(?=\s|$)/.test(m[2]) ? syn + m[2] : t;   // лише з номером: «прийшло 3», не «прийшло щось»
}

/** Усе, що пишуть боту «Облік і продаж» (doPost у Code.gs з ?bot=office). Посилання тут не оцінюються. */
function officeMessage(msg) {
  const chat = PropertiesService.getScriptProperties().getProperty('TELEGRAM_CHAT_ID');
  if (!chat || String(msg.chat.id) !== String(chat)) return;   // чужий чат — мовчки
  const alias = KEY_ALIAS[String(msg.text || '').trim().toLowerCase()];   // кнопки клавіатури з емодзі → команда
  if (alias) msg = Object.assign({}, msg, { text: alias });
  else if (canonCmd_(msg.text) !== String(msg.text || '').trim()) msg = Object.assign({}, msg, { text: canonCmd_(msg.text) });
  if (memtestReport_(msg) || photoMessage_(msg) || photoNumber_(msg) || photoShowCommand_(msg) || photoMoveCommand_(msg) ||
      photoDeleteCommand_(msg) ||
      onHandCommand_(msg)) return;
  if (parcelsCommand_(msg) || listingsCommand_(msg) || editCommand_(msg) || ebayCommand_(msg) || boundsCommand_(msg) || stepsCommand_(msg) || sellCommand_(msg) || listedCommand_(msg) || statusCommand_(msg) || todoCommand_(msg) || ledgerCommand(msg)) return;
  if (/^\/?(звіт|report)(?=\s|$)/i.test(String(msg.text || '').trim())) {
    notify_(weeklyReport() ? '⏳ Готую звіт — приблизно хвилина.' : '⚠️ Не зміг запустити звіт (GitHub).');
    return;
  }
  if (/^\/?(допомога|help|start|меню|menu)$/i.test(String(msg.text || '').trim())) {
    notify_('🔘 Кнопки внизу — головне.', OFFICE_KEYBOARD);   // оновити постійну клавіатуру
    notify_(OFFICE_HELP, menuMarkup_());
    return;
  }
  const fx = msg._fixed ? null : fuzzyCmd_(msg.text);
  if (fx && fx.text) { officeMessage(Object.assign({}, msg, { text: fx.text, _fixed: true })); return; }   // мовчки — як точна команда
  if (fx && fx.ambiguous) {
    notify_('🤔 Не впевнений, що саме: ' + fx.ambiguous.map(function (c) { return '«' + c.trim() + '»'; }).join(' чи ') +
            '? Напиши ще раз точно — тут різні дії.');
    return;
  }
  notify_('Не зрозумів 🙂 Натисни «☰ Меню» — там усе, що вміє бот. Оголошення для оцінки — в ebay_bot, питання — в retail_helper.',
          OFFICE_KEYBOARD);
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
  const s = notify_('✅ Бот «Облік і продаж» підключено. Головне — на кнопках унизу, решта — у «☰ Меню».', OFFICE_KEYBOARD);
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
    notify_(ledgerSummary_(ledgerRows_(), expenseRows_()) + '\n\n<a href="' + ledger_().getUrl() + '">📊 Таблиця</a>', null, 'HTML');
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
    const ctext = '📒 Записав покупку №' + (r - 4) + ': ' + title + ' — ' + amount + ' €' +
            (src === 'Kleinanzeigen' ? '\nПересилку й збір Sicher bezahlen допиши в таблиці, якщо були.' : '');
    const ckb = btns_([['отримав', r - 4]]);
    planAfter_(notify_(ctext, ckb), r - 4, title, amount, ctext, ckb);
  } else {
    const row = findRow_(null, title, true);
    if (!row) { notify_('Не знайшов у обліку непроданий товар зі словами «' + title + '». Уточни назву або впиши вручну.'); return true; }
    const sh = ledger_().getSheetByName('Угоди');
    sh.getRange(row, COL.sdate).setValue(new Date());
    sh.getRange(row, COL.sto).setValue(src);
    sh.getRange(row, COL.sprice).setValue(amount);
    saleFee_(sh, row, src);
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
  if (m[1] && extra) {   // назва, з якої зроблена картка, — її ж візьме автопублікація («🚀»)
    const t = JSON.parse(props.getProperty('SELL_TITLES') || '{}');
    t[idx + 1] = title.slice(0, 150);
    props.setProperty('SELL_TITLES', JSON.stringify(t));
  }
  const code = sellDispatch_({ row: idx + 1, title: title, cost: cost, photos: cleanPhotos_(idx + 1), ebay: !!props.getProperty('EBAY_RT'),
    edits: sellEdits_()[idx + 1] || null, item_id: v[COL.status - 1] === 'Виставлено' ? listingId_(v[COL.note - 1]) : '' });
  notify_(code === 204
    ? '⏳ Готую оголошення для №' + (idx + 1) + ' «' + title + '» — приблизно хвилина.'
    : '⚠️ Не зміг запустити підготовку оголошення (GitHub ' + code + ').');
  return true;
}

/** Номер eBay-оголошення з нотаток рядка («виставлено … (eBay 123456789012)») — останній. */
function listingId_(note) {
  const all = String(note || '').match(/eBay (\d{9,15})/g);
  return all ? all[all.length - 1].replace(/\D/g, '') : '';
}

/** 16.10: «🔁 Оновити на eBay» → GitHub (sell.yml, режим revise): правки в живе оголошення (назва, опис, ціна, пороги, фото). */
function reviseCommand_(n) {
  const props = PropertiesService.getScriptProperties(), rt = props.getProperty('EBAY_RT');
  if (!rt) { notify_('🔑 eBay ще не підключено — напиши «ebay вхід».'); return; }
  const r = rowByN_(n);
  if (!r) { notify_('У таблиці немає №' + n + '.'); return; }
  const note = String(ledger_().getSheetByName('Угоди').getRange(n + LEDGER_FIRST - 1, COL.note).getValue() || '');
  const itemId = listingId_(note);
  if (r.status !== 'Виставлено' || !itemId) {
    notify_('№' + n + ' ще не виставлено автоматично на eBay (немає номера оголошення) — спершу 🚀 або «виставив ' + n + '».'); return;
  }
  const title = JSON.parse(props.getProperty('SELL_TITLES') || '{}')[n] || r.title;
  const code = sellDispatch_({ mode: 'revise', row: n, title: String(title), cost: r.spent, photos: cleanPhotos_(n), rt: rt,
    edits: sellEdits_()[n] || null, item_id: itemId });
  notify_(code === 204 ? '⏳ Оновлюю №' + n + ' на eBay — 1–2 хвилини.' : '⚠️ Не зміг запустити (GitHub ' + code + ').');
}

/** «видали фото N» — прибрати всі фото №N (надіслати нові з підписом «N»). */
function photoDeleteCommand_(msg) {
  const m = String(msg.text || '').trim().match(/^\/?(?:видали|очисти|прибери)\s+фото\s+(\d{1,4})$/i);
  if (!m) return false;
  const n = Number(m[1]), had = photosOf_(n).length;
  savePhotos_(n, []);
  notify_(had ? '🗑 Прибрав ' + had + ' фото №' + n + '. Надішли нові з підписом «' + n + '».' : 'У №' + n + ' фото не було.');
  return true;
}

// 14.10 (користувач): підправити оголошення з телефона перед «🚀» — ціна, назва на eBay, дописати в опис. Правки живуть
// до публікації (SELL_EDITS), застосовуються однаково до картки, покрокових і автопублікації; після кожної — нова картка.
function sellEdits_() { return JSON.parse(PropertiesService.getScriptProperties().getProperty('SELL_EDITS') || '{}'); }

function editCommand_(msg) {
  const t = String(msg.text || '').trim();
  const m = t.match(/^\/?(змінити|ціна|назва|опис|скинути|оригінали|обробити)\s+(\d{1,4})(?:\s+([\s\S]+))?$/i);
  if (!m) return false;
  const props = PropertiesService.getScriptProperties();
  if (String(msg.chat.id) !== String(props.getProperty('TELEGRAM_CHAT_ID'))) return true;
  const cmd = m[1].toLowerCase(), n = Number(m[2]), arg = (m[3] || '').trim(), r = rowByN_(n);
  if (!r) { notify_('У таблиці немає №' + n + '.'); return true; }
  const ed = sellEdits_(), cur = ed[n] || {};
  if (cmd === 'оригінали' || cmd === 'обробити') {   // 16.10: фото на eBay — без обробки / знову з білим фоном
    ed[n] = Object.assign(cur, { raw: cmd === 'оригінали' });
    if (!ed[n].raw) delete ed[n].raw;
    props.setProperty('SELL_EDITS', JSON.stringify(ed));
    notify_(cmd === 'оригінали' ? '📸 №' + n + ': на eBay підуть фото як є, без обробки. Повернути білий фон — «обробити ' + n + '».'
                               : '📸 №' + n + ': фото знову з білим фоном.');
    return true;
  }
  if (cmd === 'змінити' || (!arg && cmd !== 'скинути')) {
    const has = [cur.price ? 'ціна ' + cur.price + ' €' : '', cur.title ? 'назва «' + h_(cur.title) + '»' : '', cur.desc ? 'опис дописано' : '']
      .filter(Boolean);
    notify_('✏️ <b>Змінити оголошення №' + n + '</b>' + (has.length ? '\nЗараз: ' + has.join(' · ') : '') +
      '\n\nНатисни команду — скопіюється, допиши й надішли:\n<blockquote>' +
      '<code>ціна ' + n + ' </code>— ціна товару без доставки, напр. «ціна ' + n + ' 145»\n' +
      '<code>назва ' + n + ' </code>— назва на eBay (до 80 знаків)\n' +
      '<code>опис ' + n + ' </code>— дописати в опис (німецькою)\n' +
      '<code>скинути ' + n + '</code> — повернути, як порахував бот\n' +
      'фото — надішли з підписом «' + n + '»; старі прибрати — <code>видали фото ' + n + '</code></blockquote>\n' +
      'Після кожної правки пришлю оновлену картку — тоді 🚀 (уже виставлене — 🔁 Оновити на eBay).', null, 'HTML');
    return true;
  }
  if (cmd === 'скинути') delete ed[n];
  else if (cmd === 'ціна') {
    const p = Number(arg.replace(/€/g, '').replace(',', '.').trim());
    if (!(p >= 1 && p <= 5000)) { notify_('Ціна — числом, без доставки: «ціна ' + n + ' 145».'); return true; }
    ed[n] = Object.assign(cur, { price: Math.round(p) });
  } else if (cmd === 'назва') {
    ed[n] = Object.assign(cur, { title: arg.slice(0, 80) });
    if (arg.length > 80) notify_('✂️ eBay приймає до 80 знаків — обрізав: «' + arg.slice(0, 80) + '».');
  } else {
    ed[n] = Object.assign(cur, { desc: arg.slice(0, 1000) });
  }
  props.setProperty('SELL_EDITS', JSON.stringify(ed));
  sellCommand_({ text: 'продати ' + n, chat: msg.chat });   // оновлена картка з правками
  return true;
}

/** 10.10: після «📒 Записав покупку» — GitHub дописує в те саме повідомлення план: за скільки продати й прибуток. */
function planAfter_(resp, n, title, cost, text, markup) {
  try {
    const mid = resp ? ((JSON.parse(resp.getContentText() || '{}') || {}).result || {}).message_id : null;
    if (mid && title) sellDispatch_({ mode: 'plan', row: n, title: String(title), cost: cost, mid: mid, text: text, markup: markup });
  } catch (e) { console.log('planAfter_: ' + e); }
}

/** «📋 Покроково» під карткою «продати N» → покрокова інструкція для форми eBay і фото альбомом. */
function stepsCommand_(msg) {
  const m = String(msg.text || '').trim().match(/^\/?кроки\s+(\d{1,4})$/i);
  if (!m) return false;
  const n = Number(m[1]), r = rowByN_(n);
  if (!r) { notify_('У таблиці немає №' + n + '.'); return true; }
  const props = PropertiesService.getScriptProperties();
  const title = JSON.parse(props.getProperty('SELL_TITLES') || '{}')[n] || r.title;
  const code = sellDispatch_({ row: n, title: String(title), cost: r.spent, photos: cleanPhotos_(n), steps: true, ebay: !!props.getProperty('EBAY_RT'),
    edits: sellEdits_()[n] || null });
  if (code !== 204) notify_('⚠️ Не зміг запустити (GitHub ' + code + ').');
  return true;
}

/** Дані → зашифровано → GitHub sell.yml (research/sell.py). → код відповіді GitHub (204 — запущено). */
function sellDispatch_(data) {
  const props = PropertiesService.getScriptProperties();
  const sealed = seal_(data, officeToken_(props), Utilities.getUuid().replace(/-/g, ''));
  const token = props.getProperty('GITHUB_TOKEN') || GITHUB_TOKEN;
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/sell.yml/dispatches', {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ ref: 'main', inputs: sealed }) });
  return r.getResponseCode();
}

function unseal_(s, key) {
  if (!s || !s.blob || !s.nonce || hmacHex_(key, 'mac:' + s.nonce + ':' + s.blob).slice(0, 32) !== s.mac) throw new Error('підпис не збігається');
  const raw = Utilities.base64Decode(s.blob);
  let ks = [];
  for (let i = 0; ks.length < raw.length; i++) ks = ks.concat(Utilities.computeHmacSha256Signature(s.nonce + ':' + i, key, Utilities.Charset.UTF_8));
  return JSON.parse(Utilities.newBlob(raw.map(function (b, i) { return b ^ ks[i]; })).getDataAsString());
}


// ------------------------------------------------------------------ автопублікація на eBay (09.10)
// «ebay вхід» → GitHub шле посилання на дозвіл eBay → користувач вставляє адресу з ?code=… → GitHub міняє код на ключ
// і повертає його сюди зашифрованим (sealedPost, підпис токеном бота) → Script Properties EBAY_RT (18 місяців).
// Кнопка «🚀» на картці «продати N» → підтвердження → GitHub: фото → eBay, перевірка, публікація → «Виставлено» тут.
function ebayCommand_(msg) {
  const t = String(msg.text || '').trim();
  const props = PropertiesService.getScriptProperties(), chat = props.getProperty('TELEGRAM_CHAT_ID');
  let m;
  if (/^\/?(?:ebay|ебей|ібей)\s*(?:вхід|підключити|login|connect)$/i.test(t)) {
    notify_(sellDispatch_({ mode: 'auth_url' }) === 204 ? '⏳ Готую посилання для входу в eBay — до хвилини.'
      : '⚠️ Не зміг запустити підключення eBay (GitHub).');
    return true;
  }
  if (/[?&]code=v(?:%5E|\^)1/i.test(t) || (/ebay/i.test(t) && /[?&]code=/.test(t))) {
    if (msg.message_id) tgOffice_('deleteMessage', { chat_id: chat, message_id: msg.message_id });   // код у чаті не лишаємо
    notify_(sellDispatch_({ mode: 'auth_code', code: t.slice(0, 3000) }) === 204 ? '⏳ Підключаю eBay — до хвилини.'
      : '⚠️ Не зміг запустити підключення eBay (GitHub).');
    return true;
  }
  if (/isAuthSuccessful=false/i.test(t)) { notify_('eBay: дозвіл не надано. Спробуй ще раз — «ebay вхід».'); return true; }
  if ((m = t.match(/^\/?авто\s+(\d{1,4})$/i))) {
    if (!props.getProperty('EBAY_RT')) { notify_('🔑 eBay ще не підключено — напиши «ebay вхід».'); return true; }
    notify_('🚀 Виставити №' + m[1] + ' на eBay автоматично?\nНазва, опис, ціна й пороги — як у картці вище, фото — збережені для №' +
            m[1] + '. Спершу eBay перевірить оголошення: якщо щось не так — нічого не опублікую й напишу, що саме.',
            { inline_keyboard: [[{ text: '✅ Так, виставити №' + m[1], callback_data: 'c|так-авто|' + m[1] }]] });
    return true;
  }
  if ((m = t.match(/^\/?(?:так-авто|опублікувати)\s+(\d{1,4})$/i))) { publishCommand_(Number(m[1]), false); return true; }
  if ((m = t.match(/^\/?оновити\s+(\d{1,4})$/i))) {   // 16.10: правки в живе оголошення — з підтвердженням
    notify_('🔁 Оновити оголошення №' + m[1] + ' на eBay?\nНазва, опис, ціна, пороги пропозицій і фото — як у картці вище ' +
            '(з твоїми правками). Оголошення лишається тим самим: перегляди й спостерігачі зберігаються.',
            { inline_keyboard: [[{ text: '✅ Так, оновити №' + m[1], callback_data: 'c|так-оновити|' + m[1] }]] });
    return true;
  }
  if ((m = t.match(/^\/?так-оновити\s+(\d{1,4})$/i))) { reviseCommand_(Number(m[1])); return true; }
  if ((m = t.match(/^\/?(?:ebay|ебей|ібей)\s+тест\s+(\d{1,4})$/i))) { publishCommand_(Number(m[1]), true); return true; }
  return false;
}

// ------------------------------------------------------------------ дії в eBay кнопками (10.10)
// Трек у замовлення, нова ціна, відповідь покупцю, відповідь на пропозицію — research/ebay_actions.py. Дані для кнопки
// (чернетка відповіді, сума пропозиції) — у EBAY_ACT за коротким номером: callback_data Telegram — до 64 байт.
function actPut_(obj) {
  const props = PropertiesService.getScriptProperties(), all = JSON.parse(props.getProperty('EBAY_ACT') || '{}');
  const id = String((Number(props.getProperty('EBAY_ACT_N')) || 0) + 1);
  props.setProperty('EBAY_ACT_N', id);
  all[id] = obj;
  Object.keys(all).sort(function (a, b) { return Number(a) - Number(b); }).slice(0, -30).forEach(function (k) { delete all[k]; });
  props.setProperty('EBAY_ACT', JSON.stringify(all));
  return id;
}

function ebayAct_(data) {
  const rt = PropertiesService.getScriptProperties().getProperty('EBAY_RT');
  if (!rt) { notify_('🔑 eBay ще не підключено — напиши «ebay вхід».'); return 0; }
  return sellDispatch_(Object.assign({ mode: 'ebay', rt: rt }, data));
}

function itemIdOf_(n) {
  const note = String(ledger_().getSheetByName('Угоди').getRange(n + LEDGER_FIRST - 1, COL.note).getValue() || '');
  return ((note.match(/eBay (\d{9,15})/) || [])[1]) || '';
}

/** Трек проданого на eBay → у замовлення eBay (якщо eBay підключено) + подяка покупцю (10.10, один раз). → рядок для повідомлення. */
function shipToEbay_(n, title, t) {
  if (!t || !PropertiesService.getScriptProperties().getProperty('EBAY_RT')) return '';
  const sto = String(ledger_().getSheetByName('Угоди').getRange(n + LEDGER_FIRST - 1, COL.sto).getValue() || '');
  if (sto && !/ebay/i.test(sto)) return '';
  const thanks = !(events_()[n] || {}).thanked;
  const ok = ebayAct_({ action: 'ship', row: n, title: String(title), track: t.num, carrier: t.carrier || 'DHL', item_id: itemIdOf_(n),
    thanks: thanks }) === 204;
  if (ok && thanks) setEvents_(n, { thanked: iso_(new Date()) });
  return ok ? '\n⏳ Передаю трек в eBay' + (thanks ? ' і подяку покупцю' : '') + '.' : '';
}

/** 10.10: фото квитанції відправки з номером товару → як «відправив N трек». → рядок у підсумок фото. */
function receiptShipped_(n, g) {
  const rc = g.receipt || {}, r = rowByN_(n);
  const t = rc.track ? trackArg_(String(rc.track).replace(/\s+/g, '') + ' ' + (rc.carrier || '')) : null;
  if (!t) return '🧾 Квитанцію бачу, але трек не прочитав — напиши «відправив ' + n + ' трек».';
  if (!r || r.status !== 'Продано') {
    return '🧾 Квитанція з треком ' + t.num + ', але №' + n + ' не позначений проданим — якщо це інший товар: «перенеси фото ' + n + ' N».';
  }
  setEvents_(n, { sent: iso_(new Date()), outTrack: t.num, outCarrier: t.carrier });
  return '🧾 Квитанція: ' + (t.carrier ? t.carrier + ' ' : '') + t.num + ' → «відправлено»' + shipToEbay_(n, r.title, t).replace(/^\n/, ' · ');
}

/** 10.10: продаж на eBay оплачено → позитивний відгук покупцю. */
function feedbackToEbay_(n, title) {
  if (!PropertiesService.getScriptProperties().getProperty('EBAY_RT')) return;
  ebayAct_({ action: 'feedback', row: n, title: String(title), item_id: itemIdOf_(n) });
}

/** e|id|send/acc/cnt/dec — питання і пропозиції з листів eBay; p|N|ціна — «⬇️ Знизити» з тижневого звіту. */
function ebayCallback_(cq, chat) {
  const data = String(cq.data || ''), from = cq.message && cq.message.chat ? cq.message.chat.id : '';
  const ex = data.match(/^e\|(\d{1,6})\|(send|acc|cnt|dec)$/), pc = data.match(/^p\|(\d{1,4})\|(\d{1,5})$/);
  if (!ex && !pc) return false;
  const label = ex ? { send: '📨 Надсилаю', acc: '✅ Приймаю', cnt: '🔁 Зустрічна', dec: '✖️ Відхиляю' }[ex[2]] : '⬇️ Знижую';
  tgOffice_('answerCallbackQuery', { callback_query_id: cq.id, text: label });
  if (!chat || String(from) !== String(chat)) return true;
  if (ex) {   // відповідь — одна: кнопки з картки прибираємо (посилання лишаємо)
    const kb = ((cq.message.reply_markup || {}).inline_keyboard || [])
      .map(function (row) { return row.filter(function (b) { return !b.callback_data; }); }).filter(function (row) { return row.length; });
    tgOffice_('editMessageReplyMarkup', { chat_id: chat, message_id: cq.message.message_id, reply_markup: JSON.stringify({ inline_keyboard: kb }) });
    const a = JSON.parse(PropertiesService.getScriptProperties().getProperty('EBAY_ACT') || '{}')[ex[1]];
    if (!a) { notify_('Ця кнопка застаріла — відповідай в eBay.'); return true; }
    const code = ebayAct_(Object.assign({}, a, { op: ex[2] }));
    if (code) notify_(code === 204 ? label + ' в eBay — до 2 хвилин.' : '⚠️ Не зміг запустити (GitHub ' + code + ').');
    return true;
  }
  const n = Number(pc[1]), r = rowByN_(n);
  if (!r) { notify_('У таблиці немає №' + n + '.'); return true; }
  const code = ebayAct_({ action: 'revise', row: n, title: r.title, price: Number(pc[2]), item_id: itemIdOf_(n) });
  if (code) notify_(code === 204 ? '⬇️ Знижую ціну №' + n + ' до ' + pc[2] + ' € на eBay — до 2 хвилин.' : '⚠️ Не зміг запустити (GitHub ' + code + ').');
  return true;
}

/** Кнопки під листом eBay: питання → «📨 Надіслати відповідь»; пропозиція покупця → прийняти / зустрічна / відхилити. */
function ebayMailButtons_(g, markup) {
  if (!PropertiesService.getScriptProperties().getProperty('EBAY_RT') || g.role === 'buyer') return markup;
  let rows = [];
  if ((g.kind === 'question' || g.kind === 'message') && g.reply_de) {
    rows = [[{ text: '📨 Надіслати відповідь (як вище)', callback_data: 'e|' + actPut_({ action: 'answer', text: String(g.reply_de).slice(0, 1500),
      question: String(g.message || '').slice(0, 400), title: String(g.item || '').slice(0, 120) }) + '|send' }]];
  } else if ((g.kind === 'offer' || g.kind === 'counter') && Number(g.amount) > 0) {
    const id = actPut_({ action: 'offer', amount: Number(g.amount), title: String(g.item || '').slice(0, 120) });
    rows = [[{ text: '✅ Прийняти ' + Number(g.amount).toFixed(0) + ' €', callback_data: 'e|' + id + '|acc' },
             { text: '🔁 Зустрічна', callback_data: 'e|' + id + '|cnt' }, { text: '✖️ Відхилити', callback_data: 'e|' + id + '|dec' }]];
  }
  if (!rows.length) return markup;
  return { inline_keyboard: rows.concat((markup || {}).inline_keyboard || []) };
}

// ------------------------------------------------------------------ «📦 Де посилки» (12.10)
// Посилки до нас (куплене, ще не отримане) і від нас (продане, відправлене; доставлене — ще 3 дні) → GitHub (sell.yml,
// режим parcels: eBay GetOrders + живий статус DHL) → одне повідомлення: де посилка, коли очікується, чи дійшла.
const PARCELS_BTN = { text: '📦 Де посилки', callback_data: 'c|посилки|0' };

function parcelsData_() {
  const ev = events_(), today = iso_(new Date()), rows = ledgerRows_();
  let links = [];   // 16.10: посилання на оголошення — для головного фото посилки (не вийшло — посилки й без фото)
  try { if (rows.length) links = ledger_().getSheetByName('Угоди').getRange(LEDGER_FIRST, COL.link, rows[rows.length - 1].n, 1).getValues(); }
  catch (e) { console.log('посилання: ' + e); }
  const incoming = rows.filter(function (r) { return ['Оплачено', 'В дорозі'].indexOf(r.status) >= 0; }).map(function (r) {
    const e = ev[r.n] || {};
    return { n: r.n, title: r.title, src: r.src, id: r.id, track: e.track || '', carrier: e.carrier || '', date: r.date,
      link: String((links[r.n - 1] || [''])[0] || '') };
  });
  const outgoing = rows.filter(function (r) {
    const e = ev[r.n] || {};
    return r.status === 'Продано' && e.sent && (!e.outDelivered || days_(e.outDelivered, today) <= 3);
  }).map(function (r) {
    const e = ev[r.n] || {};
    return { n: r.n, title: r.title, src: r.src, id: r.id, track: e.outTrack || '', carrier: e.outCarrier || '', delivered: e.outDelivered || '' };
  });
  return { incoming: incoming, outgoing: outgoing };
}

/** 13.10: події з GitHub (17TRACK / DHL / eBay) → як листи перевізників: статус «Отримано» і одне сповіщення. */
function parcelEvents_(list) {
  const rows = ledgerRows_();
  list.forEach(function (x) {
    const n = Number(x.n), r = rows.filter(function (y) { return y.n === n; })[0];
    if (!r || ['delivered', 'ready'].indexOf(x.kind) < 0) return;
    const date = /^\d{4}-\d\d-\d\d$/.test(String(x.date)) ? new Date(x.date + 'T12:00:00') : new Date();
    if (x.dir === 'out') { if (r.status === 'Продано') outParcel_(n, x.kind, date); }
    else if (['Оплачено', 'В дорозі'].indexOf(r.status) >= 0) inParcel_(n, x.kind, date);
  });
}

/** 13.10: сповіщення без кнопки — раз на 3 години (8–21 за Берліном) і лише коли щось у дорозі. */
function parcelsWatch_() {
  const props = PropertiesService.getScriptProperties(), now = Date.now();
  const h = Number(new Date().toLocaleString('en-GB', { timeZone: 'Europe/Berlin', hour: '2-digit', hour12: false }));
  if (!(h >= 8 && h <= 21) || now - Number(props.getProperty('PARCELS_WATCH_AT') || 0) < 3 * 3600 * 1000) return false;
  const d = parcelsData_();
  d.incoming = d.incoming.filter(function (r) { return r.track || /ebay/i.test(String(r.src || '')); });
  d.outgoing = d.outgoing.filter(function (r) { return !r.delivered; });
  if (!d.incoming.length && !d.outgoing.length) return false;
  props.setProperty('PARCELS_WATCH_AT', String(now));
  return sellDispatch_(Object.assign({ mode: 'parcels', watch: true, rt: props.getProperty('EBAY_RT') || '' }, d)) === 204;
}

/** 16.10: «🏷 Виставлено» → GitHub (sell.yml, режим listings): активні лоти eBay + рядки «Виставлено» з обліку. */
function listingsCommand_(msg) {
  if (!/^\/?(?:виставлено|мої лоти|лоти)$/i.test(String(msg.text || '').trim())) return false;
  const props = PropertiesService.getScriptProperties();
  const sh = ledger_().getSheetByName('Угоди');
  const rows = ledgerRows_().filter(function (r) { return r.status === 'Виставлено'; }).map(function (r) {
    return { n: r.n, title: r.title, status: r.status, note: String(sh.getRange(r.n + LEDGER_FIRST - 1, COL.note).getValue() || '') };
  });
  const rt = props.getProperty('EBAY_RT') || '';
  if (!rt && !rows.length) { notify_('🏷 Нічого не виставлено. Продати — «💶 Продати».'); return true; }
  const code = sellDispatch_({ mode: 'listings', rt: rt, rows: rows });
  notify_(code === 204 ? '⏳ Дивлюся виставлене — до хвилини.' : '⚠️ Не зміг запустити (GitHub ' + code + ').');
  return true;
}

function parcelsCommand_(msg) {
  if (!/^\/?(?:посилки|де посилки|трекінг|трекинг)(?:\s+0)?$/i.test(String(msg.text || '').trim())) return false;
  const d = parcelsData_();
  if (!d.incoming.length && !d.outgoing.length) {
    notify_('📦 Посилок у дорозі немає: усе куплене вже в тебе, а продане — доставлено.');
    return true;
  }
  const rt = PropertiesService.getScriptProperties().getProperty('EBAY_RT') || '';
  const code = sellDispatch_(Object.assign({ mode: 'parcels', rt: rt }, d));
  notify_(code === 204 ? '⏳ Дивлюся посилки (' + d.incoming.length + ' до тебе, ' + d.outgoing.length + ' від тебе) — до хвилини.'
                       : '⚠️ Не зміг запустити (GitHub ' + code + ').');
  return true;
}

/** «✅ Межі оновив» з тижневого звіту → GitHub записує рекомендовані межі підписок KA як поточні. */
function boundsCommand_(msg) {
  const mm = String(msg.text || '').trim().match(/^\/?межі(?:\s+(1))?$/i);   // «межі» — показати; «межі 1» (кнопка) — записати
  if (!mm) return false;
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN') || GITHUB_TOKEN;
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/ka_bounds.yml/dispatches', {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ ref: 'main', inputs: { mode: mm[1] ? 'accept' : 'show' } }) });
  notify_(r.getResponseCode() !== 204 ? '⚠️ Не зміг запустити (GitHub ' + r.getResponseCode() + ').'
    : mm[1] ? '⏳ Записую нові межі підписок KA — до хвилини.' : '⏳ Рахую межі підписок KA за сьогоднішніми цінами — до хвилини.');
  return true;
}

// ------------------------------------------------------------------ бот «Помічник» (10.10)
// Питання з Telegram (оголошення, фото, «чи вигідно за 160?») → GitHub assist.yml → Claude Code у репозиторії
// (research/assist.py) → відповідь редагує «🤔 Думаю…». Альбом фото збираємо ~15 с (підпис — на одному з фото).
const ASSIST_HELP = '🤖 Я помічник з перепродажу — знаю весь проєкт і рахую тими самими оцінювачами, що й картки.\n' +
  'Кидай посилання на оголошення KA / eBay, текст чи фото з питанням, наприклад:\n' +
  '«ПК за 160 €: DDR4 2x8 і SSD 250 — дешеві, а процесор хтось купує окремо?»\n' +
  'Відповідь — за 1–2 хвилини. Пам\'ятаю кілька останніх повідомлень; «нове» — почати розмову спочатку.\n' +
  'Нічого не змінюю і продавцям не пишу — лише рахую й раджу.';

function tgAssist_(method, payload) {
  const tok = PropertiesService.getScriptProperties().getProperty('ASSIST_BOT_TOKEN');
  if (!tok) return { code: 0, json: {} };
  const r = UrlFetchApp.fetch('https://api.telegram.org/bot' + tok + '/' + method, { method: 'post', payload: payload, muteHttpExceptions: true });
  let j = {};
  try { j = JSON.parse(r.getContentText() || '{}'); } catch (e) { j = {}; }
  return { code: r.getResponseCode(), json: j };
}

function assistMessage(msg) {
  const props = PropertiesService.getScriptProperties(), chat = props.getProperty('TELEGRAM_CHAT_ID');
  if (!chat || String(msg.chat.id) !== String(chat)) return;   // лише власник
  const urls = [].concat(msg.entities || [], msg.caption_entities || []).map(function (en) { return en.url || ''; }).filter(Boolean);
  const text = [msg.text, msg.caption].concat(urls).filter(Boolean).join(' ').trim();
  if (/^\/?(?:start|допомога|help)$/i.test(text)) { tgAssist_('sendMessage', { chat_id: chat, text: ASSIST_HELP }); return; }
  // 14.10: «отримав 3» машинально в помічника — це команда обліку, а не питання: виконуємо, відповідь — у боті обліку
  const fz = /^\S+\s+\d{1,4}(?=\s|$)/.test(text) ? fuzzyCmd_(text) : null;   // «забарв 3» — описка в команді з номером
  const asCmd = fz && fz.text ? fz.text : canonCmd_(text);
  if (/^\/?(отримав|отримала|перевірив|перевірила|проблема|відправив|відправила|трек|виставив|виставила|нагадай|нагадування)(?=\s|$)/i.test(asCmd)) {
    msg = Object.assign({}, msg, { text: asCmd });
    officeMessage(msg);
    tgAssist_('sendMessage', { chat_id: chat, text: '📒 Це команда обліку — виконав, відповідь у боті «Облік і продаж».' });
    return;
  }
  if (/^\/?(?:нове|забудь|reset|new)$/i.test(text)) {
    props.deleteProperty('ASSIST_HIST');
    tgAssist_('sendMessage', { chat_id: chat, text: '🆕 Почали з чистого аркуша.' });
    return;
  }
  const f = photoFile_(msg);
  const buf = JSON.parse(props.getProperty('ASSIST_BUF') || '{"photos":[],"q":""}');
  if (Date.now() - (buf.ts || 0) > 10 * 60000) { buf.photos = []; buf.q = ''; }   // старі фото без питання — забуваємо
  if (f) buf.photos.push(f.id);
  if (text) buf.q = (buf.q ? buf.q + '\n' : '') + text;
  buf.ts = Date.now();
  if (msg.media_group_id) {   // альбом: зачекати решту фото (підпис може бути на будь-якому)
    props.setProperty('ASSIST_BUF', JSON.stringify(buf));
    if (!ScriptApp.getProjectTriggers().some(function (t) { return t.getHandlerFunction() === 'assistFlush'; })) {
      ScriptApp.newTrigger('assistFlush').timeBased().after(15000).create();
    }
    return;
  }
  if (!buf.q) {   // лише фото — чекаємо питання
    props.setProperty('ASSIST_BUF', JSON.stringify(buf));
    tgAssist_('sendMessage', { chat_id: chat, text: '📸 Фото є (' + buf.photos.length + '). Напиши питання — або надішли ще фото.' });
    return;
  }
  props.deleteProperty('ASSIST_BUF');
  assistAsk_(buf.q, buf.photos);
}

/** Тригер через ~15 с після альбому: є питання — питаємо з усіма фото; немає — просимо написати. */
function assistFlush() {
  ScriptApp.getProjectTriggers().forEach(function (t) { if (t.getHandlerFunction() === 'assistFlush') ScriptApp.deleteTrigger(t); });
  const props = PropertiesService.getScriptProperties(), chat = props.getProperty('TELEGRAM_CHAT_ID');
  const buf = JSON.parse(props.getProperty('ASSIST_BUF') || '{"photos":[],"q":""}');
  if (!buf.photos.length && !buf.q) return;
  if (!buf.q) {
    tgAssist_('sendMessage', { chat_id: chat, text: '📸 Фото є (' + buf.photos.length + '). Напиши питання — або надішли ще фото.' });
    return;
  }
  props.deleteProperty('ASSIST_BUF');
  assistAsk_(buf.q, buf.photos);
}

function assistAsk_(q, photos) {
  const props = PropertiesService.getScriptProperties(), chat = props.getProperty('TELEGRAM_CHAT_ID');
  tgAssist_('sendChatAction', { chat_id: chat, action: 'typing' });
  const wait = tgAssist_('sendMessage', { chat_id: chat, text: '🤔 Думаю… (1–2 хв)' });
  const waitId = ((wait.json || {}).result || {}).message_id || null;
  let rows = [];
  try {
    if (props.getProperty('LEDGER_ID')) {
      rows = ledgerRows_().filter(function (r) { return r.title; }).slice(-25)
        .map(function (r) { return { n: r.n, title: String(r.title).slice(0, 70), status: r.status, spent: r.spent, sprice: r.sprice, profit: r.profit }; });
    }
  } catch (e) { console.log('assist rows: ' + e); }
  const data = { q: String(q).slice(0, 3500), photos: (photos || []).slice(0, 6), hist: JSON.parse(props.getProperty('ASSIST_HIST') || '[]'),
    rows: rows, wait: waitId, today: iso_(new Date()) };
  const sealed = seal_(data, officeToken_(props), Utilities.getUuid().replace(/-/g, ''));
  const token = props.getProperty('GITHUB_TOKEN') || GITHUB_TOKEN;
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/assist.yml/dispatches', {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ ref: 'main', inputs: sealed }) });
  if (r.getResponseCode() !== 204 && waitId) {
    tgAssist_('editMessageText', { chat_id: chat, message_id: waitId, text: '⚠️ Не зміг запустити помічника (GitHub ' + r.getResponseCode() + ').' });
  }
}

/** Із processLedger: з'явився ASSIST_BOT_TOKEN (новий бот) → вебхук на цей же вебзастосунок (?bot=assist) і вітання. */
function assistConnect_() {
  const props = PropertiesService.getScriptProperties(), tok = props.getProperty('ASSIST_BOT_TOKEN');
  if (!tok || props.getProperty('ASSIST_HOOK') === tok.slice(-8)) return;
  const main = props.getProperty('TELEGRAM_BOT_TOKEN');
  const info = JSON.parse(UrlFetchApp.fetch('https://api.telegram.org/bot' + main + '/getWebhookInfo', { muteHttpExceptions: true }).getContentText() || '{}');
  const base = String(((info || {}).result || {}).url || '').split('?')[0];
  if (!/^https:\/\/script\.google\.com\/macros\/s\/.+\/exec$/.test(base)) return;
  const r = UrlFetchApp.fetch('https://api.telegram.org/bot' + tok + '/setWebhook', { method: 'post', muteHttpExceptions: true,
    payload: { url: base + '?bot=assist', allowed_updates: '["message"]', drop_pending_updates: 'true' } });
  if (r.getResponseCode() !== 200) { console.log('assist webhook: ' + r.getResponseCode()); return; }
  props.setProperty('ASSIST_HOOK', tok.slice(-8));
  const hi = tgAssist_('sendMessage', { chat_id: props.getProperty('TELEGRAM_CHAT_ID'), text: '✅ Помічник підключений.\n\n' + ASSIST_HELP });
  if (hi.code !== 200) notify_('🤖 Бот «Помічник» підключено. Відкрий його в Telegram і натисни «Start» — він не може написати першим.');
}

function publishCommand_(n, dry) {
  const props = PropertiesService.getScriptProperties(), rt = props.getProperty('EBAY_RT');
  if (!rt) { notify_('🔑 eBay ще не підключено — напиши «ebay вхід».'); return; }
  const r = ledgerRows_().filter(function (x) { return x.n === n; })[0];
  if (!r || !r.title) { notify_('У таблиці немає №' + n + '.'); return; }
  if (!dry && ['Продано', 'Повернено', 'Скасовано', 'Виставлено'].indexOf(r.status) >= 0) {
    notify_('№' + n + ' — статус «' + r.status + '», автоматично не виставляю.'); return;
  }
  const photos = cleanPhotos_(n);
  if (!photos.length) { notify_('📸 Для №' + n + ' немає фото — надішли фото з підписом «' + n + '», потім натисни ще раз.'); return; }
  const pub = JSON.parse(props.getProperty('EBAY_PUB') || '{}'), now = Date.now();
  if (!dry && pub[n] && now - pub[n] < 15 * 60 * 1000) { notify_('⏳ №' + n + ' уже виставляється — дочекайся відповіді.'); return; }
  const title = JSON.parse(props.getProperty('SELL_TITLES') || '{}')[n] || r.title;
  const code = sellDispatch_({ mode: 'publish', dry: !!dry, row: n, title: title, cost: r.spent, photos: photos, rt: rt,
    edits: sellEdits_()[n] || null });
  if (code === 204 && !dry) { pub[n] = now; props.setProperty('EBAY_PUB', JSON.stringify(pub)); }
  notify_(code !== 204 ? '⚠️ Не зміг запустити (GitHub ' + code + ').'
    : dry ? '🧪 Перевіряю оголошення №' + n + ' на eBay (без публікації) — 1–2 хвилини.' : '⏳ Виставляю №' + n + ' на eBay — 1–2 хвилини.');
}

/** Зашифроване з GitHub (doPost: {"sealed": …}) — лише з підписом токеном бота «Облік і продаж». */
function sealedPost(s) {
  const props = PropertiesService.getScriptProperties();
  let d;
  try { d = unseal_(s, officeToken_(props)); } catch (e) { console.log('sealedPost: ' + e.message); return; }
  if (d.kind === 'ebay_rt' && d.rt) {
    props.setProperty('EBAY_RT', d.rt);
    props.setProperty('EBAY_RT_DATE', iso_(new Date()));
    const ready = ledgerRows_().filter(function (r) {
      return ['Перевірено', 'Отримано'].indexOf(r.status) >= 0 && photosOf_(r.n).length;
    })[0];
    notify_('✅ eBay підключено (ключ діє ~18 місяців).\nТепер на картці «продати N» є кнопка «🚀 Виставити на eBay автоматично».' +
            (ready ? '\n🧪 Для перевірки проганяю №' + ready.n + ' через eBay без публікації.' : ''));
    if (ready) publishCommand_(ready.n, true);
  } else if (d.kind === 'repriced' && d.row) {
    const sh = ledger_().getSheetByName('Угоди'), row = Number(d.row) + LEDGER_FIRST - 1;
    const note = String(sh.getRange(row, COL.note).getValue() || '');
    sh.getRange(row, COL.note).setValue((note ? note + '; ' : '') + 'ціна знижена до ' + d.price + ' € ' +
      Utilities.formatDate(new Date(), 'Europe/Berlin', 'dd.MM') + (d.item_id && note.indexOf(d.item_id) < 0 ? ' (eBay ' + d.item_id + ')' : ''));
  } else if (d.kind === 'assist') {   // відповідь помічника — в історію розмови (6 останніх)
    const h = JSON.parse(props.getProperty('ASSIST_HIST') || '[]');
    h.push({ q: String(d.q || '').slice(0, 400), a: String(d.a || '').slice(0, 1200) });
    props.setProperty('ASSIST_HIST', JSON.stringify(h.slice(-6)));
  } else if (d.kind === 'ebay_bad') {
    props.deleteProperty('EBAY_RT');
  } else if (d.kind === 'revised' && d.row) {
    const sh = ledger_().getSheetByName('Угоди'), row = Number(d.row) + LEDGER_FIRST - 1;
    const note = String(sh.getRange(row, COL.note).getValue() || '');
    sh.getRange(row, COL.note).setValue((note ? note + '; ' : '') + 'оновлено на eBay ' +
      Utilities.formatDate(new Date(), 'Europe/Berlin', 'dd.MM') + (d.price ? ' (' + d.price + ' €)' : ''));
    const ed = sellEdits_();
    if (ed[d.row]) { delete ed[d.row]; props.setProperty('SELL_EDITS', JSON.stringify(ed)); }
  } else if (d.kind === 'parcels' && Array.isArray(d.events)) {
    parcelEvents_(d.events);
  } else if (d.kind === 'listed' && d.row) {
    const chat = props.getProperty('TELEGRAM_CHAT_ID');
    const ed = sellEdits_();
    if (ed[d.row]) { delete ed[d.row]; props.setProperty('SELL_EDITS', JSON.stringify(ed)); }
    listedCommand_({ text: 'виставив ' + d.row + (d.price ? ' ' + d.price : ''), chat: { id: chat }, _auto: true }, d.item_id);
  }
}


// ------------------------------------------------------------------ тижневий звіт (01.10)
// Рядки обліку → зашифровано (як «продати») → GitHub weekly_report.yml → research/weekly_report.py → бот «Облік і продаж».
function iso_(v) { return Object.prototype.toString.call(v) === '[object Date]' ? Utilities.formatDate(v, 'Europe/Berlin', 'yyyy-MM-dd') : (v || ''); }
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
        profit: num_(v[COL.profit - 1]), id: String(v[COL.id - 1] || '') };
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
  const sealed = seal_({ rows: rows, today: iso_(new Date()), ebay: !!props.getProperty('EBAY_RT') }, officeToken_(props),
    Utilities.getUuid().replace(/-/g, ''));
  const token = props.getProperty('GITHUB_TOKEN') || GITHUB_TOKEN;
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/weekly_report.yml/dispatches', {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ ref: 'main', inputs: sealed }) });
  console.log('тижневий звіт: GitHub ' + r.getResponseCode());
  return r.getResponseCode() === 204;
}

// «виставив 3 124» → статус «Виставлено» (+ ціна в нотатки) — для тижневого звіту: що на продажу, а що лежить
function listedCommand_(msg, itemId) {
  const m = String(msg.text || '').trim().match(/^\/?(?:виставив|виставила)(?=\s|$)\s*(\d{1,4})?(?:\s+(\d+(?:[.,]\d{1,2})?))?/i);
  if (!m) return false;
  const props = PropertiesService.getScriptProperties();
  if (String(msg.chat.id) !== String(props.getProperty('TELEGRAM_CHAT_ID')) || !props.getProperty('LEDGER_ID')) return true;
  if (!m[1]) { notify_('Напиши так: «виставив 3 124» — номер у таблиці і ціна (ціну можна не писати).'); return true; }
  // 16.10: виставив вручну — номер оголошення з посилання («виставив 4 168 https://www.ebay.de/itm/123…») → працює «🔁 Оновити»
  const link = String(msg.text || '').match(/ebay\.[a-z.]+\/itm\/(?:[^\s\/]+\/)?(\d{9,15})|(?:^|\s)(\d{12})(?:\s|$)/i);
  if (!itemId && link) itemId = link[1] || link[2];
  const sh = ledger_().getSheetByName('Угоди');
  const row = Number(m[1]) + LEDGER_FIRST - 1;
  const title = row <= sh.getLastRow() ? sh.getRange(row, COL.title).getValue() : '';
  if (!title) { notify_('У таблиці немає №' + m[1] + '.'); return true; }
  const st = sh.getRange(row, COL.status).getValue();
  if (['Продано', 'Повернено', 'Скасовано'].indexOf(st) >= 0) { notify_('№' + m[1] + ' — статус «' + st + '», не змінюю.'); return true; }
  sh.getRange(row, COL.status).setValue('Виставлено');
  if (m[2] || itemId) {
    const note = String(sh.getRange(row, COL.note).getValue() || '');
    sh.getRange(row, COL.note).setValue((note ? note + '; ' : '') + 'виставлено' + (m[2] && !(itemId && m[2] === itemId) ? ' за ' +
      m[2].replace(',', '.') + ' €' : '') + ' ' + Utilities.formatDate(new Date(), 'Europe/Berlin', 'dd.MM') + (itemId ? ' (eBay ' + itemId + ')' : ''));
  }
  if (!link && !msg._auto) notify_('🏷 №' + m[1] + ' «' + title + '» — виставлено' + (m[2] ? ' за ' + m[2] + ' €' : '') + '. Коли продаси — «продав ' +
          (m[2] || 'ціна') + ' ' + String(title).split(' ').slice(0, 2).join(' ') + '» (або лист eBay запише сам).' +
          ' Щоб правити оголошення з бота (🔁) — додай посилання: «виставив ' + m[1] + ' ' + (m[2] || 'ціна') + ' https://www.ebay.de/itm/…».');
  else if (link) notify_('🏷 №' + m[1] + ' — виставлено, оголошення eBay ' + itemId + ' запам\'ятав: «продати ' + m[1] + '» → ✏️ правки → 🔁 Оновити.');
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
          '\nПеревірив — «перевірив ' + r.n + '», щось не так — «проблема ' + r.n + '».', btns_([['перевірив', r.n], ['проблема', r.n]]));
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
      // 13.10: коротко — строк і що зробити; «отримав N» — кнопкою під оглядом
      const tail = e.track ? ' Трек: ' + trackLink_({ num: e.track, carrier: e.carrier }) : ' Трек — «трек ' + r.n + ' номер».';
      if (r.src === 'Kleinanzeigen') {
        if (d >= 8) once(r.n, 'ka8', '⚠️ ' + name + ' — ' + d + ' дн. ' + from + ', не отримано: «Problem melden» у KA до ' +
          addDays_(base, 10) + ', на 14-й день гроші самі підуть продавцю.' + tail);
        else if (d >= 5 && !rem.ka8) once(r.n, 'ka5', '📦 ' + name + ' — ' + d + ' дн. ' + from + ', посилки ще немає: напиши продавцю ' +
          '(проблему в KA — до ' + addDays_(base, 10) + ').' + tail);
      } else {
        if (d >= 20) once(r.n, 'eb20', '⚠️ ' + name + ' — ' + d + ' дн. без доставки: «Artikel nicht erhalten» у Mein eBay → Käufe ' +
          '(до 30 днів після очікуваної доставки).' + tail);
        else if (d >= 10 && !rem.eb20) once(r.n, 'eb10', '📦 ' + name + ' — ' + d + ' дн. ' + from + ', не доставлено: напиши продавцю, ' +
          'після очікуваної дати — «Artikel nicht erhalten».' + tail);
      }
    } else if (r.status === 'Отримано') {
      if (!e.got) got.push(r.n);
      const d = e.got ? days_(e.got, today) : 0, act = ' Перевірив — «перевірив ' + r.n + '», не так — «проблема ' + r.n + '».';
      if (!rem.test0) once(r.n, 'test0', '🧪 ' + name + ' отримано — протестуй: ' + testTip_(r.cat) + '. ' + claimLine_(r, e) + act);
      else if (d >= 2) once(r.n, 'test2', '🧪 ' + name + ' отримано ' + d + ' дн. тому, ще не перевірено. ' + claimLine_(r, e) + act);
    } else if (r.status === 'Продано' && e.sent && e.outTrack && !e.outDelivered) {
      if (days_(e.sent, today) >= 7) once(r.n, 'out7', '📦 ' + name + ' — посилка покупцю йде вже ' + days_(e.sent, today) +
        ' дн. і досі не доставлена. Глянь трек: ' + trackLink_({ num: e.outTrack, carrier: e.outCarrier }) + ' .');
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

// 13.10 (крок 8): «☀️ Ранковий огляд» замість «На сьогодні» з довгими абзацами. Зверху — стан одним поглядом (гроші,
// посилки, на руках), далі «Зробити сьогодні» коротко з кнопками (щодня, поки не зроблено: протестувати, виставити,
// відправити), «Увага» — строки, що спливають (посилка не йде, захист KA / eBay; як і раніше — раз), і нагадування.
const WARN_KEYS = ['ka5', 'ka8', 'eb10', 'eb20', 'out7', 'salesup'];

function moneyNow_(rows, exps, today) {
  const sum = function (list, f) { return list.reduce(function (a, r) { return a + (Number(f(r)) || 0); }, 0); };
  const sold = rows.filter(function (r) { return r.status === 'Продано' && r.sprice != null; });
  const open = rows.filter(function (r) { return ['Продано', 'Повернено', 'Скасовано'].indexOf(r.status) < 0; });
  const received = sum(sold, function (r) { return r.net != null ? r.net : r.sprice; });
  const bought = sum(rows.filter(function (r) { return r.status !== 'Скасовано'; }), function (r) { return r.spent; });
  const month = sum(sold.filter(function (r) { return String(iso_(r.sdate)).slice(0, 7) === today.slice(0, 7); }),
    function (r) { return r.profit != null ? r.profit : r.sprice - (r.spent || 0); });
  return { cash: received - bought - sum(exps, function (x) { return x.amount; }), stock: sum(open, function (r) { return r.spent; }),
           month: month, open: open.length };
}

/** Чисте: рядки, події, сьогодні, попередження/нагадування (рядки), витрати → {text, markup} або null (нема що робити). */
function morningDigest_(rows, ev, today, warn, todos, exps, got) {
  const eur = function (x) { return (x < 0 ? '−' : x > 0 ? '+' : '') + Math.abs(Math.round(x)) + ' €'; };
  const name = function (r) { return '№' + r.n + ' «' + h_(String(r.title).slice(0, 34).trim()) + '»'; };
  const acts = [], btn = [];
  rows.forEach(function (r) {
    const e = ev[r.n] || {};
    if (r.status === 'Отримано') {
      const d = e.got ? days_(e.got, today) : 0;
      acts.push('🧪 Протестуй ' + name(r) + (d ? ' — ' + d + ' дн.' : '') +
                (r.src === 'Kleinanzeigen' ? ' · проблему в KA — до ' + addDays_(e.shipped || r.date, 10) : ''));
      btn.push(['перевірив', r.n]);
    } else if (r.status === 'Перевірено') {
      acts.push('💶 Виставити ' + name(r));
      btn.push(['продати', r.n]);
    } else if (r.status === 'Продано' && r.sdate && !e.sent && days_(r.sdate, today) <= 7) {
      const d = days_(r.sdate, today);
      acts.push('📮 Відправ ' + name(r) + ' — продано ' + (d === 0 ? 'сьогодні' : d === 1 ? 'вчора' : d + ' дн. тому'));
      btn.push(['відправив', r.n]);
    } else if (r.status === 'Проблема') {
      acts.push('🚩 ' + name(r) + ' — проблема: заявлено продавцю / eBay / KA?');
    }
  });
  if (!acts.length && !warn.length && !todos.length) return null;
  const m = moneyNow_(rows, exps, today);
  const pin = rows.filter(function (r) { return ['Оплачено', 'В дорозі'].indexOf(r.status) >= 0; }).length;
  const pout = rows.filter(function (r) { const e = ev[r.n] || {}; return r.status === 'Продано' && e.sent && !e.outDelivered; }).length;
  const out = ['☀️ <b>Ранковий огляд</b> · ' + today.slice(8, 10) + '.' + today.slice(5, 7),
    '💶 Гроші зараз ' + eur(m.cash) + ' · у товарі ' + Math.round(m.stock) + ' € · за місяць ' + eur(m.month),
    '🗂 На руках ' + m.open + (pin || pout ? ' · 📦 посилки: ' + pin + ' до тебе, ' + pout + ' від тебе' : '')];
  if (acts.length) out.push('', '<b>✅ Зробити сьогодні</b>', acts.slice(0, 8).map(function (a, i) { return (i + 1) + '. ' + a; }).join('\n') +
                            (acts.length > 8 ? '\n… ще ' + (acts.length - 8) + ' — «на руках»' : ''));
  if (warn.length) out.push('', '<b>⚠️ Увага</b>', '<blockquote>' + warn.map(h_).join('\n\n') + '</blockquote>');
  if (todos.length) out.push('', h_(todos.join('\n')));
  (got || []).forEach(function (n) { if (!btn.some(function (b) { return b[1] === n; })) btn.push(['отримав', n]); });
  const kb = btn.length ? btns_(btn.slice(0, 8)) : { inline_keyboard: [] };
  kb.inline_keyboard.push([PARCELS_BTN, { text: '📒 Облік', callback_data: 'k|облік' }]);
  return { text: out.join('\n'), markup: kb };
}

// Щодня з 10:00 за Берліном — «☀️ Ранковий огляд», лише якщо є що робити
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
        (r.status === 'Продано' && (ev[r.n].outDelivered || (ev[r.n].sent && !ev[r.n].outTrack) ||
                                    (r.sdate && days_(r.sdate, key) > 30))))) delete ev[r.n];
  });
  saveEvents_(ev);
  // протестувати / відправити — тепер щоденні пункти «Зробити»; у «Увага» лише строки, що спливають
  const warn = a.lines.filter(function (l, i) { return WARN_KEYS.indexOf(a.marks[i][1]) >= 0; });
  const waiting = a.marks.filter(function (m) { return ['ka5', 'ka8', 'eb10', 'eb20'].indexOf(m[1]) >= 0; })
    .map(function (m) { return m[0]; });   // посилка не йде → кнопка «📦 Отримав», якщо вже прийшла
  let exps = [];
  try { exps = expenseRows_(); } catch (e) { exps = []; }
  const d = morningDigest_(rows, ev, key, warn, b.lines, exps, waiting);
  if (d) notify_(d.text, d.markup, 'HTML');
  return !!d;
}

// «отримав 3» / «перевірив 3» / «проблема 3» / «відправив 3»
function statusCommand_(msg) {
  const m = String(msg.text || '').trim().match(/^\/?(отримав|отримала|перевірив|перевірила|проблема|відправив|відправила|трек)(?=\s|$)\s*(\d{1,4})?(?:\s+([\s\S]{4,80}))?/i);
  if (!m) return false;
  const props = PropertiesService.getScriptProperties();
  if (String(msg.chat.id) !== String(props.getProperty('TELEGRAM_CHAT_ID')) || !props.getProperty('LEDGER_ID')) return true;
  const cmd = m[1].toLowerCase().replace(/ла$/, 'в');
  if (!m[2]) { notify_('Напиши з номером у таблиці: «' + cmd + ' 3»' + (cmd === 'трек' ? ' і трек-номер: «трек 3 00340434…».' : '.')); return true; }
  const n = Number(m[2]), r = rowByN_(n);
  if (!r) { notify_('У таблиці немає №' + n + '.'); return true; }
  const sh = ledger_().getSheetByName('Угоди'), row = n + LEDGER_FIRST - 1, today = iso_(new Date());
  const e = events_()[n] || {}, name = '№' + n + ' «' + r.title + '»';
  const t = trackArg_(m[3]);
  if (cmd === 'відправив') {
    if (r.status !== 'Продано') { notify_(name + ' — статус «' + r.status + '», а «відправив» — для проданого.'); return true; }
    setEvents_(n, t ? { sent: today, outTrack: t.num, outCarrier: t.carrier } : { sent: today });
    notify_('📮 ' + name + ' — відправлено, більше не нагадую.' + (t ? ' Коли покупець отримає — напишу.'
      : ' Додай трек — «трек ' + n + ' номер», і я скажу, коли покупець отримає.') + shipToEbay_(n, r.title, t), t ? trackButton_(t) : null);
    return true;
  }
  if (cmd === 'трек') {
    if (!t) { notify_('Не бачу трек-номера. Напиши так: «трек ' + n + ' 00340434…» (можна з перевізником: «трек ' + n + ' hermes H100…»).'); return true; }
    if (r.status === 'Продано') {
      setEvents_(n, { outTrack: t.num, outCarrier: t.carrier, sent: e.sent || today });
      notify_('📮 ' + name + ' — трек посилки покупцю: ' + (t.carrier || '') + ' ' + t.num + '. Коли покупець отримає — напишу.' +
              shipToEbay_(n, r.title, t), trackButton_(t));
      return true;
    }
    if (['Повернено', 'Скасовано'].indexOf(r.status) >= 0) { notify_(name + ' — статус «' + r.status + '», не змінюю.'); return true; }
    setEvents_(n, { track: t.num, carrier: t.carrier, shipped: e.shipped || today });
    if (r.status === 'Оплачено') sh.getRange(row, COL.status).setValue('В дорозі');
    notify_('📦 ' + name + ' — трек ' + (t.carrier || '') + ' ' + t.num + ' записав. Листи перевізника про доставку тепер ' +
            'зараховуються самі.', trackButton_(t));
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
    notify_('✅ ' + name + ' перевірено. ' + (r.src === 'Kleinanzeigen' ? 'Тепер можна підтвердити отримання в KA. ' :
            'Залиш продавцю відгук на eBay (Mein eBay → Käufe → «Bewertung abgeben»). ') + 'Продати — «продати ' + n + '».',
            btns_([['продати', n]]));
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

/** Рядки аркуша «Витрати»: [{amount, cat}] (порожні — без суми — пропускаємо). */
function expenseRows_() {
  const sh = ledger_().getSheetByName(EXP_SHEET);
  if (!sh || sh.getLastRow() < EXP_FIRST) return [];
  return sh.getRange(EXP_FIRST, 3, sh.getLastRow() - EXP_FIRST + 1, 2).getValues()
    .filter(function (r) { return Number(r[0]); })
    .map(function (r) { return { amount: Number(r[0]), cat: String(r[1] || 'Інше') }; });
}

/** «облік» (11.10): кожен продаж — куплено → продано → заробив; разом виручка, собівартість, витрати на продаж,
 *  операційні витрати по категоріях і підсумок (скільки ще до нуля, якщо в мінусі). */
function h_(x) { return String(x == null ? '' : x).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

/** «облік» (HTML). 14.10 (крок 8): блоки через порожній рядок — гроші зверху, продажі з деталями в цитаті, товар і витрати. */
function ledgerSummary_(rows, exps) {
  const e = function (x) { return (Math.round(x * 100) / 100).toFixed(2) + ' €'; };
  const sgn = function (x) { return (x >= 0 ? '+' : '−') + e(Math.abs(x)); };
  const sold = rows.filter(function (r) { return r.status === 'Продано' && r.sprice != null; });
  const open = rows.filter(function (r) { return ['Продано', 'Повернено', 'Скасовано'].indexOf(r.status) < 0; });
  const sum = function (list, f) { return list.reduce(function (a, r) { return a + (Number(f(r)) || 0); }, 0); };
  const revenue = sum(sold, function (r) { return r.sprice; }), cost = sum(sold, function (r) { return r.spent; });
  const profit = sum(sold, function (r) { return r.profit != null ? r.profit : (r.net != null ? r.net : r.sprice) - (r.spent || 0); });
  const sellCosts = revenue - cost - profit;   // пересилка покупцю, пакування, комісія
  const expTotal = sum(exps, function (x) { return x.amount; });
  const net = profit - expTotal;
  // 11.10: гроші загалом — отримано з продажів мінус усі купівлі (без скасованих) мінус витрати
  const received = sum(sold, function (r) { return r.net != null ? r.net : r.sprice; });
  const bought = sum(rows.filter(function (r) { return r.status !== 'Скасовано'; }), function (r) { return r.spent; });
  const stock = sum(open, function (r) { return r.spent; });
  const cash = received - bought - expTotal;
  const money = ['💶 Гроші зараз: <b>' + sgn(cash) + '</b>' + (stock ? ' · з товаром на руках (' + e(stock) + ') — ' + sgn(cash + stock) : ''),
    '📊 Разом: ' + (expTotal ? sgn(profit) + ' − ' + e(expTotal) + ' = ' : '') + '<b>' + sgn(net) + '</b>' +
    (net < 0 ? ' — до нуля ще ' + e(-net) : '')];
  if (business_() && net > 0) {   // 13.10: Kleingewerbe — податок на прибуток (оцінка; точна ставка — від зарплати)
    money.push('🏛 Податок на прибуток (~' + Math.round(TAX_RATE * 100) + '%): ≈ −' + e(net * TAX_RATE) + ' → лишається ≈ ' + e(net * (1 - TAX_RATE)));
  }
  let sales = '💰 Продажів ще немає';
  if (sold.length) {
    const det = [];
    if (sellCosts > 0.005) det.push('пересилка покупцям і пакування: −' + e(sellCosts));
    det.push('заробив на товарі: ' + sgn(profit) + (cost ? ' (' + (profit >= 0 ? '+' : '') + Math.round(100 * profit / cost) + '%)' : ''));
    sold.slice().sort(function (a, b) { return String(b.sdate).localeCompare(String(a.sdate)); }).slice(0, 5).forEach(function (r) {
      const p = r.profit != null ? r.profit : r.sprice - (r.spent || 0);
      const d = r.date && r.sdate ? days_(r.date, r.sdate) : null;
      det.push('• №' + r.n + ' ' + h_(String(r.title).slice(0, 36).trim()) + ': ' + e(r.spent || 0) + ' → ' + e(r.sprice) + ' = ' + sgn(p) +
               (d != null && d >= 0 ? ' · ' + d + ' дн.' : ''));
    });
    if (sold.length > 5) det.push('… ще ' + (sold.length - 5) + ' — у таблиці');
    sales = '💰 Продано: ' + sold.length + ' шт за ' + e(revenue) + ' (куплено за ' + e(cost) + ')\n<blockquote>' + det.join('\n') + '</blockquote>';
  }
  const stockLines = ['📦 На руках: ' + open.length + ' шт, у товарі ' + e(stock)];
  if (expTotal) {
    const byCat = {};
    exps.forEach(function (x) { byCat[x.cat] = (byCat[x.cat] || 0) + x.amount; });
    stockLines.push('🧾 Витрати: ' + e(expTotal) + ' (' + Object.keys(byCat).sort(function (a, b) { return byCat[b] - byCat[a]; })
      .map(function (c) { return h_(String(c).toLowerCase()) + ' ' + e(byCat[c]); }).join(' · ') + ')');
  }
  return ['📒 <b>Облік</b>', money.join('\n'), sales, stockLines.join('\n')].join('\n\n');
}

function expenseTotal_() {
  const sh = ledger_().getSheetByName(EXP_SHEET);
  if (!sh || sh.getLastRow() < EXP_FIRST) return 0;
  return Math.round(sh.getRange(EXP_FIRST, 3, sh.getLastRow() - EXP_FIRST + 1, 1).getValues()
    .reduce(function (a, r) { return a + (Number(r[0]) || 0); }, 0) * 100) / 100;
}


// ------------------------------------------------------------------ відстеження посилок (06.10)
// Трек-номер — з листа eBay/KA «відправлено» або командою «трек N номер»; листи перевізників (DHL, Hermes, DPD, GLS, UPS)
// зараховуються лише за відомим треком — особисті посилки в тій самій пошті не чіпаємо і не записуємо в лог.
// Купівля: «зараз доставлено» → «Отримано» + як протестувати; «у відділенні» → забери. Продаж («відправив N трек»):
// «доставлено» → «✅ доставлено покупцю».
const CARRIER_QUERY = 'from:(dhl.de OR dhl.com OR deutschepost.de OR myhermes.de OR hermesworld.com OR hermes-europe.de OR ' +
  'dpd.de OR dpd.com OR gls-pakete.de OR gls-group.eu OR gls-group.com OR ups.com) newer_than:3d';
const CARRIERS = [['DHL', /\bdhl\b|deutsche post/i], ['Hermes', /hermes/i], ['DPD', /\bdpd\b/i], ['GLS', /\bgls\b/i], ['UPS', /\bups\b/i]];

function carrierOf_(text) {
  for (let i = 0; i < CARRIERS.length; i++) if (CARRIERS[i][1].test(text)) return CARRIERS[i][0];
  return '';
}
function guessCarrier_(num) {
  if (/^1Z/.test(num)) return 'UPS';
  if (/^(00340|JJD)/.test(num) || /^\d{12}$|^\d{20}$/.test(num)) return 'DHL';
  if (/^H\d{19}$/.test(num)) return 'Hermes';
  if (/^[1-9]\d{13}$/.test(num)) return 'Hermes';   // 12.10: Hermes із KA — 14 цифр (53284131401093); DPD — 14 цифр з «0»
  if (/^0\d{13}$/.test(num)) return 'DPD';
  return '';
}
/** Трек-номер із тексту листа: після «Sendungsnummer / Trackingnummer / Paketnummer …» або впізнаваний формат DHL/UPS/Hermes. */
function trackFrom_(text) {
  text = String(text || '');
  const m = text.match(/(?:sendungs(?:verfolgungs)?-?nummer|tracking-?(?:nummer|number|id|code)|paketnummer|sendungs-?id)\s*(?:lautet)?\s*[:#]?\s*([A-Z]{0,4}\d[\dA-Z]{7,34})\b/i) ||
            text.match(/\b(00340\d{15}|JJD\d{14,20}|1Z[0-9A-Z]{16}|H\d{19})\b/);
  if (!m) return null;
  const num = m[1].toUpperCase();
  return { num: num, carrier: carrierOf_(text) || guessCarrier_(num) };
}
/** «00340434…», «hermes H100…» з команди → трек. */
function trackArg_(s) {
  s = String(s || '').trim();
  const num = (s.match(/[A-Z]{0,4}\d[\dA-Z]{7,34}/i) || [])[0];
  if (!num) return null;
  return { num: num.toUpperCase(), carrier: carrierOf_(s.replace(num, '')) || guessCarrier_(num.toUpperCase()) };
}
function trackLink_(t) {
  const n = encodeURIComponent(t.num);
  return { DHL: 'https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html?piececode=' + n,
    Hermes: 'https://www.myhermes.de/empfangen/sendungsverfolgung/sendungsinformation#' + n,
    DPD: 'https://tracking.dpd.de/status/de_DE/parcel/' + n, GLS: 'https://gls-group.com/DE/de/paketverfolgung?match=' + n,
    UPS: 'https://www.ups.com/track?tracknum=' + n }[t.carrier] || 'https://parcelsapp.com/de/tracking/' + n;
}
function trackButton_(t) { return { inline_keyboard: [[{ text: '📦 Відстежити (' + (t.carrier || 'трек') + ')', url: trackLink_(t) }]] }; }

/** Що каже лист перевізника: 'ready' (чекає у відділенні/Packstation), 'delivered', 'transit' або null. */
function carrierKind_(subject, body) {
  const t = String(subject || '') + '\n' + String(body || '').slice(0, 1500);
  if (/abholbereit|zur abholung bereit|(?:liegt|wartet) .{0,60}(?:filiale|packstation|paketshop|abholstation|paketbox)|kann .{0,40}abgeholt werden|ready for pick-?up/i.test(t)) return 'ready';
  if (/wurde (?:erfolgreich )?(?:zugestellt|geliefert|abgegeben)|ist zugestellt|erfolgreich zugestellt|zugestellt am|has been delivered|was delivered/i.test(t)) return 'delivered';
  if (/unterwegs|auf dem weg|kommt (?:heute|morgen)|wird (?:heute|morgen|voraussichtlich) .{0,30}zugestellt|zustellung (?:heute|morgen)|angekündigt|voraussichtlich|in zustellung|out for delivery|on its way/i.test(t)) return 'transit';
  return null;
}

function setEvents_(n, obj) {
  const ev = events_();
  ev[n] = Object.assign(ev[n] || {}, obj);
  saveEvents_(ev);
}
function rowByTrack_(num, key) {
  const ev = events_(), want = String(num || '').toUpperCase();
  const hit = Object.keys(ev).filter(function (n) { return n !== '_' && String(ev[n][key] || '').toUpperCase() === want; })[0];
  return hit ? Number(hit) : 0;
}

function shippedNotice_(row, t) {
  const r = rowByN_(row - LEDGER_FIRST + 1);
  if (!r) return;
  notify_('🚚 №' + r.n + ' «' + r.title + '» відправлено' + (t ? ' — ' + (t.carrier || 'трек') + ' ' + t.num + '. Коли перевізник ' +
          'доставить — напишу.' : '. Трек-номера в листі немає — дасть продавець, напиши «трек ' + r.n + ' номер».'),
          btns_([['отримав', r.n]], t ? trackButton_(t).inline_keyboard[0] : null));
}

/** Посилка покупцю (продаж). */
function outParcel_(n, kind, date) {
  const r = rowByN_(n), e = events_()[n] || {}, name = '№' + n + ' «' + (r ? r.title : '') + '»';
  if (kind === 'delivered') {
    if (e.outDelivered) return 'покупцю: уже доставлено';
    setEvents_(n, { outDelivered: iso_(date) });
    notify_('✅ ' + name + ' доставлено покупцю. Залиш покупцю відгук; гроші — за графіком виплат eBay.');
    return 'покупцю: доставлено';
  }
  if (kind === 'ready' && !(e.rem || {}).outReady) {
    markEvent_(n, 'rem', 'outReady');
    notify_('📬 Посилка покупцю ' + name + ' чекає у відділенні / Packstation — покупець має її забрати.');
    return 'покупцю: у відділенні';
  }
  return 'покупцю: в дорозі';
}

/** Посилка до нас (купівля). */
function inParcel_(n, kind, date) {
  const sh = ledger_().getSheetByName('Угоди'), row = n + LEDGER_FIRST - 1, cur = sh.getRange(row, COL.status).getValue();
  const r = rowByN_(n), e = events_()[n] || {};
  if (kind === 'delivered') {
    if (['Оплачено', 'В дорозі'].indexOf(cur) < 0) return 'перевізник: доставлено (статус «' + cur + '» не міняю)';
    sh.getRange(row, COL.status).setValue('Отримано');
    setEvents_(n, { got: iso_(date) });
    gotNotice_(row);
    return 'перевізник: отримано';
  }
  if (kind === 'ready') {
    if (!(e.rem || {}).ready && r) {
      markEvent_(n, 'rem', 'ready');
      notify_('📬 №' + n + ' «' + r.title + '» чекає у відділенні / Packstation — забери (зазвичай лежить 7 днів). Забрав — «отримав ' + n + '».',
              btns_([['отримав', n]]));
    }
    return 'перевізник: у відділенні';
  }
  if (cur === 'Оплачено') {
    sh.getRange(row, COL.status).setValue('В дорозі');
    if (!e.shipped) setEvents_(n, { shipped: iso_(date) });
  }
  return 'перевізник: в дорозі';
}

/** Листи перевізників за відомими треками (купівлі, які ще йдуть, і наші посилки покупцям, ще не доставлені). */
function processCarriers_(log, done) {
  const ev = events_(), rows = ledgerRows_(), tracks = {};
  rows.forEach(function (r) {
    const e = ev[r.n] || {};
    if (e.track && ['Оплачено', 'В дорозі'].indexOf(r.status) >= 0) tracks[String(e.track).toUpperCase()] = [r.n, 'in'];
    if (e.outTrack && r.status === 'Продано' && !e.outDelivered) tracks[String(e.outTrack).toUpperCase()] = [r.n, 'out'];
  });
  const keys = Object.keys(tracks);
  if (!keys.length) return 0;   // нічого не чекаємо — пошту перевізників навіть не відкриваємо
  let n = 0;
  GmailApp.search(CARRIER_QUERY, 0, 20).forEach(function (th) {
    th.getMessages().forEach(function (m) {
      if (done[m.getId()]) return;
      const body = m.getPlainBody() || '', flat = (m.getSubject() + ' ' + body).replace(/\s+/g, '').toUpperCase();
      const hit = keys.filter(function (k) { return flat.indexOf(k) >= 0; })[0];
      if (!hit) return;   // особиста чи чужа посилка — не чіпаємо
      const kind = carrierKind_(m.getSubject(), body);
      if (!kind) return;
      const t = tracks[hit];
      const what = t[1] === 'out' ? outParcel_(t[0], kind, m.getDate()) : inParcel_(t[0], kind, m.getDate());
      log.appendRow([m.getId(), m.getDate(), m.getFrom(), m.getSubject(), what, t[0] + LEDGER_FIRST - 1, '']);
      done[m.getId()] = 1;
      n++;
    });
  });
  return n;
}


// ------------------------------------------------------------------ прибирання переписки з продавцем (06.10)
// Купівлю з KA записано → відповіді продавця по цьому оголошенню прибираються з чату (GitHub ka_reply.yml, mode=close).
// Номер оголошення — зашифровано, як і решта даних обліку: репозиторій публічний, а з номера видно, що саме куплено.
function kaAdId_(link) {
  const m = String(link || '').match(/\/s-anzeige\/(?:[^\/\s"'<>?#]+\/)?(\d{6,})/);
  return m ? m[1] : '';
}

function kaCloseDispatch_(ids, why) {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('GITHUB_TOKEN') || GITHUB_TOKEN;
  if (!token || !ids.length) return false;
  const sealed = seal_({ close: ids, why: why }, officeToken_(props), Utilities.getUuid().replace(/-/g, ''));
  try {
    const r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/ka_reply.yml/dispatches', {
      method: 'post', contentType: 'application/json', muteHttpExceptions: true,
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
      payload: JSON.stringify({ ref: 'main', inputs: { blob: sealed.blob, mac: sealed.mac, nonce: sealed.nonce, mode: 'close' } }) });
    return r.getResponseCode() === 204;
  } catch (e) {
    console.log('прибирання переписки: ' + e);
    return false;
  }
}


// ------------------------------------------------------------------ фото товару (08.10)
// Фото в бот «Облік і продаж» із підписом-номером («3») → зберігаються до рядка обліку (file_id Telegram — для бота
// безстроково), повідомлення з фото прибирається з чату, а підсумок по рядку — одним повідомленням, що оновлюється.
// Gemini читає кожне фото (один запит): наклейка пам'яті → звірка з назвою покупки; екран MemTest86 з «Errors: 0» після
// завершеного проходу → статус «Перевірено» (помилки → «Проблема»). «продати N» — альбом фото слідом за оголошенням.
const PHOTO_MAX = 10;

function photosOf_(n) { return JSON.parse(PropertiesService.getScriptProperties().getProperty('PH_' + n) || '[]'); }
function savePhotos_(n, list) { PropertiesService.getScriptProperties().setProperty('PH_' + n, JSON.stringify(list.slice(-PHOTO_MAX))); }
function tgOffice_(method, payload) {
  const tok = officeToken_(PropertiesService.getScriptProperties());
  const r = UrlFetchApp.fetch('https://api.telegram.org/bot' + tok + '/' + method, { method: 'post', payload: payload, muteHttpExceptions: true });
  let j = {};
  try { j = JSON.parse(r.getContentText() || '{}'); } catch (e) { j = {}; }
  return { code: r.getResponseCode(), json: j };
}

// u — file_unique_id (те саме фото, переслане ще раз); k — роздільність і розмір (те саме фото, вдруге завантажене з галереї)
function photoFile_(msg) {   // найбільший розмір фото або картинка, надіслана файлом
  if (msg.photo && msg.photo.length) {
    const p = msg.photo[msg.photo.length - 1];
    return { id: p.file_id, t: 'photo', u: p.file_unique_id || '', k: (p.width || 0) + 'x' + (p.height || 0) + ':' + (p.file_size || 0) };
  }
  if (msg.document && /^image\//.test(msg.document.mime_type || '')) {
    return { id: msg.document.file_id, t: 'document', u: msg.document.file_unique_id || '', k: 'd:' + (msg.document.file_size || 0) };
  }
  return null;
}

function samePhoto_(a, b) {
  return a.id === b.id || (a.u && a.u === b.u) || (a.k && a.k === b.k && !/^0x0:|:0$/.test(a.k));
}

/** Прибрати дублікати в рядку (08.10: фото, надіслані вдруге через зайве питання «до якого товару?»). → скільки лишилось. */
function cleanPhotos_(n) {
  const all = photosOf_(n), out = [];
  all.forEach(function (x) { if (!out.some(function (y) { return samePhoto_(x, y); })) out.push(x); });
  if (out.length !== all.length) savePhotos_(n, out);   // пишемо лише коли справді були дублі
  return out;
}

// Питання «до якого товару?» — одне повідомлення; прибирається, щойно фото прикріплені
function askPhotoNumber_() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('PH_ASK')) return;
  const res = tgOffice_('sendMessage', { chat_id: props.getProperty('TELEGRAM_CHAT_ID'),
    text: '📸 До якого товару це фото? Напиши номер у таблиці (наприклад «3»).' });
  props.setProperty('PH_ASK', String(((res.json || {}).result || {}).message_id || 'x'));
}
function dropPhotoAsk_() {
  const props = PropertiesService.getScriptProperties(), mid = props.getProperty('PH_ASK');
  if (!mid) return;
  props.deleteProperty('PH_ASK');
  if (mid !== 'x') tgOffice_('deleteMessage', { chat_id: props.getProperty('TELEGRAM_CHAT_ID'), message_id: Number(mid) });
}

/** Фото альбому, що прийшли раніше за підписане, — до того ж рядка, без питань. */
function flushPending_(n, mg) {
  const props = PropertiesService.getScriptProperties();
  const pend = JSON.parse(props.getProperty('PH_PENDING') || '[]');
  const mine = pend.filter(function (p) { return p.mg === mg; }), rest = pend.filter(function (p) { return p.mg !== mg; });
  if (!mine.length) return;
  if (rest.length) props.setProperty('PH_PENDING', JSON.stringify(rest)); else { props.deleteProperty('PH_PENDING'); dropPhotoAsk_(); }
  mine.forEach(function (p) { attachPhoto_(n, p.f, p.m); });
}

/** Із processLedger: альбом без жодного підпису (сусід із підписом за 2 хв так і не прийшов) → єдиний товар на руках,
 *  інакше питаємо «до якого товару?». */
function photoPendingCheck_() {
  const props = PropertiesService.getScriptProperties();
  const pend = JSON.parse(props.getProperty('PH_PENDING') || '[]');
  if (!pend.some(function (p) { return Date.now() - (p.ts || 0) > 2 * 60000; })) return;
  const open = openRows_();
  if (open.length === 1 && pend.every(function (p) { return p.mg; })) {
    props.deleteProperty('PH_PENDING');
    dropPhotoAsk_();
    pend.forEach(function (p) { props.setProperty(p.mg, String(open[0].n)); attachPhoto_(open[0].n, p.f, p.m); });
    return;
  }
  askPhotoNumber_();
}

/** «перенеси фото 1 2 [k]» — останні k (або всі) фото з №1 до №2: якщо фото потрапили не до того товару. */
function photoMoveCommand_(msg) {
  const m = String(msg.text || '').trim().match(/^\/?перенеси\s+фото\s+(\d{1,4})\s+(?:в|до|->|→)?\s*(\d{1,4})(?:\s+(\d{1,2}))?$/i);
  if (!m) return false;
  const a = Number(m[1]), b = Number(m[2]);
  if (!rowByN_(a) || !rowByN_(b) || a === b) { notify_('Напиши так: «перенеси фото 1 2» (усі) або «перенеси фото 1 2 3» (останні 3).'); return true; }
  const moved = movePhotos_(a, b, m[3] ? Number(m[3]) : null);
  notify_(moved ? '📸 Переніс ' + moved + ' фото з №' + a + ' до №' + b + '.' : 'У №' + a + ' немає фото.');
  return true;
}

function movePhotos_(a, b, k) {
  const from = cleanPhotos_(a), take = k ? from.slice(-k) : from.slice();
  if (!take.length) return 0;
  savePhotos_(a, from.slice(0, from.length - take.length));
  const to = cleanPhotos_(b);
  take.forEach(function (f) { if (!to.some(function (x) { return samePhoto_(f, x); })) to.push(f); });
  savePhotos_(b, to);
  photoSummary_(a, null, photosOf_(a).length);
  photoSummary_(b, null, to.length);
  return take.length;
}

/** 10.10, один раз: два фото альбому «2» (упаковка проданого) потрапили до №1 — переносимо назад. */
/** 11.10, один раз: продажі, записані без ціни (лист «Artikel verkauft» з «Verkauft: EUR …»), — ціну з того самого листа. */
function fixSalePrice1011_() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('SALE_PRICE_1011') === '1') return;
  const sh = ledger_().getSheetByName('Угоди');
  const n = Math.max(sh.getLastRow() - LEDGER_FIRST + 1, 0);
  const data = n ? sh.getRange(LEDGER_FIRST, 1, n, COL.id).getValues() : [];
  const empty = [];
  data.forEach(function (v, i) { if (v[COL.title - 1] && v[COL.status - 1] === 'Продано' && v[COL.sprice - 1] === '') empty.push(i); });
  if (empty.length) {
    GmailApp.search('from:ebay subject:verkauft newer_than:30d', 0, 20).forEach(function (th) {
      th.getMessages().forEach(function (m) {
        const p = parseMail_(m);
        const amount = p.total != null ? p.total : p.price;
        if (p.skip || p.kind !== 'sale' || amount == null || !empty.length) return;
        const want = words_(p.title);
        let best = -1, bs = 0;
        empty.forEach(function (i) {
          const have = words_(data[i][COL.title - 1]);
          const s = want.filter(function (w) { return have.indexOf(w) >= 0; }).length;
          if (s > bs) { bs = s; best = i; }
        });
        if (best < 0 || bs < 2) return;
        sh.getRange(LEDGER_FIRST + best, COL.sprice).setValue(amount);
        empty.splice(empty.indexOf(best), 1);
        notify_('💰 Дописав ціну продажу №' + (best + 1) + ': ' + amount + ' € — тепер прибуток рахується (напиши «облік»).');
      });
    });
  }
  props.setProperty('SALE_PRICE_1011', '1');
}

/** 11.10, один раз: скасовані купівлі (гроші повернуто) не входять у «Витрачено» в «Підсумку» — ні загалом, ні за категоріями. */
function summaryNoCancelled1011_() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('SUM_NO_CANCEL_1011') === '1') return;
  const sum = ledger_().getSheetByName('Підсумок');
  if (!sum) return;
  const R = function (c) { return "'Угоди'!" + c + '5:' + c; };
  const ex = ',' + R('K') + ',"<>Скасовано"';
  if (sum.getRange('B6').getFormula() === '=SUM(' + R('J') + ')') sum.getRange('B6').setFormula('=SUMIFS(' + R('J') + ex + ')');
  for (let r = 15; r <= 19; r++) {
    const f = sum.getRange(r, 4).getFormula();
    if (f === '=SUMIFS(' + R('J') + ',' + R('D') + ',A' + r + ')') sum.getRange(r, 4).setFormula(f.slice(0, -1) + ex + ')');
  }
  for (let r = 23; r <= 26; r++) {
    const f = sum.getRange(r, 3).getFormula();
    if (f === '=SUMIFS(' + R('J') + ',' + R('E') + ',A' + r + ')') sum.getRange(r, 3).setFormula(f.slice(0, -1) + ex + ')');
  }
  props.setProperty('SUM_NO_CANCEL_1011', '1');
}

/** 11.10 (прохання користувача): у «Підсумку» — скільки ми зараз у плюсі чи мінусі загалом: отримано з продажів мінус
 *  усі купівлі (без скасованих) мінус витрати; і те саме з урахуванням товару на руках за ціною купівлі. Один раз, під усім. */
const MONEY_FMT = '[Color10]+#,##0.00 "€";[Red]-#,##0.00 "€";0.00 "€"';
function moneyBlock1011_() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('MONEY_BLOCK_1011') === '1') return;
  const ss = ledger_(), sum = ss.getSheetByName('Підсумок');
  if (!sum) return;
  expensesSheet_(ss);
  const r = sum.getLastRow() + 2;
  sum.getRange(r, 1).setValue('💶 Гроші загалом').setFontWeight('bold').setFontSize(12);
  const rows = [
    ['Отримано з продажів (чистими, після пересилки й пакування), €', '=B8', EUR_FMT],
    ['Витрачено на купівлі (без скасованих), €', '=B6', EUR_FMT],
    ['Витрати (обладнання, пакування, пересилка), €', "=IFERROR(SUM('" + EXP_SHEET + "'!C" + EXP_FIRST + ':C),0)', EUR_FMT],
    ['Гроші зараз: плюс чи мінус, €', '=B' + (r + 1) + '-B' + (r + 2) + '-B' + (r + 3), MONEY_FMT],
    ['Товар на руках (за ціною купівлі), €', '=B7', EUR_FMT],
    ['Разом з товаром на руках (якщо продати за ціною купівлі), €', '=B' + (r + 4) + '+B' + (r + 5), MONEY_FMT]];
  rows.forEach(function (x, i) {
    sum.getRange(r + 1 + i, 1).setValue(x[0]);
    sum.getRange(r + 1 + i, 2).setFormula(x[1]).setNumberFormat(x[2]).setFontWeight('bold').setBackground('#F2F2F2');
  });
  props.setProperty('MONEY_BLOCK_1011', '1');
}

// 13.10 (крок 8): таблиця читається з першого погляду — деталі згорнуті в групи «+», кожен статус свого кольору,
// службові аркуші сховані (Розкрити: «+» над колонками; аркуші — меню «Вигляд → Приховані аркуші»). Дані й формули не чіпаємо.
const STATUS_COLORS = [['Перевірено', '#E2EFDA'], ['Виставлено', '#DDEBF7'], ['Отримано', '#FFF2CC'], ['В дорозі', '#EDEDED'],
  ['Оплачено', '#EDEDED'], ['Скасовано', '#D9D9D9']];
const DETAIL_GROUPS = [[COL.link, COL.fee], [COL.sship, COL.net]];   // F–I: посилання, ціна, пересилка, збір; O–R: продаж

function tidySheet1013_() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('TIDY_1013') === '1' || !props.getProperty('LEDGER_ID')) return;
  const ss = ledger_(), deals = ss.getSheetByName('Угоди');
  if (!deals) return;
  const cf = deals.getConditionalFormatRules();
  STATUS_COLORS.forEach(function (c) {
    cf.push(SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo(c[0]).setBackground(c[1])
      .setRanges([deals.getRange('K5:K1000')]).build());
  });
  deals.setConditionalFormatRules(cf);
  DETAIL_GROUPS.forEach(function (g) {
    deals.getRange(1, g[0], 1, g[1] - g[0] + 1).shiftColumnGroupDepth(1);
    deals.getColumnGroup(g[0], 1).collapse();
  });
  ['Лог листів', 'Налаштування'].forEach(function (n) { const sh = ss.getSheetByName(n); if (sh) sh.hideSheet(); });
  props.setProperty('TIDY_1013', '1');
}

/** 16.10, один раз: кнопка «🏷 Виставлено» на клавіатурі (Telegram оновлює її лише з новим повідомленням). */
function keyboard1016_() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('KEYBOARD_1016') === '1' || props.getProperty('KEYBOARD_1013') !== '1') {
    if (props.getProperty('KEYBOARD_1013') !== '1') props.setProperty('KEYBOARD_1016', '1');   // свіжий бот — нова клавіатура й так прийде
    return;
  }
  props.setProperty('KEYBOARD_1016', '1');
  notify_('🏷 Нова кнопка внизу — «Виставлено»: що зараз на продажу, ціна, дні, перегляди, спостерігачі й посилання на лот.', OFFICE_KEYBOARD);
}

/** 13.10, один раз: новий головний екран (Telegram оновлює постійну клавіатуру лише з новим повідомленням) + меню. */
function keyboard1013_() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('KEYBOARD_1013') === '1') return;
  props.setProperty('KEYBOARD_1013', '1');
  notify_('✨ Оновив бот, щоб було простіше:\n• унизу — головне: посилки, облік, продати, звіт, на руках\n' +
          '• «☰ Меню» — усе інше розділами, замість довгої довідки. Команди текстом працюють як раніше.', OFFICE_KEYBOARD);
  notify_(OFFICE_HELP, menuMarkup_());
}

function fixAlbum1010_() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('FIX_ALBUM_1010') === '1') return;
  props.setProperty('FIX_ALBUM_1010', '1');
  const r1 = rowByN_(1), r2 = rowByN_(2);
  if (!r1 || !r2 || ['Продано', 'Виставлено'].indexOf(r2.status) < 0 || photosOf_(1).length < 2) return;
  if (movePhotos_(1, 2, 2)) notify_('📸 Два фото з альбому «2» (упаковка) помилково потрапили до №1 — переніс до №2. ' +
    'Альбоми тепер завжди йдуть за підписом, навіть якщо фото з підписом приходить останнім.');
}

function openRows_() {
  return ledgerRows_().filter(function (r) { return ['Продано', 'Повернено', 'Скасовано'].indexOf(r.status) < 0; });
}

/** Фото → номер рядка: з підпису, з альбому (підпис лише на першому фото), єдиний товар на руках, або «чекає номера». */
function photoMessage_(msg) {
  const f = photoFile_(msg);
  if (!f) return false;
  const props = PropertiesService.getScriptProperties();
  const mg = msg.media_group_id ? 'MG_' + msg.media_group_id : '';
  let n = Number(((msg.caption || '').match(/^\s*№?\s*(\d{1,4})\b/) || [])[1] || 0);
  if (n && mg) props.setProperty(mg, String(n));
  if (!n && mg) n = Number(props.getProperty(mg) || 0);
  if (n && mg && rowByN_(n)) flushPending_(n, mg);
  if (!n && !mg) { const open = openRows_(); if (open.length === 1) n = open[0].n; }   // альбом — лише за підписом сусіда
  if (n && !rowByN_(n)) { notify_('У таблиці немає №' + n + ' — фото не зберіг.'); return true; }
  if (!n) {   // фото полежить до номера; альбом — тихо чекає сусіда з підписом (він може прийти пізніше)
    const pend = JSON.parse(props.getProperty('PH_PENDING') || '[]');
    pend.push({ f: f, m: msg.message_id, mg: mg, ts: Date.now() });
    props.setProperty('PH_PENDING', JSON.stringify(pend.slice(-PHOTO_MAX)));
    if (!mg) askPhotoNumber_();
    return true;
  }
  attachPhoto_(n, f, msg.message_id);
  return true;
}

/** Відповідь-номер на «до якого товару?» — прикріпити фото, що чекають. */
function photoNumber_(msg) {
  const m = String(msg.text || '').trim().match(/^№?\s*(\d{1,4})$/);
  if (!m) return false;
  const props = PropertiesService.getScriptProperties();
  const pend = JSON.parse(props.getProperty('PH_PENDING') || '[]');
  if (!pend.length) return false;
  const n = Number(m[1]);
  if (!rowByN_(n)) { notify_('У таблиці немає №' + n + '. Напиши інший номер.'); return true; }
  props.deleteProperty('PH_PENDING');
  dropPhotoAsk_();
  pend.forEach(function (p) { if (p.mg) props.setProperty(p.mg, String(n)); attachPhoto_(n, p.f, p.m); });
  tgOffice_('deleteMessage', { chat_id: props.getProperty('TELEGRAM_CHAT_ID'), message_id: msg.message_id });
  return true;
}

function attachPhoto_(n, f, messageId) {
  const props = PropertiesService.getScriptProperties(), chat = props.getProperty('TELEGRAM_CHAT_ID');
  const list = cleanPhotos_(n);
  tgOffice_('deleteMessage', { chat_id: chat, message_id: messageId });   // фото збережене — з чату прибираємо
  if (list.some(function (x) { return samePhoto_(f, x); })) return;     // дубль — не зберігаємо і не витрачаємо Gemini
  list.push(f);
  savePhotos_(n, list);
  let g = null;
  try { g = photoGemini_(f.id); } catch (e) { console.log('фото Gemini: ' + e); }
  if (g && g.kind === 'receipt') g.line = receiptShipped_(n, g);   // 10.10: квитанція → «відправив N трек» + подяка покупцю
  photoSummary_(n, g, list.length);
}

/** Gemini: що на фото — екран MemTest86, наклейка пам'яті чи інше. null — не вдалося. */
function photoGemini_(fileId) {
  const props = PropertiesService.getScriptProperties(), key = props.getProperty('GEMINI_API_KEY');
  if (!key) return null;
  const tok = officeToken_(props);
  const info = JSON.parse(UrlFetchApp.fetch('https://api.telegram.org/bot' + tok + '/getFile?file_id=' + encodeURIComponent(fileId),
    { muteHttpExceptions: true }).getContentText() || '{}');
  const path = ((info || {}).result || {}).file_path;
  if (!path) return null;
  const blob = UrlFetchApp.fetch('https://api.telegram.org/file/bot' + tok + '/' + path, { muteHttpExceptions: true }).getBlob();
  const prompt = 'Фото для перепродажу оперативної пам\'яті або консолі. Поверни JSON без пояснень:\n' +
    '{"kind": "memtest" (екран PassMark MemTest86) | "ram_label" (наклейка на планці/коробці пам\'яті) | "receipt" (квитанція / ' +
    'чек відправки посилки: DHL Einlieferungsbeleg, Packstation, Hermes, DPD, екран Packstation з номером) | "other",\n' +
    ' "receipt": {"track": трек-номер посилки дослівно (без пробілів) або "", "carrier": "DHL" | "Hermes" | "DPD" | "GLS" | "UPS" | ""},\n' +
    ' "memtest": {"errors": число біля «Errors:» або null, "pass_done": true якщо завершено хоча б 1 повний прохід (напис PASS, ' +
    '«Pass: 2/4» або більше, підсумковий екран), "result": "PASS" | "FAIL" | null, "ram": текст рядка RAM Config або модель пам\'яті, ' +
    '"gb": обсяг пам\'яті в ГБ або null},\n' +
    ' "label": {"brand": "", "part_number": "", "gb_per_module": число або null, "modules_visible": число або null, ' +
    '"ddr": "DDR4" | "DDR5" | "DDR3" | null, "form": "desktop" | "laptop" | null (SO-DIMM = laptop; на наклейці PC4-xxxxS/SC = laptop, ' +
    'U = desktop), "registered": true якщо RDIMM / Registered (наприклад PC4-2666V-R…), "text": головний рядок наклейки дослівно}}';
  const models = ['gemini-flash-latest', 'gemini-flash-lite-latest'];
  for (let i = 0; i < models.length; i++) {
    const r = UrlFetchApp.fetch('https://generativelanguage.googleapis.com/v1beta/models/' + models[i] + ':generateContent', {
      method: 'post', contentType: 'application/json', muteHttpExceptions: true, headers: { 'x-goog-api-key': key },
      payload: JSON.stringify({ contents: [{ parts: [{ text: prompt },
        { inline_data: { mime_type: blob.getContentType() || 'image/jpeg', data: Utilities.base64Encode(blob.getBytes()) } }] }],
        generationConfig: { temperature: 0, responseMimeType: 'application/json' } }) });
    if (r.getResponseCode() !== 200) { console.log('Gemini фото ' + models[i] + ': ' + r.getResponseCode()); continue; }
    try { return JSON.parse(JSON.parse(r.getContentText()).candidates[0].content.parts[0].text.replace(/^```(?:json)?|```$/g, '')); }
    catch (e) { console.log('Gemini фото: не JSON'); }
  }
  return null;
}

/** Що каже назва покупки про пам'ять: {ddr, form, per, modules, total, parts: [номери деталей]}. */
function titleSpec_(t) {
  t = String(t || '');
  const total = Number((t.match(/(\d{1,3})\s*gb/i) || [])[1] || 0);
  const spec = { ddr: ((t.match(/ddr\s?([345])/i) || [])[1] || ''),
    form: /so-?dimm|laptop|notebook|imac|macbook|\bpc[345]-\d+[a-z]?s\b/i.test(t) ? 'laptop' : /desktop|\budimm\b|\bdimm\b|\bpc\b/i.test(t) ? 'desktop' : '',
    parts: (t.match(/\b[A-Z0-9][A-Z0-9-]{7,}\b/g) || []).filter(function (x) { return /\d/.test(x) && /[A-Z]/.test(x); })
      .map(function (x) { return x.replace(/[^A-Z0-9]/g, ''); }) };
  const nx = t.match(/\b(\d)\s*[x×]\s*(\d{1,2})\s*gb/i), xn = t.match(/\b(\d{1,2})\s*gb\s*[x×]\s*(\d)\b/i);   // «2x16GB» / «16GB x 2»
  if (nx) { spec.modules = Number(nx[1]); spec.per = Number(nx[2]); }
  else if (xn) { spec.per = Number(xn[1]); spec.modules = Number(xn[2]); }
  spec.total = spec.modules && spec.per ? spec.modules * spec.per : total;
  return spec;
}

/** Наклейка vs назва покупки → {ok: [...], bad: [...]}. */
function labelCheck_(title, label) {
  const t = titleSpec_(title), ok = [], bad = [];
  label = label || {};
  const lp = String(label.part_number || label.text || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (lp && t.parts.some(function (p) { return p.length >= 8 && (lp.indexOf(p) >= 0 || p.indexOf(lp) >= 0); })) ok.push('модель ' + label.part_number);
  const ddr = String(label.ddr || '').replace(/\D/g, '');
  if (ddr && t.ddr) (ddr === t.ddr ? ok : bad).push('DDR' + ddr + (ddr === t.ddr ? '' : ', а купував DDR' + t.ddr));
  if (label.form && t.form) (label.form === t.form ? ok : bad).push(label.form === t.form ? (t.form === 'laptop' ? 'для ноутбука' : 'для ПК')
    : 'на наклейці ' + (label.form === 'laptop' ? 'ноутбучна (SO-DIMM)' : 'для ПК') + ', а купував ' + (t.form === 'laptop' ? 'ноутбучну' : 'для ПК'));
  const per = Number(label.gb_per_module || 0), want = t.per || (t.modules ? t.total / t.modules : 0);
  if (per && want) (per === want ? ok : bad).push(per === want ? per + ' ГБ на планку' : 'на наклейці ' + per + ' ГБ на планку, а купував ' + want + ' ГБ');
  if (label.registered) bad.push('серверна (Registered / RDIMM) — у звичайний ПК не стане');
  return { ok: ok, bad: bad };
}

/** Один підсумок по рядку: створюється раз і далі редагується (не засмічуємо чат). */
function photoSummary_(n, g, count) {
  const props = PropertiesService.getScriptProperties(), chat = props.getProperty('TELEGRAM_CHAT_ID');
  const r = rowByN_(n);
  const st = JSON.parse(props.getProperty('PHS_' + n) || '{}');
  st.lines = st.lines || {};
  if (g && g.kind === 'memtest' && g.memtest) {
    const t = g.memtest, errs = t.errors == null ? null : Number(t.errors);
    if (errs > 0 || t.result === 'FAIL') {
      st.lines.memtest = '⛔ MemTest86: <b>' + (errs || '') + ' помилок</b> — планка несправна → статус «Проблема». Як заявити — «проблема ' + n + '».';
      if (r && ['Продано', 'Повернено', 'Скасовано'].indexOf(r.status) < 0) ledger_().getSheetByName('Угоди').getRange(n + LEDGER_FIRST - 1, COL.status).setValue('Проблема');
    } else if (errs === 0 && (t.pass_done || t.result === 'PASS')) {
      const can = r && ['Оплачено', 'В дорозі', 'Отримано'].indexOf(r.status) >= 0;
      if (can) { ledger_().getSheetByName('Угоди').getRange(n + LEDGER_FIRST - 1, COL.status).setValue('Перевірено'); markEvent_(n, 'tested', iso_(new Date())); }
      st.lines.memtest = '✅ MemTest86: 0 помилок' + (t.ram ? ', ' + esc_(String(t.ram).slice(0, 60)) : '') + (can ? ' → статус «Перевірено»' : '');
    } else if (errs === 0) {
      st.lines.memtest = '⏳ MemTest86: поки 0 помилок, але прохід не завершено — пришли фото, коли внизу «Pass: 2/4» або PASS.';
    }
  } else if (g && g.kind === 'receipt' && g.line) {
    st.lines.receipt = g.line;
  } else if (g && g.kind === 'ram_label' && g.label && r) {
    const c = labelCheck_(r.title, g.label);
    st.lines.label = c.bad.length ? '⚠️ Наклейка не збігається з покупкою: ' + c.bad.map(esc_).join('; ')
      : c.ok.length ? '✅ Наклейка збігається: ' + c.ok.map(esc_).join(', ') : 'ℹ️ Наклейку прочитав (' + esc_(String(g.label.text || '').slice(0, 60)) + '), але звірити нема з чим';
  }
  const text = '📸 <b>№' + n + '</b> «' + esc_(r ? String(r.title).slice(0, 60) : '') + '» — фото: ' + count +
    ['label', 'memtest', 'receipt'].filter(function (k) { return st.lines[k]; }).map(function (k) { return '\n' + st.lines[k]; }).join('') +
    '\nПродати — «продати ' + n + '» (фото прийдуть альбомом); показати — «фото ' + n + '».';
  let done = false;
  const kb = JSON.stringify(btns_([['продати', n], ['фото', n]]));
  if (st.mid) done = tgOffice_('editMessageText', { chat_id: chat, message_id: st.mid, text: text, parse_mode: 'HTML', reply_markup: kb }).code === 200;
  if (!done) {
    const res = tgOffice_('sendMessage', { chat_id: chat, text: text, parse_mode: 'HTML', disable_web_page_preview: 'true', reply_markup: kb });
    if (st.mid) tgOffice_('deleteMessage', { chat_id: chat, message_id: st.mid });
    st.mid = ((res.json || {}).result || {}).message_id || null;
  }
  props.setProperty('PHS_' + n, JSON.stringify(st));
}

/** «фото 3» — надіслати збережені фото альбомом. */
function photoShowCommand_(msg) {
  const m = String(msg.text || '').trim().match(/^\/?фото(?=\s|$)\s*(\d{1,4})?/i);
  if (!m) return false;
  if (!m[1]) { notify_('Напиши так: «фото 3». А щоб додати фото — надішли його з підписом «3».'); return true; }
  const list = cleanPhotos_(Number(m[1]));
  if (!list.length) { notify_('До №' + m[1] + ' фото ще немає — надішли їх із підписом «' + m[1] + '».'); return true; }
  sendAlbum_(list, '📸 №' + m[1]);
  return true;
}

function sendAlbum_(list, caption) {
  const chat = PropertiesService.getScriptProperties().getProperty('TELEGRAM_CHAT_ID');
  ['photo', 'document'].forEach(function (t) {
    const part = list.filter(function (x) { return x.t === t; });
    for (let i = 0; i < part.length; i += 10) {
      const chunk = part.slice(i, i + 10);
      if (chunk.length === 1) tgOffice_(t === 'photo' ? 'sendPhoto' : 'sendDocument', { chat_id: chat, caption: caption, [t]: chunk[0].id });
      else tgOffice_('sendMediaGroup', { chat_id: chat, media: JSON.stringify(chunk.map(function (x, j) {
        return j === 0 ? { type: t, media: x.id, caption: caption } : { type: t, media: x.id }; })) });
    }
  });
}


// ------------------------------------------------------------------ кнопки в боті «Облік і продаж» (09.10)
// Кнопки виконують ті самі команди, що й текст («перевірив 3» тощо); після натискання кнопки з повідомлення зникають.
const BTN = { 'отримав': '📦 Отримав', 'перевірив': '✅ Перевірив', 'проблема': '🚩 Проблема', 'відправив': '📮 Відправив',
  'продати': '💰 Продати', 'фото': '📸 Фото', 'авто': '🚀 Виставити', 'так-авто': '✅ Виставляю', 'межі': '✅ Межі записую',
  'кроки': '📋 Покроково', 'посилки': '📦 Посилки', 'змінити': '✏️ Змінити', 'оновити': '🔁 Оновити', 'так-оновити': '✅ Оновлюю' };

/** [[команда, №], …] → inline-клавіатура (по 2 в ряд); extra — ще один ряд (наприклад, «відстежити»). */
function btns_(list, extra) {
  const rows = [];
  list.forEach(function (x, i) {
    const b = { text: BTN[x[0]] + ' №' + x[1], callback_data: 'c|' + x[0] + '|' + x[1] };
    if (i % 2 === 0) rows.push([b]); else rows[rows.length - 1].push(b);
  });
  if (extra) rows.push(extra);
  return { inline_keyboard: rows };
}

/** Натискання кнопки (doPost, ?bot=office): виконати команду, кнопки з повідомлення прибрати. */
function officeCallback(cq) {
  const props = PropertiesService.getScriptProperties(), chat = props.getProperty('TELEGRAM_CHAT_ID');
  if (ebayCallback_(cq, chat) || menuCallback_(cq, chat)) return;
  const m = String(cq.data || '').match(/^c\|([^|]+)\|(\d{1,4})$/);
  const from = cq.message && cq.message.chat ? cq.message.chat.id : '';
  tgOffice_('answerCallbackQuery', { callback_query_id: cq.id, text: m ? BTN[m[1]] + (m[1] === 'посилки' ? '' : ' №' + m[2]) : '' });
  if (!m || !chat || String(from) !== String(chat) || !BTN[m[1]]) return;
  if (['продати', 'фото', 'авто', 'кроки', 'посилки', 'змінити', 'оновити'].indexOf(m[1]) < 0) {   // дія виконана — кнопки більше не потрібні
    tgOffice_('editMessageReplyMarkup', { chat_id: chat, message_id: cq.message.message_id, reply_markup: JSON.stringify({ inline_keyboard: [] }) });
  }
  officeMessage({ text: m[1] + ' ' + m[2], chat: { id: from }, message_id: 0 });
}

/** «на руках» — усе непродане: статус, скільки днів, що зробити далі (кнопки — головна дія). */
function onHandCommand_(msg) {
  if (!/^\/?(?:на руках|склад|що на руках)$/i.test(String(msg.text || '').trim())) return false;
  const today = iso_(new Date()), ev = events_();
  const open = ledgerRows_().filter(function (r) { return ['Продано', 'Повернено', 'Скасовано'].indexOf(r.status) < 0; });
  if (!open.length) { notify_('На руках нічого — усе продано. 👍'); return true; }
  // 14.10 (крок 8): згруповано за наступною дією — що робити видно з заголовка групи, без «→ …» у кожному рядку
  const groups = [['🧪 Протестувати', ['Отримано'], 'перевірив'], ['💶 Можна продавати', ['Перевірено'], 'продати'],
    ['🚚 Ще йде до тебе', ['Оплачено', 'В дорозі'], 'отримав'], ['🏷 На eBay, чекає покупця', ['Виставлено'], ''],
    ['🚩 Проблема — заяви продавцю / eBay / KA', ['Проблема'], '']];
  const acts = [], blocks = [];
  groups.forEach(function (g) {
    const list = open.filter(function (r) { return g[1].indexOf(r.status) >= 0; });
    if (!list.length) return;
    blocks.push('<b>' + g[0] + '</b>\n<blockquote>' + list.map(function (r) {
      const d = r.date ? days_(r.date, today) : null, e = ev[r.n] || {};
      if (g[2]) acts.push([g[2], r.n]);
      return '№' + r.n + ' ' + h_(String(r.title).slice(0, 40).trim()) + (d != null ? ' · ' + d + ' дн.' : '') +
        (e.track && g[2] === 'отримав' ? ' · трек ' + h_(e.track) : '');
    }).join('\n') + '</blockquote>');
  });
  const rest = open.filter(function (r) { return !groups.some(function (g) { return g[1].indexOf(r.status) >= 0; }); });
  if (rest.length) blocks.push('<b>Інше</b>\n<blockquote>' + rest.map(function (r) { return '№' + r.n + ' ' + h_(String(r.title).slice(0, 40)) + ' · ' + h_(r.status); }).join('\n') + '</blockquote>');
  const spent = open.reduce(function (a, r) { return a + (Number(r.spent) || 0); }, 0);
  notify_('📦 На руках: ' + open.length + ' · у товарі ≈ ' + spent.toFixed(0) + ' €\n\n' + blocks.join('\n\n'),
          acts.length ? btns_(acts.slice(0, 10), [PARCELS_BTN]) : { inline_keyboard: [[PARCELS_BTN]] }, 'HTML');
  return true;
}

/** Один раз: вебхукам обох ботів дозволити натискання кнопок (callback_query); адреса й черга — без змін. */
function enableButtons_() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('WEBHOOK_CB') === '1') return;
  [props.getProperty('OFFICE_BOT_TOKEN'), props.getProperty('TELEGRAM_BOT_TOKEN')].filter(Boolean).forEach(function (tok) {
    const info = JSON.parse(UrlFetchApp.fetch('https://api.telegram.org/bot' + tok + '/getWebhookInfo', { muteHttpExceptions: true }).getContentText() || '{}');
    const url = ((info || {}).result || {}).url;
    if (!url) return;
    UrlFetchApp.fetch('https://api.telegram.org/bot' + tok + '/setWebhook', { method: 'post', muteHttpExceptions: true,
      payload: { url: url, allowed_updates: '["message","callback_query"]' } });
  });
  props.setProperty('WEBHOOK_CB', '1');
}


// ------------------------------------------------------------------ «✅ Купив (самовивіз)» на картці основного бота (09.10)
// Самовивіз готівкою: листа від KA не буде, тож облік не дізнався б про покупку. Кнопка записує рядок одразу
// (назва — з картки, ціна — з оголошення; інша — виправ у таблиці), статус «Отримано» і запускає відлік перевірки.
function tgMain_(method, payload) {
  const tok = PropertiesService.getScriptProperties().getProperty('TELEGRAM_BOT_TOKEN');
  return UrlFetchApp.fetch('https://api.telegram.org/bot' + tok + '/' + method, { method: 'post', payload: payload, muteHttpExceptions: true });
}

function cardTitle_(message) {   // назва — останній курсивний фрагмент картки (<i>…</i>)
  const t = String((message || {}).text || ''), it = ((message || {}).entities || []).filter(function (e) { return e.type === 'italic'; });
  const last = it[it.length - 1];
  return (last ? t.substr(last.offset, last.length) : t.split('\n').filter(Boolean).pop() || '').trim().slice(0, 150);
}

function mainCallback(cq) {
  const props = PropertiesService.getScriptProperties(), chat = props.getProperty('TELEGRAM_CHAT_ID');
  const m = String(cq.data || '').match(/^b\|(\d{6,})\|(\d{1,5})$/);
  const from = cq.message && cq.message.chat ? cq.message.chat.id : '';
  if (!m || !chat || String(from) !== String(chat) || !props.getProperty('LEDGER_ID')) {
    tgMain_('answerCallbackQuery', { callback_query_id: cq.id });
    return;
  }
  const id = 'ka:' + m[1];
  // кнопку з картки прибираємо (решта кнопок лишається)
  const kb = ((cq.message.reply_markup || {}).inline_keyboard || [])
    .map(function (row) { return row.filter(function (b) { return !b.callback_data; }); }).filter(function (row) { return row.length; });
  tgMain_('editMessageReplyMarkup', { chat_id: chat, message_id: cq.message.message_id, reply_markup: JSON.stringify({ inline_keyboard: kb }) });
  if (findRow_(id, null, false)) { tgMain_('answerCallbackQuery', { callback_query_id: cq.id, text: 'Уже в обліку' }); return; }
  const title = cardTitle_(cq.message) || 'покупка з KA ' + m[1];
  const row = addPurchase_({ title: title, src: 'Самовивіз Гамбург', link: 'https://www.kleinanzeigen.de/s-anzeige/' + m[1],
                             price: Number(m[2]) || null, fee: '', id: id, status: 'Отримано', note: 'з кнопки «Купив»' });
  const n = row - LEDGER_FIRST + 1;
  markEvent_(n, 'got', iso_(new Date()));
  tgMain_('answerCallbackQuery', { callback_query_id: cq.id, text: '📒 Записав №' + n });
  const stext = '📒 Записав покупку №' + n + ' (самовивіз): ' + title + ' — ' + m[2] + ' €. Заплатив інакше — виправ ціну в таблиці.\n' +
          '🧪 Протестуй: ' + testTip_(category_(title)) + '.', skb = btns_([['перевірив', n], ['проблема', n]]);
  planAfter_(notify_(stext, skb), n, title, Number(m[2]) || null, stext, skb);
}


// ------------------------------------------------------------------ листи eBay про продаж → бот (09.10)
// Питання покупця, пропозиції ціни й зустрічні, повернення / запити «не отримав», виплати. Формулювання eBay різні,
// тож тип і суть визначає Gemini (як для відповідей продавців KA); для питання — ще й чернетка відповіді німецькою
// (надсилаєш сам). Виплата → нотатка в обліку й реальна комісія eBay (чи справді 6,5%, чи 0% для приватних).
const EBAY_MAIL_QUERY = 'from:ebay newer_than:3d subject:(frage OR nachricht OR preisvorschlag OR gegenangebot OR rückgabe OR ' +
  'zurückgeben OR rücksendung OR fall OR anfrage OR auszahlung OR garantie OR "nicht erhalten" OR versandetikett) -subject:bestellbestätigung';
const EBAY_KINDS = {
  question: '❓ <b>Питання покупця</b>', message: '💬 <b>Повідомлення покупця</b>', offer: '🤝 <b>Пропозиція ціни</b>',
  counter: '🔁 <b>Зустрічна пропозиція</b>',
  return: '↩️ <b>Повернення</b>', case: '⚠️ <b>Запит / суперечка</b>', payout: '💶 <b>Виплата eBay</b>' };

function ebayMailGemini_(subject, body) {
  const key = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');
  if (!key) return null;
  const prompt = 'Лист від eBay.de користувачу (він купує і продає вживану техніку як приватна особа). Тема: "' + subject + '".\n' +
    'Поверни JSON: {"kind": "question" (покупець питає про товар до покупки) | "message" (покупець пише вже після покупки: ' +
    'коли відправка, трек, самовивіз, оплата, адреса) | "offer" (пропозиція ціни, Preisvorschlag) | "counter" ' +
    '(зустрічна пропозиція, Gegenangebot) | "return" (повернення, Rückgabe) | "case" (запит/суперечка: nicht erhalten, eBay-Garantie, ' +
    'Fall) | "payout" (виплата, Auszahlung) | "label" (користувач купив етикетку доставки, Versandetikett) | "other", "role": "seller" якщо користувач тут продавець, "buyer" якщо покупець, ' +
    '"item": назва товару або "", "who": ім\'я іншої сторони або "", "message": текст іншої сторони мовою оригіналу або "", ' +
    '"uk": суть українською в 1–2 реченнях — що зробила/відповіла ІНША сторона (не переказуй, що зробив сам користувач), ' +
    '"message_from": "them" якщо "message" написала інша сторона, "me" якщо це текст самого користувача (eBay цитує його лист), ' +
    '"amount": сума в євро (пропозиція покупця, коли користувач продавець / виплата) або null, ' +
    '"my_amount": сума, яку запропонував сам користувач, або null, "their_amount": сума, яку запропонувала / відповіла інша ' +
    'сторона (зустрічна, Gegenangebot), або null, "listing_price": ціна товару в оголошенні або null, ' +
    '"offer_status": для offer/counter — "pending" (чекає відповіді) | "accepted" (прийнято) | "declined" (відхилено) | ' +
    '"expired" (прострочено, не відповіли вчасно) | "countered" (зустрічна) | "", "deadline": строк відповіді ' +
    'текстом або "", (для label — "amount": ціна етикетки), "reply_de": для question і message — коротка ввічлива відповідь німецькою від продавця (по суті; якщо ' +
    'відповіді не знаєш — попроси уточнити), інакше ""}.\n\nЛист:\n' + String(body).slice(0, 6000);
  const models = ['gemini-flash-lite-latest', 'gemini-flash-latest'];
  for (let i = 0; i < models.length; i++) {
    const r = UrlFetchApp.fetch('https://generativelanguage.googleapis.com/v1beta/models/' + models[i] + ':generateContent', {
      method: 'post', contentType: 'application/json', muteHttpExceptions: true, headers: { 'x-goog-api-key': key },
      payload: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }], generationConfig: { temperature: 0, responseMimeType: 'application/json' } }) });
    if (r.getResponseCode() !== 200) continue;
    try { return JSON.parse(JSON.parse(r.getContentText()).candidates[0].content.parts[0].text.replace(/^```(?:json)?|```$/g, '')); }
    catch (e) { console.log('Gemini eBay: не JSON'); }
  }
  return null;
}

/** Виплата → до останнього проданого без виплати; → {n, sale, fee} або null. */
function payoutToSale_(amount) {
  const ev = events_();
  const sold = ledgerRows_().filter(function (r) { return r.status === 'Продано' && r.sprice && !(ev[r.n] || {}).payout; })
    .sort(function (a, b) { return String(b.sdate).localeCompare(String(a.sdate)); });
  if (!sold.length || !amount) return null;
  const r = sold[0];
  setEvents_(r.n, { payout: amount });
  const sh = ledger_().getSheetByName('Угоди'), row = r.n + LEDGER_FIRST - 1;
  const note = String(sh.getRange(row, COL.note).getValue() || '');
  sh.getRange(row, COL.note).setValue((note ? note + '; ' : '') + 'виплата eBay ' + amount.toFixed(2) + ' €');
  // 10.10: етикетку, куплену в eBay, eBay віднімає з виплати (155,49 − 6,19 = 149,30 при Transaktionsgebühren 0,00) — це не комісія
  const label = Number(sh.getRange(row, COL.sship).getValue()) || 0;
  return { n: r.n, title: r.title, sale: r.sprice, label: label, fee: r.sprice > 0 ? Math.max(0, r.sprice - label - amount) / r.sprice : null };
}

/** Що з пропозицією: з відповіді Gemini, інакше — за словами eBay у темі / тексті. */
function offerStatus_(g, subject) {
  const st = String(g.offer_status || '').toLowerCase();
  if (st) return st;
  const t = String(subject || '') + ' ' + String(g.message || '') + ' ' + String(g.uk || '');
  return /nicht rechtzeitig|abgelaufen|прострочен/i.test(t) ? 'expired' : /abgelehnt|відхилен/i.test(t) ? 'declined'
    : /angenommen|прийнят/i.test(t) ? 'accepted' : /gegenangebot|зустрічн/i.test(t) ? 'countered' : 'pending';
}

function ebayMailText_(g, subject, link) {
  const kindText = EBAY_KINDS[g.kind] || '📨 <b>Лист eBay</b>';
  const lines = [kindText + (g.item ? ' · <i>' + esc_(String(g.item).slice(0, 70)) + '</i>' : '')];
  const offerKind = g.kind === 'offer' || g.kind === 'counter', buyer = g.role === 'buyer';
  const eur = function (x) { return Number(x).toFixed(2).replace(/\.00$/, '') + ' €'; };
  // 10.10: свій же лист (eBay його цитує) і переказ «ви надіслали пропозицію» не показуємо — цікава відповідь і суми
  if ((g.who || g.message) && !(g.message_from === 'me' || (buyer && offerKind && g.message_from !== 'them'))) {
    lines.push((g.who ? esc_(g.who) + ': ' : '') + (g.message ? '«' + esc_(String(g.message).slice(0, 500)) + '»' : ''));
  }
  if (g.uk && !(buyer && offerKind)) lines.push('🇺🇦 ' + esc_(g.uk));
  if (offerKind && buyer) {
    const mine = Number(g.my_amount) || (Number(g.their_amount) ? null : Number(g.amount)) || null;
    const theirs = Number(g.their_amount) || null;
    const status = offerStatus_(g, subject);
    // 10.10: підсумок пропозиції — одним рядком, без «відповідай в eBay» і без ціни-старту аукціону
    if (status === 'declined' || status === 'expired' || status === 'accepted') {
      const sum = mine ? eur(mine) : '';
      return { text: lines[0] + '\n' + (status === 'accepted' ? '✅ Пропозицію ' + sum + ' прийнято — оплати в eBay (Mein eBay → Käufe).'
        : status === 'expired' ? '⌛ Пропозиція ' + sum + ' прострочена — продавець не відповів вчасно.'
        : '❌ Пропозицію ' + sum + ' відхилено.'), markup: null };
    }
    if (mine) lines.push('💶 Твоя пропозиція: <b>' + eur(mine) + '</b>');
    lines.push(theirs ? '🔁 Відповідь продавця: <b>' + eur(theirs) + '</b>' + (mine ? ' (+' + eur(theirs - mine) + ' до твоєї)' : '')
      : '⏳ Продавець ще не відповів.');
    if (Number(g.listing_price) > 5) lines.push('🏷 Ціна в оголошенні: ' + eur(g.listing_price));   // 1 € — лише старт аукціону
    lines.push(theirs ? 'Відповідай в eBay: прийняти, відхилити або своя зустрічна.' : '');
  } else if (offerKind && (g.amount || g.their_amount)) {
    lines.push('💶 Пропозиція покупця: <b>' + eur(g.amount || g.their_amount) + '</b>' +
      (Number(g.listing_price) ? ' (ціна в оголошенні ' + eur(g.listing_price) + ')' : '') +
      ' — якщо в оголошенні стоять пороги, eBay прийме / відхилить сам; інакше — кнопки нижче або «Annehmen», «Ablehnen», «Gegenangebot» в eBay.');
  }
  if (g.deadline) lines.push('⏰ Строк: ' + esc_(g.deadline));
  if (g.kind === 'return' || g.kind === 'case') {
    lines.push('Що робити: відповісти в eBay протягом 3 робочих днів, інакше eBay вирішить сам на користь покупця. ' +
      'Товар справний (ти тестував, є фото MemTest86) — напиши це й додай фото; «передумав» у приватного продавця — повернення ' +
      'можна не приймати. Не впевнений — напиши Claude.');
  }
  if ((g.kind === 'question' || g.kind === 'message') && g.reply_de) lines.push('✍️ Відповідь (натисни — скопіюється):\n<code>' + esc_(g.reply_de) + '</code>');
  if (!g.uk && !g.message) lines.push(esc_(subject));
  return { text: lines.filter(Boolean).join('\n'), markup: link ? { inline_keyboard: [[{ text: '🔗 Відкрити в eBay', url: link }]] } : null };
}

function processEbayMail_(log, done) {
  let n = 0;
  GmailApp.search(EBAY_MAIL_QUERY, 0, 15).forEach(function (th) {
    th.getMessages().forEach(function (m) {
      if (done[m.getId()] || !/ebay/i.test(m.getFrom())) return;
      done[m.getId()] = 1;
      const subject = m.getSubject() || '', body = m.getPlainBody() || '', html = m.getBody() || '';
      const g = ebayMailGemini_(subject, body) || { kind: /auszahlung/i.test(subject) ? 'payout' : /preisvorschlag/i.test(subject) ? 'offer'
        : /gegenangebot/i.test(subject) ? 'counter' : /rückgabe|zurückgeben|rücksendung/i.test(subject) ? 'return'
        : /fall|garantie|nicht erhalten/i.test(subject) ? 'case' : /versandetikett/i.test(subject) ? 'label'
        : /frage|nachricht/i.test(subject) ? 'question' : 'other' };
      let what = 'eBay: ' + g.kind;
      if (g.kind === 'other') { log.appendRow([m.getId(), m.getDate(), m.getFrom(), subject, what + ' (без повідомлення)', '', '']); return; }
      if (g.kind === 'label') {   // етикетка → «Моя пересилка покупцю» останнього проданого без неї (точний прибуток)
        const amount = Number(g.amount) || money_(((body.match(/(?:betrag|preis|gesamt|summe)[^\d]{0,40}?([\d.]{1,7},\d{2})/i) || [])[1]) || '');
        const sold = ledgerRows_().filter(function (r) { return r.status === 'Продано'; })
          .sort(function (a, b) { return String(b.sdate).localeCompare(String(a.sdate)); });
        const sh = ledger_().getSheetByName('Угоди');
        const r = sold.filter(function (x) { return sh.getRange(x.n + LEDGER_FIRST - 1, COL.sship).getValue() === ''; })[0];
        if (r && amount) sh.getRange(r.n + LEDGER_FIRST - 1, COL.sship).setValue(amount);
        notify_('🏷 Етикетка доставки: ' + (amount ? amount.toFixed(2) + ' €' : 'суму не прочитав') +
                (r && amount ? ' — записав у №' + r.n + ' «' + String(r.title).slice(0, 50) + '» (колонка «Моя пересилка покупцю»).' : ''));
        log.appendRow([m.getId(), m.getDate(), m.getFrom(), subject, 'eBay: етикетка ' + (amount || '?'), r ? r.n + LEDGER_FIRST - 1 : '', '']);
        n++;
        return;
      }
      if (g.kind === 'payout') {
        const amount = Number(g.amount) || money_(((body.match(/auszahlung[^\d]{0,120}?([\d.]{1,7},\d{2})/i) || [])[1]) || '');
        const p = payoutToSale_(amount);
        notify_('💶 <b>Виплата eBay</b>: ' + (amount ? amount.toFixed(2) + ' €' : 'суму не прочитав') +
          (p ? ' — за №' + p.n + ' «' + esc_(String(p.title).slice(0, 50)) + '» (продано за ' + Number(p.sale).toFixed(2) + ' €)' +
            (p.label ? ', мінус етикетка ' + p.label.toFixed(2) + ' €' : '') +
            (p.fee != null ? '\nРеальна комісія eBay ≈ <b>' + (p.fee * 100).toFixed(1) + '%</b>' + (p.fee < 0.02
              ? ' — комісії немає, як і закладено в розрахунки бота.'
              : ' — бот рахує без комісії (з 09.10). Скажи Claude — повернемо її в розрахунки.') : '') : ''), null, 'HTML');
        log.appendRow([m.getId(), m.getDate(), m.getFrom(), subject, 'eBay: виплата ' + (amount || '?'), p ? p.n + LEDGER_FIRST - 1 : '', '']);
        n++;
        return;
      }
      const link = ((html.match(/https:\/\/[a-z.]*ebay\.de\/[^"'\s<>]*(?:mesg|msg|contact|return|rueckgabe|casemanagement|resolution)[^"'\s<>]*/i) || [])[0] || '')
        .replace(/&amp;/g, '&');
      const t = ebayMailText_(g, subject, link);
      notify_(t.text, ebayMailButtons_(g, t.markup), 'HTML');
      log.appendRow([m.getId(), m.getDate(), m.getFrom(), subject, what, '', '']);
      n++;
    });
  });
  return n;
}


// ------------------------------------------------------------------ звіт MemTest86 файлом (09.10)
// HTML / LOG зі флешки (MemTest86 кладе туди звіт після Esc) з номером у підписі → результат точніше, ніж з фото:
// PASS / FAIL, кількість помилок, моделі планок (SPD). Далі — як із фото: «Перевірено» або «Проблема», рядок у підсумку.
function parseMemtest_(text) {
  const t = String(text || '').replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ');
  const res = (t.match(/(?:test\s+)?result\s*:?\s*(PASS|FAIL)/i) || [])[1];
  const errs = t.match(/(?:total\s+)?errors?\s*:?\s*(\d+)/i);
  const passes = t.match(/(?:passes?|durchl[äa]ufe)\s*(?:completed)?\s*:?\s*(\d+)/i);
  const parts = (t.match(/part\s*(?:no|number|nummer)\.?\s*:?\s*([A-Z0-9][A-Z0-9-]{5,})/gi) || [])
    .map(function (x) { return x.replace(/^.*?:?\s*([A-Z0-9][A-Z0-9-]{5,})$/i, '$1'); });
  if (!res && !errs) return null;
  return { kind: 'memtest', memtest: { errors: errs ? Number(errs[1]) : (res && res.toUpperCase() === 'PASS' ? 0 : null),
    result: res ? res.toUpperCase() : null, pass_done: !!(res || (passes && Number(passes[1]) >= 1)),
    ram: parts.filter(function (x, i, a) { return a.indexOf(x) === i; }).slice(0, 2).join(', ') } };
}

function memtestReport_(msg) {
  const d = msg.document;
  if (!d || !(/html|text|log/i.test(d.mime_type || '') || /\.(?:html?|log|txt)$/i.test(d.file_name || ''))) return false;
  if (!/memtest/i.test((d.file_name || '') + ' ' + (msg.caption || '')) && !/^\s*№?\s*\d{1,4}\b/.test(msg.caption || '')) return false;
  let n = Number(((msg.caption || '').match(/^\s*№?\s*(\d{1,4})\b/) || [])[1] || 0);
  if (!n) { const open = openRows_(); if (open.length === 1) n = open[0].n; }
  if (!n || !rowByN_(n)) { notify_('🧪 Звіт MemTest86 отримав — напиши номер товару в підписі до файлу (наприклад «3»).'); return true; }
  const props = PropertiesService.getScriptProperties(), tok = officeToken_(props);
  const info = JSON.parse(UrlFetchApp.fetch('https://api.telegram.org/bot' + tok + '/getFile?file_id=' + encodeURIComponent(d.file_id),
    { muteHttpExceptions: true }).getContentText() || '{}');
  const path = ((info || {}).result || {}).file_path;
  const text = path ? UrlFetchApp.fetch('https://api.telegram.org/file/bot' + tok + '/' + path, { muteHttpExceptions: true }).getContentText() : '';
  const g = parseMemtest_(text);
  if (!g) { notify_('🧪 Не знайшов у файлі результату MemTest86 (PASS / FAIL, Errors). Пришли фото екрана.'); return true; }
  tgOffice_('deleteMessage', { chat_id: props.getProperty('TELEGRAM_CHAT_ID'), message_id: msg.message_id });
  photoSummary_(n, g, photosOf_(n).length);
  return true;
}
