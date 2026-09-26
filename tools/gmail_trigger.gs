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
 *   2. Вставити свій GitHub-токен між лапками в рядку GITHUB_TOKEN нижче → зберегти (💾).
 *      (Або, якщо вмієте, у ⚙ «Налаштування проєкту» → «Властивості скрипту» → GITHUB_TOKEN.)
 *   3. Угорі вибрати функцію install → «Виконати» → дозволити доступ до Gmail (це ваш власний скрипт).
 *   4. Вибрати функцію check → «Виконати» один раз: у журналі має бути «нових листів немає» або «запущено».
 * Вимкнути: вибрати функцію uninstall → «Виконати».
 * Скрипт приватний у вашому Google-акаунті; не діліться ним, поки в ньому токен.
 */
const GITHUB_TOKEN = '';   // ← вставте токен між лапками: 'github_pat_...'
const REPO = 'Orzo777/ebay-bot';
const WORKFLOW = 'ram_mail_alert.yml';
const QUERY = 'from:noreply@kleinanzeigen.de in:anywhere newer_than:1d';

function check() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('GITHUB_TOKEN') || GITHUB_TOKEN;
  if (!token) throw new Error('Немає токена: вставте його в рядок const GITHUB_TOKEN = \'...\' (крок 2).');

  const seen = JSON.parse(props.getProperty('SEEN') || '[]');
  const ids = [];
  let fresh = 0;
  GmailApp.search(QUERY, 0, 30).forEach(function (thread) {
    thread.getMessages().forEach(function (msg) {
      ids.push(msg.getId());
      if (seen.indexOf(msg.getId()) < 0) fresh++;
    });
  });
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
    // SEEN не оновлюємо — наступної хвилини спробуємо ще раз
    throw new Error('GitHub ' + resp.getResponseCode() + ': ' + resp.getContentText());
  }
  props.setProperty('SEEN', JSON.stringify(ids.slice(0, 300)));
  console.log('запущено бота, нових листів: ' + fresh);
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
  try {
    const update = JSON.parse(e.postData.contents);
    const msg = update.message;
    if (!msg || !msg.chat) return ContentService.createTextOutput('ok');
    const text = [msg.text, msg.caption].filter(String).join(' ');
    const props = PropertiesService.getScriptProperties();
    const token = props.getProperty('GITHUB_TOKEN') || GITHUB_TOKEN;
    UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/ka_share.yml/dispatches', {
      method: 'post',
      contentType: 'application/json',
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
      payload: JSON.stringify({ ref: 'main', inputs: { text: text.slice(0, 1000), chat_id: String(Number(msg.chat.id)) } }),
      muteHttpExceptions: true,
    });
  } catch (err) {
    console.log('doPost: ' + err);
  }
  return ContentService.createTextOutput('ok');   // Telegram чекає 200, інакше повторюватиме
}

function install() {
  uninstall();
  ScriptApp.newTrigger('check').timeBased().everyMinutes(1).create();
  console.log('тригер щохвилини встановлено');
}

function uninstall() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
}
