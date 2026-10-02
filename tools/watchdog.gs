/**
 * Сторож ботів (Google Apps Script, той самий проєкт, що й Code.gs / ledger.gs). Пише в бот «Облік і продаж».
 *
 * Кожні 10 хв:
 *  • eBay-сторож (ebay_watch.yml) не працює >20 хв → сам перезапускає; якщо падає двічі поспіль — лише повідомляє;
 *  • будь-який workflow у GitHub завершився збоєм → посилання на нього;
 *  • від Kleinanzeigen немає листів довше 6 год (вдень) → попередження; листи є, а бот їх не обробив → попередження;
 *  • токен GitHub прострочений, зник тригер (check / kaFilter / processLedger) → попередження.
 * Щоранку о 9:00 за Берліном — запускає daily_report.yml: «✅ Все працює» з лічильниками за добу.
 * Якщо зранку звіту немає — зламалось щось у самому Apps Script: відкрий «Виконання» в проєкті.
 * Уночі (23:00–8:00) повідомлення приходять без звуку.
 *
 * Установка: «+» → «Скрипт» → watchdog → вставити цей файл → 💾 → функція installWatchdog → «Виконати».
 * Поріг «немає листів KA» можна змінити властивістю скрипту WD_KA_HOURS (за замовчуванням 6).
 */

const VER_WATCHDOG = '2026-10-02c';   // версія файлу: сторож порівнює з GitHub і нагадує оновити (при зміні файлу — підняти)
const WD_BAD = ['failure', 'timed_out', 'startup_failure'];
const WD_ACTIVE = ['queued', 'in_progress', 'waiting', 'requested', 'pending'];
const WD_KA_QUERY = 'from:noreply@kleinanzeigen.de in:anywhere newer_than:3d ' +
  '-subject:(nachricht OR antwort OR anfrage OR geschrieben OR schrieb)';
const WD_TRIGGERS = { check: 'пошта KA → бот', kaFilter: 'фільтр скаму', processLedger: 'облік' };
const WD_FLAKY = ['ram-mail-alert'];   // разовий збій Gmail IMAP («System Error») — лише якщо двічі поспіль
const WD_NAMES = { 'ebay-watch': 'eBay-сторож', 'ram-mail-alert': 'KA-пошта', 'ka-share': '«поділитися»',
  'tests': 'тести', 'daily-report': 'щоденний звіт', 'sell': '«продати»', 'weekly-report': 'тижневий звіт', 'price-refresh': 'сторож цін', 'ka-reply': 'відповідь продавця' };

function wdBerlinHour_(now) { return Number(Utilities.formatDate(now, 'Europe/Berlin', 'H')); }

function wdGh_(path, method, payload) {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('GITHUB_TOKEN') || GITHUB_TOKEN;
  if (!token) throw new Error('GH_AUTH немає');   // 30.09: токен був у рядку Code.gs і зник після оновлення файлу
  const opt = { method: method || 'get', muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' } };
  if (payload) { opt.contentType = 'application/json'; opt.payload = JSON.stringify(payload); }
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + path, opt);
  const code = r.getResponseCode();
  if (code === 401 || code === 403) throw new Error('GH_AUTH ' + code);
  // fine-grained токен має термін дії (за замовчуванням 30 днів) — GitHub віддає дату в кожній відповіді
  const h = r.getHeaders() || {};
  const exp = h['github-authentication-token-expiration'] || h['GitHub-Authentication-Token-Expiration'];
  if (exp) wdGh_.expires = String(exp);
  return { code: code, json: code === 200 ? JSON.parse(r.getContentText()) : null };
}

// «2026-10-23 10:00:00 UTC» → попередження за 7 днів (раз на добу), щоб картки KA не зупинились раптово
function wdTokenExpiry_(st, now, msgs) {
  if (!wdGh_.expires) return;
  const d = new Date(wdGh_.expires.replace(' UTC', 'Z').replace(' ', 'T'));
  const days = Math.floor((d.getTime() - now.getTime()) / 86400e3);
  if (isNaN(days) || days > 7) return;
  wdOnce_(st, 'gh_expiry', 24, '🔑 GitHub-токен в Apps Script закінчується ' + Utilities.formatDate(d, 'Europe/Berlin', 'dd.MM') +
    (days <= 0 ? ' (сьогодні!)' : ' (через ' + days + ' дн.)') + '. Після цього картки з Kleinanzeigen і «поділитися» зупиняться.\n' +
    'GitHub → Settings → Developer settings → Fine-grained tokens → твій токен → «Regenerate token» (термін — 1 рік) → ' +
    'новий токен у ⚙ «Властивості скрипту» → GITHUB_TOKEN.', msgs, now);
}

