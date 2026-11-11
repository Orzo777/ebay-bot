// Огляд листів KA для перевірки стану (node tools/test_mailprobe.js): у відповідь — лише знеособлене
const fs = require('fs');
const vm = require('vm');
let bad = 0;
const check = (name, got, want) => { if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('!!', name, JSON.stringify(got)); } };
const NOW = Date.now();
const store = { KA_DIGESTED: JSON.stringify(['m1']), KA_FILTER_TS: String(NOW - 4 * 60000) };
const th = (id, mid, from, subj, h, o) => Object.assign({
  getId: () => id, isInInbox: () => true, isInSpam: () => false, isInTrash: () => false,
  getMessages: () => [{ getId: () => mid, getFrom: () => from, getSubject: () => subj, getDate: () => new Date(NOW - h * 3600e3) }] }, o || {});
const threads = [
  th('t1', 'm1', '"Max Mustermann über Kleinanzeigen" <a8f3k2l9x@mail.kleinanzeigen.de>', 'Nutzer-Anfrage zu deiner Anzeige „Corsair Vengeance 32GB“ von Max', 3),
  th('t2', 'm2', 'Kleinanzeigen <noreply@kleinanzeigen.de>', 'Deine Anzeige „G.Skill 32GB“ ist jetzt online', 20, { isInInbox: () => false, isInSpam: () => true }),
];
const ctx = {
  console: { log: () => {} },
  PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => store[k] || null, setProperty: (k, v) => { store[k] = v; } }) },
  GmailApp: { search: (q) => (/subject:\(nachricht/.test(q) ? [threads[0]] : /bestellbestätigung/.test(q) ? [] : threads) },
};
vm.createContext(ctx);
vm.runInContext(['gmail_trigger.gs', 'ledger.gs', 'kafilter.gs'].map((f) => fs.readFileSync(__dirname + '/' + f, 'utf8')).join('\n'), ctx);
const p = ctx.mailProbe_();
check('summary', [p.filterTs, p.digested, p.gmailOff], [4, 1, false]);
check('buyer message', p.list[0], { h: 3, from: '*@mail.kleinanzeigen.de', subj: '• zu deiner Anzeige •… von •', n: 1,
  msgq: true, ledger: false, digested: true, inbox: true, spam: false, trash: false });
check('listing mail in spam', [p.list[1].from, p.list[1].subj, p.list[1].spam, p.list[1].msgq], ['noreply@kleinanzeigen.de', 'Deine Anzeige •… ist jetzt online', true, false]);
check('no names or titles leak', /Max|Corsair|Mustermann|G\.Skill|a8f3/.test(JSON.stringify(p)), false);
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
