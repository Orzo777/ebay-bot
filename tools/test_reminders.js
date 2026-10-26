// Захист покупок і нагадування ledger.gs (node tools/test_reminders.js) — з підмінними Google-сервісами
const fs = require('fs');
const vm = require('vm');
const store = { TELEGRAM_CHAT_ID: '7', LEDGER_ID: 'L', OFFICE_BOT_TOKEN: 'O', GITHUB_TOKEN: 'g' };
const sent = [];
let now = { 'yyyy-MM-dd': '2026-10-06', H: '10' };
const pad = (x) => ('0' + x).slice(-2);
const ctx = {
  console: { log: () => {} },
  Utilities: {
    // «зараз» — підмінна дата; дати з таблиці — справжні
    formatDate: (d, tz, f) => {
      if (Math.abs(d.getTime() - Date.now()) < 60000) return now[f];
      return { 'yyyy-MM-dd': d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()),
               'dd.MM': pad(d.getUTCDate()) + '.' + pad(d.getUTCMonth() + 1) }[f];
    },
  },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => store[k] || null, setProperty: (k, v) => { store[k] = v; } }) },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
const W = 23;
const D = (s) => new Date(s + 'T10:00:00Z');
const row = (o) => { const r = new Array(W).fill(''); Object.entries(o).forEach(([k, v]) => { r[k] = v; }); return r; };
const data = [
  row({ 1: D('2026-10-01'), 2: 'G.Skill Ripjaws 2x16GB DDR4 SO-DIMM', 3: 'RAM', 4: 'Kleinanzeigen', 10: 'Оплачено' }),   // 1: KA, 5 дн.
  row({ 1: D('2026-09-26'), 2: 'Corsair Vengeance LPX 32GB DDR4', 3: 'RAM', 4: 'Kleinanzeigen', 10: 'В дорозі' }),     // 2: KA, відпр. 27.09
  row({ 1: D('2026-09-25'), 2: 'PS5 Slim Disc', 3: 'Консоль', 4: 'eBay', 10: 'Оплачено' }),                             // 3: eBay, 11 дн.
  row({ 1: D('2026-09-28'), 2: 'SK Hynix 2x16GB ECC UDIMM', 3: 'RAM', 4: 'Kleinanzeigen', 10: 'Отримано' }),            // 4: отримано 03.10
  row({ 1: D('2026-09-30'), 2: 'Xbox Series X', 3: 'Консоль', 4: 'eBay', 10: 'Отримано' }),                             // 5: без дати отримання
  row({ 1: D('2026-09-20'), 2: 'OWC 2x16GB DDR4', 3: 'RAM', 4: 'eBay', 10: 'Продано', 11: D('2026-10-05') }),           // 6: продано вчора
  row({ 1: D('2026-09-01'), 2: 'Dell OptiPlex', 3: 'ПК', 4: 'Самовивіз Гамбург', 10: 'Оплачено' }),                    // 7: самовивіз
  row({ 1: D('2026-09-01'), 2: 'Switch OLED', 3: 'Консоль', 4: 'eBay', 10: 'Скасовано' }),                              // 8
  row({ 1: D('2026-08-01'), 2: 'Crucial DDR5', 3: 'RAM', 4: 'eBay', 10: 'Продано', 11: D('2026-09-01') })];             // 9: давно
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
const has = (name, text, re) => { if (!re.test(text)) { bad++; console.log('!!', name, JSON.stringify(text)); } };

// чисті правила
const rows = ctx.ledgerRows_();
const ev = { 2: { shipped: '2026-09-27' }, 4: { got: '2026-10-03', rem: { test0: 1 } } };
let r = ctx.dueReminders_(rows, ev, '2026-10-06');
check('who gets reminded', r.marks, [[1, 'ka5'], [2, 'ka8'], [3, 'eb10'], [4, 'test2'], [5, 'test0'], [6, 'ship1']]);
has('KA 5 days: deadline from purchase', r.lines[0], /до 11\.10/);
has('KA 9 days from shipment: 10-day deadline + 14-day payout', r.lines[1], /до 07\.10.*14-й день/);
has('eBay 11 days', r.lines[2], /Artikel nicht erhalten/);
has('tested? KA claim deadline', r.lines[3], /3 дн\. тому.*до 08\.10 \(10 днів від покупки\).*перевірив 4/);
has('received: RAM test tip for console? no — console tip', r.lines[4], /контролер/);
has('sold yesterday', r.lines[5], /відправ.*відправив 6/);
check('got date set for row without it', r.got, [5]);
check('no self-pickup / cancelled / old sale', r.lines.some((l) => /OptiPlex|Switch OLED|Crucial/.test(l)), false);
r.marks.forEach(([n, k]) => { ev[n] = ev[n] || {}; ev[n].rem = Object.assign(ev[n].rem || {}, { [k]: 1 }); });
check('each reminder once', ctx.dueReminders_(rows, ev, '2026-10-06').lines.length, 0);
r = ctx.dueReminders_(rows, ev, '2026-10-07');
check('next day: KA 6 days — nothing new; sold 2 days — second nudge', r.marks, [[6, 'ship2']]);
check('KA 8 days → urgent', ctx.dueReminders_(rows, ev, '2026-10-09').marks.map((m) => m.join(':')), ['1:ka8', '6:ship2']);
// продажі пішли → варіант А і податкові пороги (раз)
const many = rows.concat(Array.from({ length: 8 }, (_, i) => ({ n: 50 + i, date: '2026-09-20', title: 'x', src: 'eBay', status: 'Продано', sdate: '2026-09-29' })));
has('sales up', ctx.dueReminders_(many, ev, '2026-10-06').lines.join('\n'), /Продажі пішли: 9 за 30 днів/);

