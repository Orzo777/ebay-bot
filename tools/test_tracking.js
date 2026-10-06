// Відстеження посилок у ledger.gs (node tools/test_tracking.js): трек із листів eBay/KA і з команд, листи перевізників
const fs = require('fs');
const vm = require('vm');
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O', LEDGER_SCANNED: '1', EXPENSES_V1: '1' };
const sent = [];
let now = { 'yyyy-MM-dd': '2026-10-06', H: '9', u: '2', 'dd.MM': '06.10' };
const pad = (x) => ('0' + x).slice(-2);
let mails = { ledger: [], carrier: [] }, searches = [];

function sheet() {   // клітинки «рядок:стовпець»; діапазони за номерами
  const cells = {};
  const s = { cells, last: 4, rows: [], getLastRow: () => s.last, appendRow: (r) => { s.rows.push(r); s.last++; } };
  s.getRange = (r, c, n, w) => {
    n = n || 1; w = w || 1;
    const rng = new Proxy({}, { get: (_, k) => {
      if (k === 'setValue') return (v) => { cells[r + ':' + c] = v; s.last = Math.max(s.last, r); return rng; };
      if (k === 'getValue') return () => (cells[r + ':' + c] !== undefined ? cells[r + ':' + c] : '');
      if (k === 'getValues') return () => Array.from({ length: n }, (_, i) => Array.from({ length: w }, (_, j) => {
        const v = cells[(r + i) + ':' + (c + j)]; return v === undefined ? '' : v; }));
      return () => rng;
    } });
    return rng;
  };
  return s;
}
const deals = sheet(), log = sheet();
log.last = 1;
const D = (x) => new Date(x + 'T10:00:00Z');
// №1 купівля KA в дорозі, №2 продано (посилка покупцю), №3 купівля eBay оплачена (без треку), №4 продано без треку
[[D('2026-10-01'), 'G.Skill Ripjaws 2x16GB DDR4 SO-DIMM', 'RAM', 'Kleinanzeigen', 'В дорозі'],
 [D('2026-09-20'), 'OWC 32GB 2x16GB DDR4 SO-DIMM iMac', 'RAM', 'eBay', 'Продано'],
 [D('2026-10-03'), 'Corsair Vengeance LPX 32GB DDR4', 'RAM', 'eBay', 'Оплачено'],
 [D('2026-09-15'), 'Crucial DDR5 32GB', 'RAM', 'eBay', 'Продано']].forEach((v, i) => {
  const r = 5 + i;
  deals.cells[r + ':2'] = v[0]; deals.cells[r + ':3'] = v[1]; deals.cells[r + ':4'] = v[2]; deals.cells[r + ':5'] = v[3];
  deals.cells[r + ':11'] = v[4]; deals.last = r;
});
deals.cells['6:12'] = D('2026-10-04');
const msg = (id, from, subject, body) => ({ getId: () => id, getFrom: () => from, getSubject: () => subject, getPlainBody: () => body,
  getBody: () => body, getDate: () => D('2026-10-06') });
const ctx = {
  console: { log: () => {} },
  Utilities: { formatDate: (d, tz, f) => (Math.abs(d.getTime() - Date.now()) < 60000 ? now[f]
    : { 'yyyy-MM-dd': d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()), 'dd.MM': pad(d.getUTCDate()) + '.' + pad(d.getUTCMonth() + 1) }[f]) },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => store[k] || null, setProperty: (k, v) => { store[k] = v; } }) },
  GmailApp: { search: (q) => { searches.push(q); const list = /dhl\.de/.test(q) ? mails.carrier : mails.ledger;
    return list.map((m) => ({ getMessages: () => [m] })); } },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