/** Повідомлення з ключем — не частіше, ніж раз на `hours` год. */
function wdOnce_(st, key, hours, text, msgs, now) {
  st.alerts = st.alerts || {};
  if (st.alerts[key] && now.getTime() - st.alerts[key] < hours * 3600e3) return;
  st.alerts[key] = now.getTime();
  msgs.push(text);
}

function wdEbay_(st, now, msgs) {
  const runs = (wdGh_('/actions/workflows/ebay_watch.yml/runs?per_page=5').json || {}).workflow_runs || [];
  if (!runs.length || runs.some(function (r) { return WD_ACTIVE.indexOf(r.status) >= 0; })) return;
  const idle = Math.round((now.getTime() - new Date(runs[0].updated_at).getTime()) / 60000);
  if (idle < 20) return;
  if (runs.length >= 2 && WD_BAD.indexOf(runs[0].conclusion) >= 0 && WD_BAD.indexOf(runs[1].conclusion) >= 0) {
    wdOnce_(st, 'ebay_crash', 6, '⛔ eBay-сторож падає вже двічі поспіль — сам не перезапускаю, треба глянути:\n' +
            runs[0].html_url, msgs, now);
    return;
  }
  if (st.ebayRestart && now.getTime() - st.ebayRestart < 30 * 60000) return;
  st.ebayRestart = now.getTime();
  const r = wdGh_('/actions/workflows/ebay_watch.yml/dispatches', 'post', { ref: 'main' });
  if (r.code === 204) wdOnce_(st, 'ebay_restart', 3, '🔁 eBay-сторож стояв ' + idle + ' хв — перезапустив.', msgs, now);
  else wdOnce_(st, 'ebay_dispatch', 12, '⚠️ eBay-сторож стоїть ' + idle + ' хв, перезапустити не вдалось (GitHub ' + r.code +
               (r.code === 422 ? ': workflow вимкнений' : '') + ').', msgs, now);
}

function wdFailures_(st, now, msgs) {
  const runs = (wdGh_('/actions/runs?per_page=50').json || {}).workflow_runs || [];
  st.failSeen = st.failSeen || [];
  const lines = [];
  runs.forEach(function (r, i) {
    if (r.status !== 'completed' || WD_BAD.indexOf(r.conclusion) < 0 || st.failSeen.indexOf(r.id) >= 0) return;
    if (WD_FLAKY.indexOf(r.name) >= 0) {
      const prev = runs.slice(i + 1).filter(function (p) { return p.name === r.name && p.status === 'completed' && p.conclusion !== 'cancelled'; })[0];
      const next = runs.slice(0, i).filter(function (p) { return p.name === r.name && p.status === 'completed' && p.conclusion !== 'cancelled'; }).pop();
      if (next && next.conclusion === 'success') { st.failSeen.push(r.id); return; }   // наступний запуск пройшов
      if (!prev || WD_BAD.indexOf(prev.conclusion) < 0) return;                       // поки один — чекаємо наступного
    }
    st.failSeen.push(r.id);
    if (now.getTime() - new Date(r.updated_at).getTime() > 3 * 3600e3) return;   // давні збої не ворушимо
    lines.push('• ' + (WD_NAMES[r.name] || r.name) + (r.conclusion === 'startup_failure' ? ' (не стартував)' : '') + ': ' + r.html_url);
  });
  st.failSeen = st.failSeen.slice(-150);
  if (lines.length) msgs.push('⚠️ Збій у GitHub:\n' + lines.slice(0, 5).join('\n') +
                              (lines.length > 5 ? '\n… ще ' + (lines.length - 5) : ''));
}

