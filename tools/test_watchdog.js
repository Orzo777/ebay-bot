// Сторож ботів (node tools/test_watchdog.js): перезапуск eBay-сторожа, збої, листи KA, токен — з підмінними Google-сервісами
const fs = require('fs');
const vm = require('vm');
const NOW = new Date('2026-09-30T12:00:00Z');   // 14:00 за Берліном
const ago = (min) => new Date(NOW.getTime() - min * 60000).toISOString();
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };

function setup(o) {
  const store = Object.assign({ GITHUB_TOKEN: 't', TELEGRAM_CHAT_ID: '7', OFFICE_BOT_TOKEN: 'O' }, o.props || {});
  const calls = [], sent = [];
  const ctx = {
    console: { log: () => {} },
    Date: class extends Date { constructor(...a) { super(...(a.length ? a : [NOW])); } },
    PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => store[k] || null, setProperty: (k, v) => { store[k] = v; } }) },
    Utilities: { formatDate: (d, tz, f) => f === 'H' ? String((d.getUTCHours() + 2) % 24)
      : f === 'dd.MM' ? String(d.getUTCDate()).padStart(2, '0') + '.' + String(d.getUTCMonth() + 1).padStart(2, '0') : '13:00' },
    GmailApp: { search: () => { if (o.gmailErr) throw new Error(o.gmailErr); return o.mail === undefined ? [] : [{ getLastMessageDate: () => new Date(o.mail) }]; } },
    ScriptApp: { getProjectTriggers: () => (o.triggers || ['check', 'kaFilter', 'processLedger']).map((f) => ({ getHandlerFunction: () => f })) },
    UrlFetchApp: { fetch: (u, opt) => {
      calls.push([opt && opt.method || 'get', u.replace(/^https:\/\/api\.github\.com\/repos\/[^/]+\/[^/]+/, '')]);
      if (/getWebhookInfo/.test(u)) return { getResponseCode: () => 200, getContentText: () => JSON.stringify({ ok: true,
        result: (o.hook || ((t) => ({ url: 'https://x/exec', pending_update_count: 0 })))(u) }) };
      if (/telegram/.test(u)) { sent.push(opt.payload); return { getResponseCode: () => 200 }; }
      if (/raw\.githubusercontent/.test(u)) return { getResponseCode: () => 200, getContentText: () => (o.raw ? o.raw(u) : '') };
      if (o.auth) return { getResponseCode: () => 401, getContentText: () => '' };
      if (/\/jobs$/.test(u)) return { getResponseCode: () => 200, getContentText: () => JSON.stringify({ jobs: o.jobs || [] }), getHeaders: () => ({}) };
      if (/dispatches/.test(u)) return { getResponseCode: () => o.dispatch || 204, getContentText: () => '', getHeaders: () => ({}) };
      const runs = /ebay_watch/.test(u) ? o.ebay || [] : /ram_mail_alert/.test(u) ? o.mailRuns || [] : o.all || [];
      return { getResponseCode: () => 200, getContentText: () => JSON.stringify({ workflow_runs: runs }),
               getHeaders: () => (o.expires ? { 'github-authentication-token-expiration': o.expires } : {}) };
    } },
  };
  vm.createContext(ctx);
  const src = ['gmail_trigger.gs', 'ledger.gs', 'watchdog.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n');
  vm.runInContext(src, ctx);
  return { run: () => ctx.watchdog(), calls, sent, store, text: () => sent.map((p) => p.text).join('\n---\n') };
}
const run = (status, conclusion, updated, extra) => Object.assign({ id: Math.random(), name: 'ebay-watch', status, conclusion,
  created_at: updated, updated_at: updated, html_url: 'https://gh/run' }, extra || {});
const okMail = { mail: ago(30), mailRuns: [run('completed', 'success', ago(29), { name: 'ram-mail-alert' })] };

// 1. eBay-сторож іде — тиша
let w = setup(Object.assign({ ebay: [run('in_progress', null, ago(30))] }, okMail));
w.run();
check('all quiet', [w.sent.length, w.calls.filter((c) => c[0] === 'post' && /dispatches/.test(c[1]) && !/ka_reply/.test(c[1])).length], [0, 0]);

// 2. стоїть 45 хв після успіху → перезапуск + одне повідомлення; через 10 хв (той самий стан) — без повтору
w = setup(Object.assign({ ebay: [run('completed', 'success', ago(45)), run('completed', 'success', ago(100))] }, okMail));
w.run();
check('restart dispatched', w.calls.some((c) => c[0] === 'post' && c[1] === '/actions/workflows/ebay_watch.yml/dispatches'), true);
check('restart told', /перезапустив/.test(w.text()), true);
w.run();
check('no second restart within 30 min', w.calls.filter((c) => c[0] === 'post' && /dispatches/.test(c[1]) && !/ka_reply/.test(c[1])).length, 1);

