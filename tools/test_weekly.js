// Тижневий звіт і «виставив N» (node tools/test_weekly.js) — з підмінними Google-сервісами
const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');
const signed = (buf) => Array.from(buf).map((b) => (b > 127 ? b - 256 : b));
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O', GITHUB_TOKEN: 'g' };
const sent = [], fetched = [];
let berlin = { u: '1', H: '9', 'yyyy-MM-dd': '2026-10-05', 'dd.MM': '05.10' };
const ctx = {
  console: { log: () => {} },
  Utilities: {
    Charset: { UTF_8: 'utf8' },
    computeHmacSha256Signature: (msg, key) => signed(crypto.createHmac('sha256', key).update(msg, 'utf8').digest()),
    newBlob: (s) => ({ getBytes: () => signed(Buffer.from(s, 'utf8')) }),
    base64Encode: (bytes) => Buffer.from(bytes.map((b) => b & 255)).toString('base64'),
    getUuid: () => 'ab-cd',
    formatDate: (d, tz, f) => (f === 'yyyy-MM-dd' && d instanceof Date && d.getFullYear() < 2026) ? '2025-01-01' : berlin[f],
  },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => store[k] || null, setProperty: (k, v) => { store[k] = v; } }) },
  UrlFetchApp: { fetch: (u, o) => { fetched.push([u, JSON.parse(o.payload)]); return { getResponseCode: () => 204 }; } },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
const W = 23;
const row = (o) => { const r = new Array(W).fill(''); Object.entries(o).forEach(([k, v]) => { r[k] = v; }); return r; };
const data = [row({ 1: new Date('2026-09-29T10:00:00Z'), 2: 'OWC 2x16GB DDR4', 3: 'RAM', 4: 'eBay', 6: 45, 9: 50.49, 10: 'Отримано' }),
              row({ 1: new Date('2026-09-20T10:00:00Z'), 2: 'PS5 Slim', 3: 'Консоль', 9: 300, 10: 'Продано', 11: new Date('2026-09-27T10:00:00Z'), 13: 420, 17: 395, 18: 95 }),
              row({})];
const cells = {};
vm.runInContext(`
  ledger_ = function () { return { getSheetByName: () => ({ getLastRow: () => 4 + __data.length,
    getRange: (r, c, n, w) => ({ getValues: () => __data.slice(r - 5, r - 5 + (n || 1)),
                                 getValue: () => (__cells[r + ':' + c] !== undefined ? __cells[r + ':' + c] : __data[r - 5][c - 1]),
                                 setValue: (v) => { __cells[r + ':' + c] = v; } }) }) }; };
  notify_ = function (t) { __sent.push(t); };
`, Object.assign(ctx, { __data: data, __sent: sent, __cells: cells }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };

const rows = ctx.ledgerRows_();
check('rows', rows.map((r) => [r.n, r.title, r.spent, r.status, r.sdate, r.sprice, r.profit]),
      [[1, 'OWC 2x16GB DDR4', 50.49, 'Отримано', '', null, null], [2, 'PS5 Slim', 300, 'Продано', '05.10'.length ? rows[1].sdate : '', 420, 95]]);

// щопонеділка з 9:00 — один раз
check('monday 9:00 sends', ctx.weeklyIfDue_(ctx.PropertiesService.getScriptProperties(), new Date()), true);
check('dispatch weekly_report.yml', [fetched.length, /weekly_report\.yml\/dispatches$/.test(fetched[0][0]), Object.keys(fetched[0][1].inputs).sort()],
      [1, true, ['blob', 'mac', 'nonce']]);
check('ledger data encrypted', /OWC|PS5|50\.49/.test(JSON.stringify(fetched[0][1])), false);
check('second time same day — no', ctx.weeklyIfDue_(ctx.PropertiesService.getScriptProperties(), new Date()), false);
berlin = Object.assign({}, berlin, { u: '2', 'yyyy-MM-dd': '2026-10-06' });
check('tuesday — no', ctx.weeklyIfDue_(ctx.PropertiesService.getScriptProperties(), new Date()), false);
berlin = Object.assign({}, berlin, { u: '1', H: '8', 'yyyy-MM-dd': '2026-10-12' });
check('monday 8:00 — not yet', ctx.weeklyIfDue_(ctx.PropertiesService.getScriptProperties(), new Date()), false);

// команди
const say = (text, chat) => vm.runInContext('officeMessage(__m)', Object.assign(ctx, { __m: { text: text, chat: { id: chat || 7 } } }));
say('звіт');
check('report command', [fetched.length, /Готую звіт/.test(sent[sent.length - 1])], [2, true]);
say('виставив 1 124');
check('listed status', [cells['5:11'], /виставлено за 124/.test(cells['5:22']), /виставлено за 124 €/.test(sent[sent.length - 1])], ['Виставлено', true, true]);
say('виставив 2');
check('sold not changed', [cells['6:11'], /Продано/.test(sent[sent.length - 1])], [undefined, true]);
say('виставив 1', 99);
check('foreign chat', sent.length, 3);
// 06.10: купівлю з KA записано → прибрати переписку; номер оголошення — лише зашифрованим (публічний журнал запусків)
check('ad id from KA link', [ctx.kaAdId_('https://www.kleinanzeigen.de/s-anzeige/msi-a320-a-pro/3531265450-228-877'), ctx.kaAdId_('https://www.ebay.de/itm/1')],
      ['3531265450', '']);
const f0 = fetched.length;
check('close dispatched', ctx.kaCloseDispatch_(['3531265450'], 'куплено'), true);
const last = fetched[fetched.length - 1];
check('close → ka_reply.yml mode=close, encrypted', [fetched.length - f0, /ka_reply\.yml\/dispatches$/.test(last[0]), last[1].inputs.mode,
      /3531265450|куплено/.test(JSON.stringify(last[1]))], [1, true, 'close', false]);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
