// «продати N» (node tools/test_sell.js): шифрування — пара до research/sell.py (той самий вектор у test_sell.py), команда
const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');
const signed = (buf) => Array.from(buf).map((b) => (b > 127 ? b - 256 : b));
const sent = [], fetched = [];
const ctx = {
  console: { log: () => {} },
  Utilities: {
    Charset: { UTF_8: 'utf8' },
    computeHmacSha256Signature: (msg, key) => signed(crypto.createHmac('sha256', key).update(msg, 'utf8').digest()),
    newBlob: (s) => ({ getBytes: () => signed(Buffer.from(s, 'utf8')) }),
    base64Encode: (bytes) => Buffer.from(bytes.map((b) => b & 255)).toString('base64'),
    getUuid: () => '1234-5678',
  },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };

// вектор: Python-тест розшифровує саме його (test_sell.py, VECTOR)
const v = ctx.seal_({ row: 3, title: 'OWC 2x16GB DDR4 für iMac', cost: 45.5 }, '123:ABC', 'n0nce');
check('vector', v, { blob: 'ckNGkBdsfkbzaDMDdkUQy2CAcFUVcmcL47hP7XagKCLHDNhFMfmX+ODbR4DI3gCFZpjESsJhH5H5',
  mac: '61231ce607032562185d2078ff7a8ccd', nonce: 'n0nce' });

// команда: таблиця з двома рядками, GitHub і Telegram підмінені
const COLN = 23;
const row = (title, status, spent, price) => { const r = new Array(COLN).fill(''); r[2] = title; r[10] = status; r[9] = spent; r[6] = price; return r; };
const data = [row('OWC 2x16GB DDR4 für iMac', 'Отримано', 50.49, 45), row('PS5 Slim', 'Продано', 300, 300)];
vm.runInContext(`
  PropertiesService = { getScriptProperties: () => ({ getProperty: (k) => ({ TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O', GITHUB_TOKEN: 'g' })[k] }) };
  ledger_ = function () { return { getSheetByName: () => ({ getLastRow: () => 4 + __data.length,
    getRange: (r, c, n, w) => ({ getValues: () => __data.slice(r - 5, r - 5 + n) }) }) }; };
  notify_ = function (t) { __sent.push(t); };
  UrlFetchApp = { fetch: (u, o) => { __fetched.push([u, JSON.parse(o.payload)]); return { getResponseCode: () => 204 }; } };
`, Object.assign(ctx, { __data: data, __sent: sent, __fetched: fetched }));
const say = (text, chat) => vm.runInContext('sellCommand_(__m)', Object.assign(ctx, { __m: { text: text, chat: { id: chat || 7 } } }));

check('not a sell command', [say('продав 110 OWC'), say('купив 45 X')], [false, false]);
check('foreign chat silent', [say('продати 1', 99), sent.length, fetched.length], [true, 0, 0]);
say('продати');
check('list shows only open', [/№1 OWC/.test(sent[0]), /PS5/.test(sent[0])], [true, false]);
say('продати 1');
check('dispatch sell.yml', [fetched.length, /sell\.yml\/dispatches$/.test(fetched[0][0]), Object.keys(fetched[0][1].inputs).sort()],
  [1, true, ['blob', 'mac', 'nonce']]);
check('no plain cost or title in request', /50.49|OWC|iMac/.test(JSON.stringify(fetched[0][1])), false);
check('told waiting', /Готую оголошення для №1/.test(sent[sent.length - 1]), true);
say('продати 2');
check('sold row refused', [fetched.length, /Продано/.test(sent[sent.length - 1])], [1, true]);
say('продати 9');
check('missing row', /немає №9/.test(sent[sent.length - 1]), true);
say('продати 1 Kingston Fury 2x16GB DDR4 3200');
check('title override dispatched', fetched.length, 2);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