// 3. двічі поспіль startup_failure → не перезапускає, повідомляє
w = setup(Object.assign({ ebay: [run('completed', 'startup_failure', ago(40)), run('completed', 'failure', ago(90))] }, okMail));
w.run();
check('crash loop: no restart', w.calls.some((c) => c[0] === 'post' && /dispatches/.test(c[1]) && !/ka_reply/.test(c[1])), false);
check('crash loop told', /падає/.test(w.text()), true);

// 3б. 05.10: двічі поспіль «GitHub не дав машину» (інцидент GitHub, наш код не стартував) → перезапуск, як завжди
w = setup(Object.assign({ ebay: [run('completed', 'failure', ago(40)), run('completed', 'failure', ago(90))],
                          jobs: [{ runner_id: 0, steps: [] }] }, okMail));
w.run();
check('infra failure: restart', w.calls.some((c) => c[0] === 'post' && /ebay_watch.yml\/dispatches/.test(c[1]) && !/ka_reply/.test(c[1])), true);
check('infra failure: no crash alarm', /падає/.test(w.text()), false);
// а якщо машина була і кроки йшли — це наш код: як і раніше, не перезапускає
w = setup(Object.assign({ ebay: [run('completed', 'failure', ago(40)), run('completed', 'failure', ago(90))],
                          jobs: [{ runner_id: 5, steps: [{ name: 'watch', conclusion: 'failure' }] }] }, okMail));
w.run();
check('real crash: no restart', w.calls.some((c) => c[0] === 'post' && /dispatches/.test(c[1]) && !/ka_reply/.test(c[1])), false);
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(5))], jobs: [{ runner_id: 0, steps: [] }], all: [
  run('completed', 'failure', ago(20), { id: 5, name: 'ka-share', html_url: 'https://gh/5' })] }, okMail));
w.run();
check('infra failure explained', /збій GitHub, не наш код/.test(w.text()), true);

// 4. свіжий збій ka-share → посилання; давній (5 год) — ні; вдруге — не повторює
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(5))], all: [
  run('completed', 'failure', ago(20), { id: 1, name: 'ka-share', html_url: 'https://gh/1' }),
  run('completed', 'failure', ago(300), { id: 2, name: 'tests', html_url: 'https://gh/2' }),
  run('completed', 'cancelled', ago(20), { id: 3, name: 'ram-mail-alert' })] }, okMail));
w.run();
check('fresh failure reported', [/«поділитися»: https:\/\/gh\/1/.test(w.text()), /gh\/2/.test(w.text())], [true, false]);
w.run();
check('failure once', w.sent.length, 1);

// 5. листів KA немає 8 год (вдень) → попередження один раз; лист прийшов → «знову приходять»
w = setup({ ebay: [run('in_progress', null, ago(5))], mail: ago(8 * 60), mailRuns: [run('completed', 'success', ago(479), { name: 'ram-mail-alert' })] });
w.run();
w.run();
check('ka stale once', [w.sent.length, /немає листів 8 год/.test(w.text())], [1, true]);

// 6. лист є 40 хв, а ram_mail_alert після нього не запускався
w = setup({ ebay: [run('in_progress', null, ago(5))], mail: ago(40), mailRuns: [run('completed', 'success', ago(120), { name: 'ram-mail-alert' })] });
w.run();
check('unprocessed mail', /не обробив/.test(w.text()), true);

// 7. прострочений токен GitHub → одне повідомлення про токен
w = setup(Object.assign({ auth: true }, okMail));
w.run();
check('token alert', [w.sent.length, /GitHub-токен/.test(w.text())], [1, true]);
w = setup(Object.assign({ props: { GITHUB_TOKEN: '' } }, okMail));
w.run();
check('no token alert', [w.sent.length, /немає GitHub-токена/.test(w.text())], [1, true]);

// 8. зник тригер check
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(5))], triggers: ['kaFilter', 'processLedger'] }, okMail));
w.run();
check('missing trigger', /немає тригера: check/.test(w.text()), true);

// 9. Gmail-ліміт Google (01.10) → повідомлення, а не тиша
w = setup({ ebay: [run('in_progress', null, ago(5))], gmailErr: 'Service invoked too many times for one day: gmail.' });
w.run();
check('gmail quota alert', /Google обмежив Apps Script/.test(w.text()), true);

// 10. check() давно не писав LAST_CHECK_OK → тривога; знову пише → «знову працює»
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(5))], props: { LAST_CHECK_OK: String(NOW.getTime() - 30 * 60000) } }, okMail));
w.run();
check('check down', /не працює вже 30 хв/.test(w.text()), true);
w.store.LAST_CHECK_OK = String(NOW.getTime() - 60000);
w.run();
check('check back', /знову працює/.test(w.text()), true);

