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

// 18.10: події — запис на товар (EV_<№>); старий спільний LEDGER_EVENTS розкладається; прибирання закритих угод
const evStore = { LEDGER_EVENTS: JSON.stringify({ 1: { got: '2026-10-01' }, 2: { sent: '2026-10-02' } }) };
current = Object.assign(project(evStore), { getProperties: () => Object.assign({}, evStore),
  setProperties: (o) => { Object.assign(evStore, o); }, deleteProperty: (k) => { delete evStore[k]; } });
check('legacy events read', ctx.events_()[2].sent, '2026-10-02');
ctx.markEvent_(3, 'got', '2026-10-18');
check('sharded after first save', [evStore.LEDGER_EVENTS, JSON.parse(evStore.EV_1).got, JSON.parse(evStore.EV_3).got], [undefined, '2026-10-01', '2026-10-18']);
ctx.markEvent_(3, 'rem', 'test0');
check('one row updated, others intact', [JSON.parse(evStore.EV_3).rem.test0, ctx.events_()[1].got], [1, '2026-10-01']);
// 24.10: гонка — команда «трек 1» записала відправку, поки інша обробка тримала старий знімок і писала свій товар:
// запис одного товару (і збереження «лише цих ключів») не повинен повертати старе в інших
const stale = ctx.events_();
evStore.EV_1 = JSON.stringify(Object.assign(JSON.parse(evStore.EV_1), { sent: '2026-10-24', outTrack: 'T1' }));
ctx.markEvent_(3, 'rem', 'race');
stale[3] = Object.assign(stale[3] || {}, { got: '2026-10-24' });
ctx.saveEvents_(stale, [3]);
check('no lost update', [JSON.parse(evStore.EV_1).sent, JSON.parse(evStore.EV_1).outTrack, JSON.parse(evStore.EV_3).got], ['2026-10-24', 'T1', '2026-10-24']);
check('capKeys_ keeps the newest', Object.keys(ctx.capKeys_({ a: 1, b: 1, c: 1, d: 1 }, 2)), ['c', 'd']);
// прибирання: №1 продано 100 днів тому (усе його — геть), №2 продано вчора (лише правки / назви / ціни), №3 відкритий
Object.assign(evStore, { PH_1: '[1]', PHS_1: '{}', EV_2: '{}', PH_2: '[2]', PH_3: '[3]',
  SELL_EDITS: JSON.stringify({ 1: { price: 1 }, 2: { price: 2 }, 3: { price: 3 } }), SELL_TITLES: JSON.stringify({ 2: 'x', 3: 'y' }),
  LISTED_PRICE: JSON.stringify({ 2: 175 }), EBAY_PUB: JSON.stringify({ 3: Date.now() - 2 * 86400000 }) });
vm.runInContext('ledgerRows_ = function () { return __rows; }; iso_ = function () { return "2026-10-18"; };', Object.assign(ctx, { __rows: [
  { n: 1, status: 'Продано', sdate: '2026-07-10', date: '2026-07-01' }, { n: 2, status: 'Продано', sdate: '2026-10-17' },
  { n: 3, status: 'Виставлено', date: '2026-10-10' }] }));
ctx.pruneProps_();
check('prune', [evStore.PH_1, evStore.PHS_1, evStore.EV_1, evStore.PH_2, evStore.EV_2, evStore.PH_3,
  Object.keys(JSON.parse(evStore.SELL_EDITS)), JSON.parse(evStore.SELL_TITLES), evStore.LISTED_PRICE, evStore.EBAY_PUB],
  [undefined, undefined, undefined, '[2]', '{}', '[3]', ['3'], { 3: 'y' }, '{}', '{}']);
const n2 = Object.keys(evStore).length;
ctx.pruneProps_();
check('prune once a day', Object.keys(evStore).length, n2);

// 18.10: довге повідомлення — кількома (≤3900), кнопки під останнім; один задовгий абзац — без нескінченної рекурсії
const posted = [];
current = project({ OFFICE_BOT_TOKEN: 'O', TELEGRAM_CHAT_ID: '7' });
ctx.UrlFetchApp = { fetch: (u, o) => { posted.push(o.payload); return { getResponseCode: () => 200 }; } };
ctx.notify_(Array.from({ length: 30 }, (_, i) => 'лот ' + i + ' ' + 'x'.repeat(240)).join('\n\n'), { inline_keyboard: [[{ text: 'k' }]] }, 'HTML');
check('split long message', [posted.length > 1, posted.every((p) => p.text.length <= 3900), posted.slice(0, -1).every((p) => !p.reply_markup),
  !!posted[posted.length - 1].reply_markup], [true, true, true, true]);
posted.length = 0;
ctx.notify_('y'.repeat(4000));
check('one huge paragraph — sent once, trimmed', [posted.length, posted[0].text.length], [1, 3900]);

// 18.10: у GitHub — лише номер оголошення з нотаток
check('compact notes for GitHub', ctx.rowsForGithub_([{ n: 4, title: 'T', status: 'Виставлено', note: 'довга історія; виставлено (eBay 111111111); ' +
  'ціна → 159 €; оновлено (eBay 336844624233); ще багато тексту' }, { n: 5, title: 'U', status: 'Перевірено', note: 'на KA' }]),
  [{ n: 4, title: 'T', status: 'Виставлено', note: '(eBay 336844624233)', ka: '' }, { n: 5, title: 'U', status: 'Перевірено', note: '', ka: '' }]);

console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