vm.runInContext(`ledger_ = function () { return { getSheetByName: (n) => n === 'Угоди' ? __deals : n === 'Лог листів' ? __log : null }; };
  notify_ = function (t, mk) { __sent.push([t, mk]); };`, Object.assign(ctx, { __deals: deals, __log: log, __sent: sent }));
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };
const ev = () => JSON.parse(store.LEDGER_EVENTS || '{}');
const last = () => (sent[sent.length - 1] || [''])[0];

// розпізнавання
const tr = (t) => { const x = ctx.trackFrom_(t); return x ? [x.num, x.carrier] : null; };
check('eBay DHL', tr('Ihr Artikel wurde versandt.\nVersandservice: DHL Paket\nSendungsnummer: 00340434292135100186'), ['00340434292135100186', 'DHL']);
check('Hermes', tr('Ihre Sendungsnummer lautet H1001234567890123456. Hermes'), ['H1001234567890123456', 'Hermes']);
check('UPS by format', tr('Tracking-Nummer 1Z999AA10123456784'), ['1Z999AA10123456784', 'UPS']);
check('KA with DPD', tr('Dein Artikel ist unterwegs. Paketnummer: 01505012345678 (DPD)'), ['01505012345678', 'DPD']);
check('no number', tr('Sendungsnummer: wird nachgereicht'), null);
check('kind delivered', ctx.carrierKind_('Ihre DHL Sendung wurde zugestellt', ''), 'delivered');
check('kind "wird heute zugestellt" is transit', ctx.carrierKind_('Ihre Sendung wird heute zugestellt', ''), 'transit');
check('kind ready', ctx.carrierKind_('Ihre Sendung liegt in der Packstation 123 für Sie bereit', ''), 'ready');
check('kind ready filiale', ctx.carrierKind_('Abholbereit: Ihre Sendung in der Filiale', ''), 'ready');
check('kind transit', ctx.carrierKind_('Ihre DHL Sendung kommt heute', ''), 'transit');
check('link', ctx.trackLink_({ num: 'H1', carrier: 'Hermes' }).indexOf('myhermes.de') > 0 &&
      ctx.trackLink_({ num: 'X1', carrier: '' }).indexOf('parcelsapp') > 0, true);
check('parseMail_ keeps track', ctx.parseMail_(msg('a', 'eBay <ebay@ebay.de>', 'Ihr Artikel wurde versandt: Corsair Vengeance LPX',
  'Sendungsnummer: 00340434292135100999 DHL')).track.num, '00340434292135100999');

// команди
const say = (text) => vm.runInContext('officeMessage(__m)', Object.assign(ctx, { __m: { text: text, chat: { id: 7 } } }));
say('трек 3 00340434292135100999');
check('трек on purchase', [deals.cells['7:11'], ev()[3].track, ev()[3].carrier, ev()[3].shipped], ['В дорозі', '00340434292135100999', 'DHL', '2026-10-06']);
check('трек reply has button', /dhl\.de/.test(JSON.stringify(sent[sent.length - 1][1])), true);
say('трек 1 00340434292135100186');
say('відправив 2 hermes H1001234567890123456');
check('відправив with track', [ev()[2].sent, ev()[2].outTrack, ev()[2].outCarrier, /Коли покупець отримає/.test(last())],
      ['2026-10-06', 'H1001234567890123456', 'Hermes', true]);
say('відправив 4');
check('відправив without track asks for one', [ev()[4].sent, /трек 4 номер/.test(last())], ['2026-10-06', true]);
say('трек 3');
check('трек without number → hint', /Не бачу трек-номера|трек-номер/.test(last()), true);

// листи перевізників: лише за відомими треками
const n0 = sent.length;
mails.carrier = [
  msg('c1', 'DHL <noreply@dhl.de>', 'Ihre DHL Sendung wurde zugestellt', 'Sendungsnummer 00340434292135100186. Zugestellt an: Nachbar'),
  msg('c2', 'Hermes <noreply@myhermes.de>', 'Ihre Sendung wurde zugestellt', 'Sendungsnummer H1001234567890123456'),
  msg('c3', 'DHL <noreply@dhl.de>', 'Ihre DHL Sendung wurde zugestellt', 'Sendungsnummer 00340999999999999999 (Amazon)'),   // особиста
  msg('c4', 'DHL <noreply@dhl.de>', 'Ihre Sendung liegt in der Filiale für Sie bereit', 'Sendungsnummer 00340434292135100999')];
