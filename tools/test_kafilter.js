// Перевірка фільтра шахрайських відповідей KA (node tools/test_kafilter.js)
const fs = require('fs');
const vm = require('vm');
const ctx = {};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(__dirname + '/kafilter.gs', 'utf8') + ';this.r = kaScamReason_; this.t = kaMessageText_;', ctx);
const cases = [
  ['Hi, haben Sie pay .Pal? Die ist noch unausgepackt Lg', true],   // справжня відповідь 28.09 (PS5)
  ['Hallo, zahlung per PayPal Freunde und Familie', true],
  ['Ja ist noch da. Schreib mir auf WhatsApp 0151 23456789', true],
  ['Gerne, nur Überweisung möglich', true],
  ['Paypal?', true],
  ['kontakt: max.mustermann@gmail.com', true],
  ['Ich kann auch per PayPal senden', true],
  ['F&F bitte', true],
  ['Schreib mir auf W h a t s A p p', true],
  ['Ja, noch verfügbar. Versand über Sicher bezahlen geht klar.', false],
  ['Hallo, ja ist noch da, lief immer fehlerfrei. Rechnung habe ich nicht.', false],
  ['PayPal Waren und Dienstleistungen ist ok', false],
  ['Du kannst es direkt kaufen, dann reserviere ich', false],
  ['Hi es sind 4×4 GB RAM für den preis passt es', false],
];
let bad = 0;
for (const [s, want] of cases) {
  const got = !!ctx.r(ctx.t(s));
  if (got !== want) { bad++; console.log('!!', s, got); }
}
console.log(bad ? 'FAILED ' + bad : 'OK');
process.exit(bad ? 1 : 0);