// 11. разовий збій KA-пошти, за яким успіх — тиша; два поспіль — повідомлення
const rm = (id, c, min) => run('completed', c, ago(min), { id: id, name: 'ram-mail-alert', html_url: 'https://gh/' + id });
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(5))], all: [rm(12, 'success', 5), rm(11, 'cancelled', 8), rm(10, 'failure', 10), rm(9, 'success', 20)] }, okMail));
w.run();
check('flaky single failure silent', w.sent.length, 0);
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(5))], all: [rm(21, 'failure', 5), rm(20, 'failure', 10), rm(19, 'success', 20)] }, okMail));
w.run();
check('two failures reported', /gh\/21/.test(w.text()), true);

// 12. токен GitHub закінчується за 5 днів → попередження раз на добу; за 30 днів — тиша
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(5))], expires: '2026-10-05 10:00:00 UTC' }, okMail));
w.run();
w.run();
check('token expiry warned once', [w.sent.length, /закінчується 05\.10/.test(w.text()), /через 4 дн\./.test(w.text())], [1, true, true]);
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(5))], expires: '2026-11-15 10:00:00 UTC' }, okMail));
w.run();
check('far expiry silent', w.sent.length, 0);

// 06.10: Apps Script відстає від GitHub (автооновлення не стартувало під час збою GitHub) → сторож сам запускає
// автооновлення щогодини, до 3 спроб, і лише потім пише; коли наздогнав — лічильник скидається
const newer = (u) => (/watchdog\.gs$/.test(u) ? "const VER_WATCHDOG = '2099-01-01';" : '');
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(5))], raw: newer }, okMail));
const deploys = () => w.calls.filter((c) => c[0] === 'post' && /deploy_apps_script\.yml\/dispatches/.test(c[1]) && !/ka_reply/.test(c[1])).length;
w.run();
check('behind → deploy dispatched', [deploys(), w.sent.length], [1, 0]);
w.run();
check('not again within the hour', deploys(), 1);
const st = () => JSON.parse(w.store.WD_STATE);
for (let k = 0; k < 3; k++) { const s2 = st(); s2.verAt -= 61 * 60000; w.store.WD_STATE = JSON.stringify(s2); w.run(); }
check('3 tries, then the user is told', [deploys(), /автооновлення не допомогло/.test(w.text())], [3, true]);
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(5))], props: { WD_STATE: JSON.stringify({ deployTries: 2 }) } }, okMail));
w.run();
check('caught up → counter reset, nothing sent', [st().deployTries, deploys(), w.sent.length], [0, 0, 0]);

// 09.10: облік давно не оновлювався → тривога; знову живий → «✅»
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(5))], props: { LEDGER_OK: String(NOW.getTime() - 90 * 60000) } }, okMail));
w.run();
check('ledger stale → alarm', /Облік не оновлювався 90 хв/.test(w.text()), true);
w.store.LEDGER_OK = String(NOW.getTime() - 5 * 60000);
const st2 = JSON.parse(w.store.WD_STATE); st2.hookAt = NOW.getTime(); w.store.WD_STATE = JSON.stringify(st2);
w.run();
check('ledger back → ok message', /Облік знову оновлюється/.test(w.text()), true);
// вебхуки: черга / помилка / не встановлений → одне повідомлення на бот; все гаразд — тиша
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(5))], props: { TELEGRAM_BOT_TOKEN: 'M' },
  hook: (u) => (/botM\//.test(u) ? { url: 'https://x/exec', pending_update_count: 37 }
    : { url: 'https://x/exec?bot=office', pending_update_count: 0, last_error_date: NOW.getTime() / 1000 - 600, last_error_message: 'Wrong response from the webhook: 500' }) }, okMail));
w.run();
check('webhook problems reported per bot', [/основний бот: у черзі 37/.test(w.text()), /«Облік і продаж»: помилка вебзастосунку: Wrong response/.test(w.text())], [true, true]);
w.run();
check('not again within the hour', (w.text().match(/у черзі 37/g) || []).length, 1);
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(5))], props: { TELEGRAM_BOT_TOKEN: 'M' } }, okMail));
w.run();
check('healthy webhooks → silence', w.sent.length, 0);

// 10.10: прибирання відповідей KA — щогодини звідси (cron GitHub спрацьовує раз на 6–7 год)
w = setup(Object.assign({ ebay: [run('in_progress', null, ago(30))] }, okMail));
w.run(); w.run();
check('ka sweep dispatched once per hour', w.calls.filter((c) => c[0] === 'post' && c[1] === '/actions/workflows/ka_reply.yml/dispatches').length, 1);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
