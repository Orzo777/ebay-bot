// Перехід на окрему пошту (node tools/test_transfer.js): transferExport (старий проєкт) → аркуш «_перенесення» → transferImport
// (новий проєкт) → ті самі «Властивості скрипту», аркуш видалено; без LEDGER_ID / без аркуша — зрозуміла помилка.
const fs = require('fs');
const vm = require('vm');

function project(store) {
  return { getProperty: (k) => (k in store ? store[k] : null), setProperty: (k, v) => { store[k] = String(v); },
    setProperties: (o) => { Object.keys(o).forEach((k) => { store[k] = String(o[k]); }); }, getProperties: () => Object.assign({}, store),
    deleteProperty: (k) => { delete store[k]; } };
}
const sheets = {};
const ss = {
  getSheetByName: (n) => sheets[n] || null,
  insertSheet: (n) => {
    const cells = [];
    sheets[n] = { cells, getLastRow: () => cells.length,
      getRange: (r, c, h, w) => ({ setNumberFormat() { return this; }, setValues: (v) => { v.forEach((row, i) => { cells[r - 1 + i] = row.slice(); }); },
        getValues: () => cells.slice(r - 1, r - 1 + h).map((row) => row.slice(c - 1, c - 1 + w)) }) };
    return sheets[n];
  },
  deleteSheet: (s) => { Object.keys(sheets).forEach((k) => { if (sheets[k] === s) delete sheets[k]; }); },
};
let current = null;
const ctx = { console: { log: () => {} }, PropertiesService: { getScriptProperties: () => current } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(__dirname + '/ledger.gs', 'utf8'), ctx);
vm.runInContext('ledger_ = function () { return __ss; };', Object.assign(ctx, { __ss: ss }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };

const oldStore = { LEDGER_ID: 'L1', TELEGRAM_BOT_TOKEN: '1:A', EBAY_RT: 'v^1.1#RT', PH_4: '[{"id":"F"}]', TG_SEEN_OFFICE: '[1,2]' };
current = project(oldStore);
check('export count', ctx.transferExport(), 5);
check('export sheet', sheets._перенесення.cells.map((r) => r[0]), ['EBAY_RT', 'LEDGER_ID', 'PH_4', 'TELEGRAM_BOT_TOKEN', 'TG_SEEN_OFFICE']);

const newStore = {};
current = project(newStore);
let err = '';
try { ctx.transferImport(); } catch (e) { err = e.message; }
check('no LEDGER_ID → asks for it', /LEDGER_ID/.test(err), true);
newStore.LEDGER_ID = 'L1';
check('import count', ctx.transferImport(), 3);
check('imported values, seen-lists skipped, sheet removed', [newStore.EBAY_RT, newStore.PH_4, newStore.TG_SEEN_OFFICE, '_перенесення' in sheets],
  ['v^1.1#RT', '[{"id":"F"}]', undefined, false]);
err = '';
try { ctx.transferImport(); } catch (e) { err = e.message; }
check('second import → no sheet', /transferExport/.test(err), true);

// старий проєкт після експорту — тригери вимикаються
const deleted = [];
ctx.ScriptApp = { getProjectTriggers: () => ['a', 'b', 'c'], deleteTrigger: (t) => deleted.push(t) };
check('uninstallAll removes every trigger', [ctx.uninstallAll(), deleted], [3, ['a', 'b', 'c']]);

console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
