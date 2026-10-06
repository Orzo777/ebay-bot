// Версії файлів Apps Script (node tools/test_versions.js). Сторож порівнює VER_* у Apps Script з GitHub і нагадує
// оновити файл. Щоб нагадування не пропустити, цей тест падає, якщо файл змінився, а VER_* — ні:
// tools/versions.json зберігає відбиток кожного файлу для його версії. Змінив .gs → підніми VER_* і онови versions.json
// (тест сам підкаже нове значення).
const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');
const FILES = { 'gmail_trigger.gs': 'VER_CODE', 'ledger.gs': 'VER_LEDGER', 'kafilter.gs': 'VER_KAFILTER', 'watchdog.gs': 'VER_WATCHDOG' };
const manifest = JSON.parse(fs.readFileSync(__dirname + '/versions.json', 'utf8'));
let bad = 0;
const want = {};
for (const [f, name] of Object.entries(FILES)) {
  const src = fs.readFileSync(__dirname + '/' + f, 'utf8');
  const m = src.match(new RegExp("const " + name + " = '([^']+)'"));
  if (!m) { bad++; console.log('!! немає ' + name + ' у ' + f); continue; }
  const sha = crypto.createHash('sha256').update(src.replace(m[0], '').replace(/\r\n/g, '\n')).digest('hex').slice(0, 16);
  want[f] = { ver: m[1], sha: sha };
  const rec = manifest[f] || {};
  if (rec.sha !== sha && rec.ver === m[1]) { bad++; console.log('!! ' + f + ' змінився, а ' + name + " лишився '" + m[1] + "' — підніми версію"); }
  else if (rec.sha !== sha || rec.ver !== m[1]) { bad++; console.log('!! онови tools/versions.json для ' + f); }
}
if (bad) console.log('очікуваний tools/versions.json:\n' + JSON.stringify(want, null, 1));

// сторож: застарілий файл (або без константи) → повідомлення з інструкцією; ті самі версії → тиша
const raw = {};
for (const f of Object.keys(FILES)) raw[f] = fs.readFileSync(__dirname + '/' + f, 'utf8');
const ctx = { console: { log: () => {} }, UrlFetchApp: { fetch: (u) => {
  const f = u.split('/tools/')[1];
  return { getResponseCode: () => 200, getContentText: () => (f === 'ledger.gs' ? raw[f].replace(/const VER_LEDGER = '[^']+'/, "const VER_LEDGER = '2099-01-01'") : raw[f]) };
} } };
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs', 'kafilter.gs', 'watchdog.gs'].map((f) => raw[f]).join('\n') + '\nthis.v = wdVersions_;', ctx);
const msg = ctx.v(new Date());
if (!/ledger\.gs ← tools\/ledger\.gs/.test(msg) || /kafilter/.test(msg) || !/автооновлення не допомогло/.test(msg)) { bad++; console.log('!! versions msg', msg); }
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
