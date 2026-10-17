// Маршрутизація doPost між двома ботами (node tools/test_webhook.js): основний → GitHub ka_share, ?bot=office → облік
const fs = require('fs');
const vm = require('vm');
const store = {}, fetched = [], office = [];
let busy = false;
const ctx = {
  console: { log: () => {} },
  LockService: { getScriptLock: () => ({ waitLock: () => {}, tryLock: () => !busy, releaseLock: () => {} }) },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => store[k] || null, setProperty: (k, v) => { store[k] = v; },
    deleteProperty: (k) => { delete store[k]; }, getKeys: () => Object.keys(store) }) },
  HtmlService: { createHtmlOutput: (t) => t },
  UrlFetchApp: { fetch: (u) => { fetched.push(u); return { getResponseCode: () => 204, getContentText: () => '' }; } },
  officeMessage: (m) => office.push(m.text),
  ledgerCommand: () => false,
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(__dirname + '/gmail_trigger.gs', 'utf8'), ctx);
store.GITHUB_TOKEN = 't';
const post = (id, text, bot) => ctx.doPost({ parameter: bot ? { bot: bot } : {},
  postData: { contents: JSON.stringify({ update_id: id, message: { text: text, chat: { id: 7 } } }) } });
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };
post(1, 'https://www.kleinanzeigen.de/s-anzeige/x/1-225-1');
check('main → GitHub', [fetched.length, office.length], [1, 0]);
post(1, 'облік', 'office');   // той самий update_id, але інший бот — не дубль
check('office → ledger, not GitHub', [fetched.length, office], [1, ['облік']]);
post(1, 'облік', 'office');
check('office dedup', office.length, 1);
post(1, 'x');
check('main dedup', fetched.length, 1);
post(5, 'звіт');
check('office command from main bot → ledger, not GitHub', [fetched.length, office[office.length - 1]], [1, 'звіт']);

// 17.10: замок зайнятий (альбом — інше фото ще читається) → у чергу, не губимо; наступний виклик обробляє чергу по порядку
busy = true;
post(6, 'фото-1', 'office');
post(7, 'фото-2', 'office');
busy = false;
check('busy → queued, not lost', [office.length, Object.keys(store).filter((k) => k.indexOf('TGQ_') === 0).length], [2, 2]);
post(8, 'на руках', 'office');
check('queue drained in order after own update', [office.slice(-3), Object.keys(store).filter((k) => k.indexOf('TGQ_') === 0).length],
  [['на руках', 'фото-1', 'фото-2'], 0]);
busy = true; post(9, 'пізно', 'office'); busy = false;
ctx.drainQueued_();
check('check() trigger drains too', office[office.length - 1], 'пізно');
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