const done = {};
check('carrier mails matched', vm.runInContext('processCarriers_(__log, __done)', Object.assign(ctx, { __done: done })), 3);
check('purchase delivered → Отримано + test tip', [deals.cells['5:11'], ev()[1].got, sent.slice(n0).some((s) => /№1 .*отримано.*Протестуй/.test(s[0]))],
      ['Отримано', '2026-10-06', true]);
check('sale delivered to buyer', [ev()[2].outDelivered, sent.slice(n0).some((s) => /✅ №2 .*доставлено покупцю/.test(s[0]))], ['2026-10-06', true]);
check('ready in Filiale', [deals.cells['7:11'], sent.slice(n0).some((s) => /№3 .*чекає у відділенні/.test(s[0]))], ['В дорозі', true]);
check('personal parcel ignored and not logged', [log.rows.length, log.rows.some((r) => /c3/.test(r[0]))], [3, false]);
const n1 = sent.length;
vm.runInContext('processCarriers_(__log, __done)', ctx);
check('no repeats', [sent.length, log.rows.length], [n1, 3]);

// повний прохід обліку: лист eBay «зугестельт» з треком посилки покупцю не чіпає купівлі
mails.ledger = [msg('L1', 'eBay <ebay@ebay.de>', 'Ihr Artikel wurde zugestellt: OWC 32GB', 'Sendungsnummer: H1001234567890123456 Hermes'),
                msg('L2', 'eBay <ebay@ebay.de>', 'Ihr Artikel wurde versandt: Corsair Vengeance LPX 32GB DDR4', 'Sendungsnummer: 00340434292135100777 DHL')];
store.LEDGER_EVENTS = JSON.stringify(Object.assign(ev(), { 2: { sent: '2026-10-05', outTrack: 'H1001234567890123456' } }));
deals.cells['7:11'] = 'Оплачено';
const n2 = sent.length;
searches = [];
ctx.processLedger();
check('eBay delivered with buyer track → out path', [ev()[2].outDelivered, deals.cells['5:11'], deals.cells['6:11']], ['2026-10-06', 'Отримано', 'Продано']);
check('eBay shipped → В дорозі + 🚚 with track', [deals.cells['7:11'], ev()[3].track, sent.slice(n2).some((s) => /🚚 №3 .*DHL 00340434292135100777/.test(s[0]) && s[1])],
      ['В дорозі', '00340434292135100777', true]);
check('carrier search only when something is awaited', searches.filter((q) => /dhl\.de/.test(q)).length, 1);
store.LEDGER_EVENTS = '{}';
searches = [];
vm.runInContext('processCarriers_(__log, {})', ctx);
check('nothing awaited → mailbox of carriers not even searched', searches.length, 0);

// нагадування: посилка покупцю йде 7 днів
const rows = ctx.ledgerRows_();
const r7 = ctx.dueReminders_(rows, { 2: { sent: '2026-09-28', outTrack: 'H1', outCarrier: 'Hermes' } }, '2026-10-06');
check('buyer parcel 8 days → reminder with link', r7.lines.some((l) => /№2 .*посилка покупцю йде вже 8 дн.*myhermes/.test(l)), true);
const rk = ctx.dueReminders_(rows.map((r) => (r.n === 1 ? Object.assign({}, r, { status: 'В дорозі' }) : r)), { 1: { track: '00340434292135100186', carrier: 'DHL' } }, '2026-10-09');
check('KA reminder carries track link', rk.lines.some((l) => /№1 .*dhl\.de/.test(l)), true);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
