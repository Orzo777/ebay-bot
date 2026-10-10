/**
 * Kleinanzeigen → GitHub «будильник» для бота RAM (Google Apps Script, працює у ВАШОМУ Google-акаунті).
 *
 * Що робить: щохвилини шукає в Gmail нові листи від Kleinanzeigen (включно з кошиком — ви видаляєте
 * переглянуті) і, якщо з'явився новий, одразу запускає workflow ram_mail_alert.yml на GitHub.
 * Сама оцінка («брати чи ні») і картка в Telegram — у Python-коді репозиторію; тут лише сигнал «є пошта».
 * Навіщо: розклад cron у GitHub запускав бота раз на 2,5–5 год замість кожних 15 хв.
 *
 * Установка (один раз, ~5 хв):
 *   1. https://script.google.com → «Новий проєкт» → вставити цей файл замість вмісту Code.gs.
 *   2. ⚙ «Налаштування проєкту» → «Властивості скрипту» → додати GITHUB_TOKEN = ваш GitHub-токен.
 *      НЕ вписуйте токен у рядок нижче: при оновленні файлу з репозиторію він зітреться (30.09 так і сталося).
 *   3. Угорі вибрати функцію install → «Виконати» → дозволити доступ до Gmail (це ваш власний скрипт).
 *   4. Вибрати функцію check → «Виконати» один раз: у журналі має бути «нових листів немає» або «запущено».
 * Вимкнути: вибрати функцію uninstall → «Виконати».
 * Скрипт приватний у вашому Google-акаунті; не діліться ним, поки в ньому токен.
 */
const GITHUB_TOKEN = '';   // лише запасний варіант — токен тримайте у «Властивостях скрипту» (крок 2)
const VER_CODE = '2026-10-10a';   // версія файлу: сторож порівнює з GitHub і нагадує оновити (при зміні файлу — підняти)
const REPO = 'Orzo777/ebay-bot';
const WORKFLOW = 'ram_mail_alert.yml';
// Лише листи, новіші за останній оброблений (after: у секундах). 01.10: давній запит «newer_than:1d» + getMessages()
// читав сотні листів щохвилини (KA складає сповіщення однієї підписки в одну розмову) — Gmail-ліміт Google
// вичерпався о 5:00, і до вечора бот не отримував сигналу про нові листи.
const QUERY = 'from:noreply@kleinanzeigen.de in:anywhere';

function check() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('GITHUB_TOKEN') || GITHUB_TOKEN;
  if (!token) throw new Error('Немає токена: додайте GITHUB_TOKEN у ⚙ «Властивості скрипту» (крок 2).');

  const last = Number(props.getProperty('LAST_MAIL_TS')) || Date.now() - 3600e3;   // мс найновішого вже переданого листа
  let newest = last, fresh = 0;
  GmailApp.search(QUERY + ' after:' + (Math.floor(last / 1000) - 120), 0, 20).forEach(function (thread) {
    const t = thread.getLastMessageDate().getTime();
    if (t > last) { fresh++; newest = Math.max(newest, t); }
  });
  props.setProperty('LAST_CHECK_OK', String(Date.now()));   // для сторожа: пошук у Gmail працює
  if (!fresh) {
    console.log('нових листів немає');
    return;
  }

  const resp = UrlFetchApp.fetch(
    'https://api.github.com/repos/' + REPO + '/actions/workflows/' + WORKFLOW + '/dispatches', {
      method: 'post',
      contentType: 'application/json',
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
      payload: JSON.stringify({ ref: 'main' }),
      muteHttpExceptions: true,
    });
  if (resp.getResponseCode() !== 204) {
    // LAST_MAIL_TS не оновлюємо — наступної хвилини спробуємо ще раз
    throw new Error('GitHub ' + resp.getResponseCode() + ': ' + resp.getContentText());
  }
  props.setProperty('LAST_MAIL_TS', String(newest));
  console.log('запущено бота, нових розмов: ' + fresh);
}

/**
 * «Поділитися → бот» (вебхук Telegram). Telegram надсилає сюди кожне повідомлення, яке ви пишете боту
 * або яким ділитесь з ним (кнопка «Teilen» в оголошенні Kleinanzeigen). Скрипт одразу запускає GitHub
 * workflow ka_share.yml, той оцінює оголошення й відповідає карткою в Telegram (~30 с).
 * Відповідь іде лише у ВАШ чат: GitHub звіряє chat.id із секретом TELEGRAM_CHAT_ID.
 *
 * Розгортання (один раз): «Розгорнути» → «Нове розгортання» → тип «Вебзастосунок» →
 * «Виконувати від імені: я», «Хто має доступ: будь-хто» → «Розгорнути» → скопіювати адресу …/exec.
 */
