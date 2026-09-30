/**
 * Фільтр шахрайських відповідей Kleinanzeigen у Gmail (Google Apps Script, той самий проєкт).
 *
 * Картки бота відсіюють шахраїв за оголошенням, але частина пише вже у відповідь: «Haben Sie PayPal?»,
 * «PayPal Freunde», «schreib mir auf WhatsApp», «Überweisung» (30.09: ~9 з 10 відповідей). Цей скрипт щохвилини
 * переглядає листи-сповіщення KA про нові повідомлення і такі — позначає міткою «KA скам» і переносить у Кошик
 * (Gmail зберігає Кошик 30 днів — якщо фільтр помилився, лист можна повернути: мітка «KA скам» у лівому меню).
 * Сам чат у Kleinanzeigen не зачіпається — лише лист на пошті.
 *
 * Установка: «+» → «Скрипт» → kafilter → вставити цей файл → 💾 → функція installKaFilter → «Виконати» → дозволити.
 * Перевірити без видалення: функція kaFilterDryRun → «Виконати» → у журналі список, що було б прибрано.
 */

const KA_MSG_QUERY = 'from:kleinanzeigen newer_than:3d subject:(nachricht OR antwort OR anfrage OR geschrieben OR schrieb) ' +
  '-subject:(gekauft OR verkauft OR bestellung OR zahlung OR versand OR Suchauftrag) -label:"KA скам"';

// Оплата без захисту покупця, контакт поза KA, передоплата — те саме, що блокує бот в описах оголошень
const KA_SCAM = new RegExp([
  'paypal\\s*[-.:,/]?\\s*(?:an\\s+)?(?:freunde|friends|familie|family|f\\s*&\\s*f|fnf|ff\\b|privat)',
  'freunde\\s*(?:und|&|\\+)\\s*familie', 'familie\\s*(?:und|&)\\s*freunde', 'friends\\s*(?:and|&)\\s*family',
  '\\bf\\s?&\\s?f\\b', '\\bfnf\\b', 'whats\\s?app', 'wa\\.me', '\\btelegram\\b', '\\bsignal\\b', '\\bthreema\\b',
  '[üu]e?berweisung', 'vorkasse', 'echtzeit', '\\bwero\\b', 'paypal\\.me', 'anzahlung', 'kaution', 'treuhand',
  'zahlungslink', 'zahlungs-?link',
  '(?:\\+49|0049|\\b0)\\s?1[5-7]\\d[\\s/-]?\\d{3,}',
  '[\\w.+-]+@(?:gmail|gmx|web|yahoo|outlook|hotmail|icloud|t-online|aol|mail)\\.',
  '(?:haben|hast)\\s+(?:sie|du)\\s+(?:auch\\s+)?paypal', 'paypal\\s*\\?'].join('|'), 'i');
// «PayPal» просто так — шахрайське, якщо поруч немає «Waren und Dienstleistungen» / «Käuferschutz» / «Sicher bezahlen»
const KA_PAYPAL = /paypal/i;
const KA_SAFE = /waren\s*(?:und|&)\s*dienstleistung|käuferschutz|kaeuferschutz|sicher\s+bezahlen|direkt\s+kaufen/i;

function kaScamReason_(text) {
  const m = text.match(KA_SCAM);
  if (m) return m[0];
  // «pay .Pal», «Whats App», «W.h.a.t.s.A.p.p» — шахраї розбивають слова, щоб обійти фільтри (30.09: «haben Sie pay .Pal?»)
  const sq = String(text).toLowerCase().replace(/[^a-zäöüß0-9&+]/g, '');
  const sm = sq.match(/whatsapp|telegram|threema|freunde(?:und|&|\+)familie|friends(?:and|&)family|paypal(?:ff|fnf|f&f|freunde|friends)/);
  if (sm) return sm[0];
  if ((KA_PAYPAL.test(text) || /paypal/.test(sq)) && !KA_SAFE.test(text)) return 'PayPal без «Waren und Dienstleistungen»';
  return '';
}

// Лише текст повідомлення, без шаблонної «шапки»/«підвалу» KA (там можуть бути слова на кшталт «Sicher bezahlen»)
function kaMessageText_(body) {
  return String(body || '').replace(/\s+/g, ' ')
    .replace(/(?:Sicherheitshinweis|Sicherheitstipps?|Tipps? für (?:sicheres|deine Sicherheit))[\s\S]*$/i, '');
}

function kaFilter_(dry) {
  let label = GmailApp.getUserLabelByName('KA скам') || GmailApp.createLabel('KA скам');
  let n = 0;
  GmailApp.search(KA_MSG_QUERY, 0, 50).forEach(function (th) {
    const msgs = th.getMessages();
    const last = msgs[msgs.length - 1];
    const reason = kaScamReason_(kaMessageText_(last.getPlainBody()));
    if (!reason) return;
    n++;
    console.log((dry ? '[перевірка] ' : '') + 'скам (' + reason + '): ' + last.getSubject());
    if (!dry) {
      th.addLabel(label);
      th.moveToTrash();
    }
  });
  console.log(n ? 'прибрано: ' + n : 'шахрайських відповідей немає');
}

function kaFilter() { kaFilter_(false); }
function kaFilterDryRun() { kaFilter_(true); }

function installKaFilter() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'kaFilter') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('kaFilter').timeBased().everyMinutes(1).create();
  kaFilterDryRun();
  console.log('фільтр увімкнено (щохвилини)');
}