// заплановане
const T = vm.runInContext('LEDGER_TODO', ctx);
check('HDD on 15.10', ctx.dueTodos_(T, {}, '2026-10-15').lines.length === 1 && /HDD/.test(ctx.dueTodos_(T, {}, '2026-10-15').lines[0]), true);
check('nothing on 14.10', ctx.dueTodos_(T, {}, '2026-10-14').lines.length, 0);
const t25 = ctx.dueTodos_(T, {}, '2026-10-25');
check('price check monthly (+ unmarked HDD/PC still within 14 days)', [t25.lines.length, t25.lines.filter((l) => /продажами/.test(l)).length], [3, 1]);
const doneEv = { _: { rem: {} } };
ctx.dueTodos_(T, {}, '2026-10-25').marks.concat(ctx.dueTodos_(T, {}, '2026-10-15').marks).forEach(([, k]) => { doneEv._.rem[k] = 1; });
check('same period — once', ctx.dueTodos_(T, doneEv, '2026-10-30').lines.filter((l) => /продажами/.test(l)).length, 0);
check('next month — again', ctx.dueTodos_(T, doneEv, '2026-11-24').lines.filter((l) => /продажами/.test(l)).length, 1);
check('one-time not resent after long pause', ctx.dueTodos_(T, {}, '2026-11-05').lines.some((l) => /HDD/.test(l)), false);

// щоденне повідомлення о 10:00 — один раз
now = { 'yyyy-MM-dd': '2026-10-06', H: '9' };
check('9:00 — not yet', ctx.remindersIfDue_(ctx.PropertiesService.getScriptProperties(), new Date()), false);
now = { 'yyyy-MM-dd': '2026-10-06', H: '10' };
store.LEDGER_EVENTS = JSON.stringify({ 2: { shipped: '2026-09-27' }, 9: { sent: '2026-09-02' } });
check('10:00 — sends', ctx.remindersIfDue_(ctx.PropertiesService.getScriptProperties(), new Date()), true);
// 13.10: «☀️ Ранковий огляд» — стан, «Зробити сьогодні» (щодня, поки не зроблено), «Увага» (строки — раз), кнопки
const dg = sent[0].replace(/<[^>]+>/g, '');   // 14.10: HTML — текст без тегів; блок «Увага» — у цитаті
check('one message', [sent.length, /^☀️ Ранковий огляд · 06\.10\n💶 (Вкладено з кишені \d+ €|На руках грошей).*з них у товарі \d+ € \(за ціною купівлі\)[\s\S]*📈 Прибуток за жовтень/.test(dg),
  (dg.match(/№\d/g) || []).length], [1, true, 6]);   // 24.10: гроші словами
has('to do', dg, /✅ Зробити сьогодні\n1\. 🧪 Протестуй №4 .*проблему в KA — до 08\.10\n2\. 🧪 Протестуй №5 .*\n3\. 📮 Відправ №6 .* — продано вчора/);
has('warnings short', dg, /⚠️ Увага\n📦 №1 .*напиши продавцю \(проблему в KA — до 11\.10\)/);
check('warnings: one line each', dg.split('⚠️ Увага')[1].trim().split('\n\n').every((l) => !l.includes('\n')), true);
has('warnings in a quote', sent[0], /<b>⚠️ Увага<\/b>\n<blockquote>📦 №1/);
const saved = JSON.parse(store.LEDGER_EVENTS);
check('saved: got date, marks, old sale pruned', [saved[5].got, saved[1].rem.ka5, saved[9]], ['2026-10-06', 1, undefined]);
check('again same day — no', ctx.remindersIfDue_(ctx.PropertiesService.getScriptProperties(), new Date()), false);