function doPost(e) {
  // Apps Script відповідає Telegram перенаправленням 302, і Telegram повторює те саме повідомлення
  // ще 5–7 разів (26.09: 7 однакових карток). Тому кожен update_id обробляємо лише раз.
  const lock = LockService.getScriptLock();
  try {
    lock.waitLock(10000);
    const update = JSON.parse(e.postData.contents);
    // 09.10: зашифроване з GitHub (ключ eBay, «виставлено») — не Telegram; перевіряє підпис sealedPost у ledger.gs
    if (update.sealed) {
      if (typeof sealedPost === 'function') sealedPost(update.sealed);
      return HtmlService.createHtmlOutput('ok');
    }
    // Другий бот «Облік і продаж» (ledger.gs) приходить сюди ж з адресою …/exec?bot=office; у кожного бота свої update_id
    const office = !!(e.parameter && e.parameter.bot === 'office');
    const assist = !!(e.parameter && e.parameter.bot === 'assist');   // 10.10: бот «Помічник» (ledger.gs, assistMessage)
    const seenKey = assist ? 'TG_SEEN_ASSIST' : office ? 'TG_SEEN_OFFICE' : 'TG_SEEN';
    const props = PropertiesService.getScriptProperties();
    const seen = JSON.parse(props.getProperty(seenKey) || '[]');
    if (seen.indexOf(update.update_id) >= 0) return HtmlService.createHtmlOutput('ok');
    seen.push(update.update_id);
    props.setProperty(seenKey, JSON.stringify(seen.slice(-200)));
    if (assist) {
      if (update.message && update.message.chat && typeof assistMessage === 'function') assistMessage(update.message);
      return HtmlService.createHtmlOutput('ok');
    }
    if (update.callback_query) {   // натискання кнопки (09.10): бот «Облік і продаж»
      if (office && typeof officeCallback === 'function') officeCallback(update.callback_query);
      else if (!office && typeof mainCallback === 'function') mainCallback(update.callback_query);   // «✅ Купив (самовивіз)»
      return HtmlService.createHtmlOutput('ok');
    }
    const msg = update.message;
    if (!msg || !msg.chat) return HtmlService.createHtmlOutput('ok');
    if (office) {
      if (typeof officeMessage === 'function') officeMessage(msg);
      return HtmlService.createHtmlOutput('ok');
    }
    // Команди обліку («купив 45 OWC …», «продав 110 OWC», «облік») обробляє ledger.gs — у GitHub не пересилаємо
    if (typeof sellCommand_ === 'function' && sellCommand_(msg)) return HtmlService.createHtmlOutput('ok');   // відповідь — у бот обліку
    // «звіт» / «виставив 3» / «отримав 3» / «нагадай …» в основному боті — теж команди обліку (а не посилання для оцінки)
    if (typeof officeMessage === 'function' && /^\/?(звіт|виставив|виставила|отримав|отримала|перевірив|перевірила|проблема|відправив|відправила|трек|нагадай|нагадування)(?=\s|$)/i.test(String(msg.text || '').trim())) {
      officeMessage(msg);
      return HtmlService.createHtmlOutput('ok');
    }
    if (typeof ledgerCommand === 'function' && ledgerCommand(msg)) return HtmlService.createHtmlOutput('ok');
    // Посилання буває сховане «під словом» (text_link) або в підписі до фото — збираємо все
    const urls = [].concat(msg.entities || [], msg.caption_entities || [])
      .map(function (en) { return en.url || ''; }).filter(Boolean);
    const text = [msg.text, msg.caption].concat(urls).filter(Boolean).join(' ') || '(без тексту)';
    const token = props.getProperty('GITHUB_TOKEN') || GITHUB_TOKEN;
    const resp = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/ka_share.yml/dispatches', {
      method: 'post',
      contentType: 'application/json',
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
      payload: JSON.stringify({ ref: 'main', inputs: { text: text.slice(0, 1000), chat_id: String(Number(msg.chat.id)) } }),
      muteHttpExceptions: true,
    });
    if (resp.getResponseCode() !== 204) console.log('GitHub ' + resp.getResponseCode() + ': ' + resp.getContentText());
  } catch (err) {
    console.log('doPost: ' + err);
  } finally {
    lock.releaseLock();
  }
  // HtmlService, а НЕ ContentService: ContentService відповідає 302 (перенаправлення), Telegram вважає це
  // помилкою і безкінечно повторює найстаріше повідомлення, а нові стоять у черзі (26.09: бот «мовчав»).
  return HtmlService.createHtmlOutput('ok');
}

function install() {
  uninstall();
  ScriptApp.newTrigger('check').timeBased().everyMinutes(1).create();
  console.log('тригер щохвилини встановлено');
}

function uninstall() {
  // лише свій тригер: облік (ledger.gs) і фільтр скаму (kafilter.gs) мають власні
  ScriptApp.getProjectTriggers().forEach(function (t) { if (t.getHandlerFunction() === 'check') ScriptApp.deleteTrigger(t); });
}