function wdKaMail_(st, now, msgs) {
  const props = PropertiesService.getScriptProperties();
  const limit = Number(props.getProperty('WD_KA_HOURS')) || 6;
  const th = GmailApp.search(WD_KA_QUERY, 0, 1);
  const last = th.length ? th[0].getLastMessageDate() : null;
  const ageH = last ? (now.getTime() - last.getTime()) / 3600e3 : 72;
  const h = wdBerlinHour_(now);
  if (ageH > limit && h >= 9 && h < 23 && !st.kaStale) {
    st.kaStale = true;
    msgs.push('📭 Від Kleinanzeigen немає листів ' + (ageH >= 72 ? 'понад 3 доби' : Math.round(ageH) + ' год') +
              '. Так уже було 23–25.09, коли стояв застосунок KA. Перевір: застосунок Kleinanzeigen не встановлено; ' +
              'пошукові підписки є і в них увімкнено «E-Mail»; листи не в «Спамі».');
  } else if (ageH <= limit && st.kaStale) {
    st.kaStale = false;
    msgs.push('📬 Листи від Kleinanzeigen знову приходять.');
  }
  // лист є, а бот його не обробив: після листа не було жодного запуску ram_mail_alert
  if (!last || now.getTime() - last.getTime() < 15 * 60000) return;
  const runs = (wdGh_('/actions/workflows/ram_mail_alert.yml/runs?per_page=1').json || {}).workflow_runs || [];
  const ran = runs.length && new Date(runs[0].created_at).getTime() >= last.getTime() - 60000;
  if (!ran && !st.kaUnproc) {
    st.kaUnproc = true;
    msgs.push('⚠️ Лист від Kleinanzeigen прийшов о ' + Utilities.formatDate(last, 'Europe/Berlin', 'HH:mm') +
              ', але бот його не обробив. Відкрий Apps Script → «Виконання»: чи працює функція check.');
  } else if (ran) {
    st.kaUnproc = false;
  }
}

// check() щохвилини пише LAST_CHECK_OK після пошуку в Gmail; давно не писав — Gmail відмовляє або тригер мертвий
function wdCheckAlive_(st, now, msgs) {
  const ok = Number(PropertiesService.getScriptProperties().getProperty('LAST_CHECK_OK')) || 0;
  if (!ok) return;   // стара версія Code.gs — ще не пише
  const min = Math.round((now.getTime() - ok) / 60000);
  if (min > 15 && !st.checkDown) {
    st.checkDown = true;
    msgs.push('⛔ Пошта KA → бот не працює вже ' + min + ' хв: функція check в Apps Script падає. Нові оголошення ' +
              'з Kleinanzeigen приходитимуть пачками із запізненням. Apps Script → «Виконання» — там текст помилки.');
  } else if (min <= 15 && st.checkDown) {
    st.checkDown = false;
    msgs.push('✅ Пошта KA → бот знову працює.');
  }
}

function wdTriggers_(st, now, msgs) {
  const have = ScriptApp.getProjectTriggers().map(function (t) { return t.getHandlerFunction(); });
  const miss = Object.keys(WD_TRIGGERS).filter(function (f) { return have.indexOf(f) < 0; });
  if (miss.length) wdOnce_(st, 'triggers', 24, '⚠️ В Apps Script немає тригера: ' + miss.map(function (f) {
    return f + ' (' + WD_TRIGGERS[f] + ')'; }).join(', ') + '. Запусти відповідну функцію install…', msgs, now);
}

function wdSend_(text, now) {
  const props = PropertiesService.getScriptProperties();
  const tok = officeToken_(props), chat = props.getProperty('TELEGRAM_CHAT_ID');
  if (!tok || !chat) { console.log(text); return; }
  const h = wdBerlinHour_(now);
  UrlFetchApp.fetch('https://api.telegram.org/bot' + tok + '/sendMessage', { method: 'post', muteHttpExceptions: true,
    payload: { chat_id: chat, text: text, disable_web_page_preview: 'true', disable_notification: String(h < 8 || h >= 23) } });
}