// команди
const say = (text, chat) => vm.runInContext('officeMessage(__m)', Object.assign(ctx, { __m: { text: text, chat: { id: chat || 7 } } }));
sent.length = 0;
say('отримав 1');
check('отримав → status', cells['5:11'], 'Отримано');
has('отримав → how to test + KA deadline', sent[0], /Протестуй.*MemTest86.*до 11\.10.*перевірив 1/s);
check('отримав → date + test0', [JSON.parse(store.LEDGER_EVENTS)[1].got, JSON.parse(store.LEDGER_EVENTS)[1].rem.test0], ['2026-10-06', 1]);
// 14.10: синоніми — «Забрав 1» = «отримав 1»; без номера — не команда
check('synonyms', [ctx.canonCmd_('Забрав 2'), ctx.canonCmd_('прийшло 3 сьогодні'), ctx.canonCmd_('протестувала 4'),
  ctx.canonCmd_('прийшло щось дивне'), ctx.canonCmd_('продати 3')],
  ['отримав 2', 'отримав 3 сьогодні', 'перевірив 4', 'прийшло щось дивне', 'продати 3']);
say('перевірила 1');
check('перевірив → status', [cells['5:11'], /підтвердити отримання в KA/.test(sent[1])], ['Перевірено', true]);
say('проблема 3');
check('проблема eBay', [cells['7:11'], /nicht wie beschrieben/.test(sent[2])], ['Проблема', true]);
say('відправив 6');
check('відправив → sent', [JSON.parse(store.LEDGER_EVENTS)[6].sent, /більше не нагадую/.test(sent[3])], ['2026-10-06', true]);
say('відправив 1');
has('відправив not sold', sent[4], /для проданого/);
say('отримав 8');
has('cancelled row untouched', sent[5], /Скасовано/);
// 19.10: «випадково натиснув „отримав“» — «назад N» повертає статус і події; «статус N …» — будь-який
say('назад 3');
check('undo problem', [cells['7:11'], /повернув статус «Оплачено»/.test(sent[sent.length - 1])], ['Оплачено', true]);
say('назад 1');   // останнім було «перевірила 1» (з «Отримано»): статус і дата тесту — назад, отримання лишається
check('undo tested', [cells['5:11'], JSON.parse(store.LEDGER_EVENTS)[1].tested, JSON.parse(store.LEDGER_EVENTS)[1].got], ['Оплачено', undefined, '2026-10-06']);   // підмінна таблиця: статус рядка — з __data (Оплачено)
say('назад 2');
has('nothing to undo', sent[sent.length - 1], /немає що скасовувати.*статус 2 Оплачено/);
say('статус 5 оплач');
check('set any status', [cells['9:11'], /«Отримано» → «Оплачено»/.test(sent[sent.length - 1])], ['Оплачено', true]);
say('статус 5 щось');
has('unknown status', sent[sent.length - 1], /Такого статусу немає/);
check('undo button in notices', /\['назад', n\]/.test(fs.readFileSync(__dirname + '/ledger.gs', 'utf8')), true);
say('нагадай 20.10 забрати посилку з DHL');
check('нагадай stored', JSON.parse(store.USER_TODO), [['2026-10-20', 'забрати посилку з DHL']]);
say('нагадай 01.02 перевірити податки');
check('past date → next year', JSON.parse(store.USER_TODO)[1][0], '2027-02-01');
say('нагадування');
has('list', sent[sent.length - 1], /15\.10 — HDD[\s\S]*20\.10 — забрати[\s\S]*25\.10 — Звірити[\s\S]*кожні 30/);
check('user todo fires', ctx.dueTodos_(JSON.parse(store.USER_TODO), {}, '2026-10-20').lines, ['📅 забрати посилку з DHL']);
const before = sent.length;
say('отримав 2', 99);
check('foreign chat — silent', [sent.length, cells['6:11']], [before, undefined]);
check('main bot routes office commands', /отримав\|отримала\|перевірив/.test(fs.readFileSync(__dirname + '/gmail_trigger.gs', 'utf8')), true);
// 26.10: продане й доставлене — подія лишається (раніше стиралась і наступного ранку знову «📮 Відправ №…», а звірка з eBay
// записувала відправку й доставку наново, щодня); «Відправ» — не нагадуємо
now = { 'yyyy-MM-dd': '2026-10-07', H: '10' };
store.LEDGER_EVENTS = JSON.stringify(Object.assign(JSON.parse(store.LEDGER_EVENTS),
  { 6: { sent: '2026-10-05', outTrack: '354502403646', outCarrier: 'dhl', outDelivered: '2026-10-06' } }));
sent.length = 0;
ctx.remindersIfDue_(ctx.PropertiesService.getScriptProperties(), new Date());
check('delivered sale: events kept, no «send» in digest', [!!JSON.parse(store.LEDGER_EVENTS)[6],
  sent.some((t) => /Відправ №6/.test(t))], [true, false]);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
