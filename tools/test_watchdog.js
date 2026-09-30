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
    Utilities: { formatDate: (d, tz, f) => f === 'H' ? String((d.getUTCHours() + 2) % 24) : '13:00' },
    GmailApp: { search: () => o.mail === undefined ? [] : [{ getLastMessageDate: () => new Date(o.mail) }] },
    ScriptApp: { getProjectTriggers: () => (o.triggers || ['check', 'kaFilter', 'processLedger']).map((f) => ({ getHandlerFunction: () => f })) },
    UrlFetchApp: { fetch: (u, opt) => {
      calls.push([opt && opt.method || 'get', u.replace(/^https:\/\/api\.github\.com\/repos\/[^/]+\/[^/]+/, '')]);
      if (/telegram/.test(u)) { sent.push(opt.payload); return { getResponseCode: () => 200 }; }
      if (o.auth) return { getResponseCode: () => 401, getContentText: () => '' };
      if (/dispatches/.test(u)) return { getResponseCode: () => o.dispatch || 204, getContentText: () => '' };
      const runs = /ebay_watch/.test(u) ? o.ebay || [] : /ram_mail_alert/.test(u) ? o.mailRuns || [] : o.all || [];
      return { getResponseCode: () => 200, getContentText: () => JSON.stringify({ workflow_runs: runs }) };
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
check('all quiet', [w.sent.length, w.calls.filter((c) => c[0] === 'post' && /dispatches/.test(c[1])).length], [0, 0]);

// 2. стоїть 45 хв після успіху → перезапуск + одне повідомлення; через 10 хв (той самий стан) — без повтору
w = setup(Object.assign({ ebay: [run('completed', 'success', ago(45)), run('completed', 'success', ago(100))] }, okMail));
w.run();
check('restart dispatched', w.calls.some((c) => c[0] === 'post' && c[1] === '/actions/workflows/ebay_watch.yml/dispatches'), true);
check('restart told', /перезапустив/.test(w.text()), true);
w.run();
check('no second restart within 30 min', w.calls.filter((c) => c[0] === 'post' && /dispatches/.test(c[1])).length, 1);

// 3. двічі поспіль startup_failure → не перезапускає, повідомляє
w = setup(Object.assign({ ebay: [run('completed', 'startup_failure', ago(40)), run('completed', 'failure', ago(90))] }, okMail));
w.run();
check('crash loop: no restart', w.calls.some((c) => c[0] === 'post' && /dispatches/.test(c[1])), false);
check('crash loop told', /падає/.test(w.text()), true);

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

console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