function watchdog() {
  const props = PropertiesService.getScriptProperties();
  const st = JSON.parse(props.getProperty('WD_STATE') || '{}');
  const now = new Date();
  const msgs = [];
  [wdEbay_, wdFailures_, wdCheckAlive_, wdKaMail_, wdTriggers_, wdTokenExpiry_].forEach(function (f) {
    try { f(st, now, msgs); } catch (e) {
      if (/GH_AUTH/.test(String(e))) {
        wdOnce_(st, 'gh_auth', 12, (/GH_AUTH немає/.test(String(e))
                ? '🔑 В Apps Script немає GitHub-токена. Без нього картки з Kleinanzeigen і «поділитися» не працюють. '
                : '🔑 GitHub-токен в Apps Script не працює (' + String(e).replace(/.*GH_AUTH /, '') + ', мабуть, прострочений). ' +
                  'Без нього картки з Kleinanzeigen і «поділитися» не працюють. Створи новий токен на GitHub і ') +
                'Запиши його у ⚙ «Властивості скрипту» як GITHUB_TOKEN (не в код — оновлення файлу його зітре).', msgs, now);
      } else {
        console.log(f.name + ': ' + e);
        // 01.10: Gmail-ліміт Google вичерпався, а сторож мовчав — тепер про будь-яку помилку перевірки пише
        wdOnce_(st, 'err_' + f.name, 6, /too many times|invoked too many|Service/i.test(String(e))
          ? '⛔ Google обмежив Apps Script на сьогодні (' + String(e).slice(0, 120) + '). Пошта KA → бот може не працювати ' +
            'до скидання ліміту (~доба). Напиши мені — розберемось.'
          : '⚠️ Сторож: помилка перевірки ' + f.name + ': ' + String(e).slice(0, 200), msgs, now);
      }
    }
  });
  props.setProperty('WD_STATE', JSON.stringify(st));
  if (msgs.length) wdSend_(msgs.join('\n\n'), now);
  console.log(msgs.length ? msgs.join('\n\n') : 'усе гаразд');
}

// Раз на добу: чи файли в Apps Script тієї ж версії, що на GitHub (їх вставляють вручну — легко пропустити оновлення)
const WD_FILES = [['gmail_trigger.gs', 'code.gs', 'VER_CODE', true], ['ledger.gs', 'ledger.gs', 'VER_LEDGER', true],
  ['kafilter.gs', 'kafilter.gs', 'VER_KAFILTER', false], ['watchdog.gs', 'watchdog.gs', 'VER_WATCHDOG', false]];

function wdLocalVersion_(name) {   // старий файл без константи → '' (теж «застарів»)
  return { VER_CODE: typeof VER_CODE === 'undefined' ? '' : VER_CODE,
    VER_LEDGER: typeof VER_LEDGER === 'undefined' ? '' : VER_LEDGER,
    VER_KAFILTER: typeof VER_KAFILTER === 'undefined' ? '' : VER_KAFILTER,
    VER_WATCHDOG: typeof VER_WATCHDOG === 'undefined' ? '' : VER_WATCHDOG }[name] || '';
}

function wdVersions_(now) {
  const old = [];
  let redeploy = false;
  WD_FILES.forEach(function (f) {
    const r = UrlFetchApp.fetch('https://raw.githubusercontent.com/' + REPO + '/main/tools/' + f[0], { muteHttpExceptions: true });
    if (r.getResponseCode() !== 200) return;
    const m = r.getContentText().match(new RegExp("const " + f[2] + " = '([^']+)'"));
    if (m && m[1] !== wdLocalVersion_(f[2])) { old.push(f[1] + ' ← tools/' + f[0]); redeploy = redeploy || f[3]; }
  });
  if (!old.length) return '';
  return '🔄 На GitHub є новіші версії файлів Apps Script — заміни їх вміст (Raw → скопіювати все → вставити → 💾):\n' +
    old.map(function (s) { return '• ' + s; }).join('\n') +
    (redeploy ? '\nПотім «Ввести в дію» → «Керування розгортаннями» → ✏ → «Нова версія» → «Ввести в дію».' : '');
}

function dailyReport() {
  try {
    const v = wdVersions_(new Date());
    if (v) wdSend_(v, new Date());
  } catch (e) { console.log('версії: ' + e); }
  let code;
  try { code = wdGh_('/actions/workflows/daily_report.yml/dispatches', 'post', { ref: 'main' }).code; } catch (e) { code = String(e); }
  if (code !== 204) wdSend_('⚠️ Не зміг запустити щоденний звіт (GitHub ' + code + ').', new Date());
}

function installWatchdog() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (['watchdog', 'dailyReport'].indexOf(t.getHandlerFunction()) >= 0) ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('watchdog').timeBased().everyMinutes(10).create();
  ScriptApp.newTrigger('dailyReport').timeBased().atHour(9).nearMinute(0).everyDays(1).inTimezone('Europe/Berlin').create();
  watchdog();
  dailyReport();
  console.log('сторож увімкнено (кожні 10 хв) + звіт щоранку о 9:00; перший звіт прийде за ~1 хв');
}
