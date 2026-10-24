/**
 * Фільтр шахрайських відповідей Kleinanzeigen у Gmail (Google Apps Script, той самий проєкт).
 *
 * Картки бота відсіюють шахраїв за оголошенням, але частина пише вже у відповідь: «Haben Sie PayPal?»,
 * «PayPal Freunde», «schreib mir auf WhatsApp», «Überweisung» (30.09: ~9 з 10 відповідей). Цей скрипт щохвилини
 * переглядає листи-сповіщення KA про нові повідомлення і такі — позначає міткою «KA скам» і переносить у Кошик
 * (Gmail зберігає Кошик 30 днів — якщо фільтр помилився, лист можна повернути: мітка «KA скам» у лівому меню).
 * Перевіряє кожні 5 хв лише нові листи. Сам чат у Kleinanzeigen не зачіпається — лише лист на пошті.
 *
 * Чесні відповіді (01.10) — коротко в основний бот: від кого, на що, суть («згоден на ціну», «продано»…), переклад.
 * Переклад і суть робить Gemini (властивість скрипту GEMINI_API_KEY — той самий ключ, що в секретах GitHub);
 * без ключа — німецький текст і суть за ключовими словами.
 *
 * Установка: «+» → «Скрипт» → kafilter → вставити цей файл → 💾 → функція installKaFilter → «Виконати» → дозволити.
 * Перевірити дайджест: функція kaDigestTest → «Виконати» — надішле в бот 2 останні чесні відповіді.
 * Перевірити без видалення: функція kaFilterDryRun → «Виконати» → у журналі список, що було б прибрано.
 */

// Лише розмови з новими листами після попереднього проходу (after:) — щохвилинний перегляд 50 розмов за 3 дні
// разом із check() вичерпав денний Gmail-ліміт Google 01.10.
const VER_KAFILTER = '2026-10-24c';   // версія файлу: сторож порівнює з GitHub і нагадує оновити (при зміні файлу — підняти)
const KA_MSG_QUERY = 'from:kleinanzeigen subject:(nachricht OR antwort OR anfrage OR geschrieben OR schrieb OR preisvorschlag) ' +
  '-subject:(gekauft OR verkauft OR bestellung OR zahlung OR versand OR Suchauftrag) -label:"KA скам"';

// Оплата без захисту покупця, контакт поза KA, передоплата — те саме, що блокує бот в описах оголошень
const KA_SCAM = new RegExp([
  'paypal\\s*[-.:,/]?\\s*(?:an\\s+)?(?:freunde|friends|familie|family|f\\s*&\\s*f|fnf|ff\\b|privat)',
  'freunde\\s*(?:und|&|\\+)\\s*familie', 'familie\\s*(?:und|&)\\s*freunde', 'friends\\s*(?:and|&)\\s*family',
  '\\bf\\s?&\\s?f\\b', '\\bfnf\\b', 'whats\\s?app', 'wa\\.me', '\\btelegram\\b', '\\bsignal\\b', '\\bthreema\\b',
  '[üu]e?berweisung', 'vorkasse', 'echtzeit', '\\bwero\\b', 'paypal\\.me', 'anzahlung', 'kaution', 'treuhand',
  'zahlungslink', 'zahlungs-?link',
  '(?:\\+49|0049|\\b0)\\s?1[5-7]\\d[\\s/-]?\\d{3,}',
  '[\\w.+-]+@(?:gmail|gmx|web|yahoo|outlook|hotmail|icloud|t-online|aol|mail)\\.',
  '(?:haben|hast)\\s+(?:sie|du)\\s+(?:auch\\s+)?paypal', 'paypal\\s*\\?'].join('|'), 'i');
// «PayPal» просто так — шахрайське, якщо поруч немає «Waren und Dienstleistungen» / «Käuferschutz» / «Sicher bezahlen»
const KA_PAYPAL = /paypal/i;
const KA_SAFE = /waren\s*(?:und|&)\s*dienstleistung|käuferschutz|kaeuferschutz|sicher\s+bezahlen|direkt\s+kaufen/i;

function kaScamReason_(text) {
  const m = text.match(KA_SCAM);
  if (m) return m[0];
  // «pay .Pal», «Whats App», «W.h.a.t.s.A.p.p» — шахраї розбивають слова, щоб обійти фільтри (30.09: «haben Sie pay .Pal?»)
  const sq = String(text).toLowerCase().replace(/[^a-zäöüß0-9&+]/g, '');
  const sm = sq.match(/whatsapp|telegram|threema|freunde(?:und|&|\+)familie|friends(?:and|&)family|paypal(?:ff|fnf|f&f|freunde|friends)/);
  if (sm) return sm[0];
  if ((KA_PAYPAL.test(text) || /paypal/.test(sq)) && !KA_SAFE.test(text)) return 'PayPal без «Waren und Dienstleistungen»';
  return '';
}

// Лише текст повідомлення, без шаблонної «шапки»/«підвалу» KA (там можуть бути слова на кшталт «Sicher bezahlen»)
function kaMessageText_(body) {
  return String(body || '').replace(/\s+/g, ' ')
    .replace(/(?:Sicherheitshinweis|Sicherheitstipps?|Tipps? für (?:sicheres|deine Sicherheit))[\s\S]*$/i, '');
}

function kaFilter_(dry) {
  const props = PropertiesService.getScriptProperties();
  const started = Date.now();
  const since = dry ? started - 3 * 86400e3 : (Number(props.getProperty('KA_FILTER_TS')) || started - 86400e3) - 5 * 60e3;
  let label = null;
  let n = 0;
  GmailApp.search(KA_MSG_QUERY + ' after:' + Math.floor(since / 1000), 0, dry ? 50 : 20).forEach(function (th) {
    const msgs = th.getMessages();
    const last = msgs[msgs.length - 1];
    const reason = kaScamReason_(kaMessageText_(last.getPlainBody()));
    if (!reason) {
      if (!dry) digestOnce_(props, last);
      return;
    }
    n++;
    console.log((dry ? '[перевірка] ' : '') + 'скам (' + reason + '): ' + last.getSubject());
    if (!dry) {
      label = label || GmailApp.getUserLabelByName('KA скам') || GmailApp.createLabel('KA скам');
      th.addLabel(label);
      th.moveToTrash();
    }
  });
  if (!dry) props.setProperty('KA_FILTER_TS', String(started));
  console.log(n ? 'прибрано: ' + n : 'шахрайських відповідей немає');
}

function kaFilter() { kaFilter_(false); }
function kaFilterDryRun() { kaFilter_(true); }

function installKaFilter() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'kaFilter') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('kaFilter').timeBased().everyMinutes(5).create();
  kaFilterDryRun();
  console.log('фільтр увімкнено (кожні 5 хв)');
}


// ------------------------------------------------------------------ чесні відповіді → бот (01.10)
const KA_INTENTS = { hacked: '🚨 Пише, що НЕ викладав це оголошення (акаунт зламали) — фейк, не купуй', agree: '✅ Згоден', counter: '💬 Пропонує іншу ціну', sold: '⛔ Уже продано / зарезервовано',
  available: '🟢 Ще є', question: '❓ Питає', other: '✉️ Відповідь' };

function digestOnce_(props, m) {
  const seen = JSON.parse(props.getProperty('KA_DIGESTED') || '[]');
  if (seen.indexOf(m.getId()) >= 0) return;
  try {
    kaDigest_(m, props);
  } catch (e) {
    console.log('дайджест: ' + e);
  }
  seen.push(m.getId());
  props.setProperty('KA_DIGESTED', JSON.stringify(seen.slice(-300)));
}

// Суть без Gemini — за ключовими словами (німецька)
function kaIntent_(text) {
  const t = String(text || '').toLowerCase();
  // 02.10: «я не викладав це оголошення, акаунт зламали» — шахраї публікують фейки зі старих чужих акаунтів
  if (/gehackt|nicht von mir|nicht ich|(?:habe|hab)\s+(?:diese|die|keine)\s+anzeige|(?:konto|account|profil)\s+(?:wurde\s+)?(?:gehackt|übernommen|missbraucht)|fake-?anzeige|betrüger\s+(?:haben|hat)/.test(t)) return 'hacked';
  if (/verkauft|schon weg|nicht mehr (?:da|verfügbar|zu haben)|reserviert|vergeben/.test(t)) return 'sold';
  if (/(?:letzte[rn]?|mindest|unter|für)\s+(?:preis\s*:?\s*)?\d+\s*(?:€|euro)|preis\s*:?\s*\d+\s*(?:€|euro)|\d+\s*(?:€|euro)\s+(?:wäre|ist|würde|geht|kann)/.test(t)) return 'counter';
  if (/\b(?:einverstanden|deal|abgemacht|geht klar|können wir so machen)\b|\d+\s*(?:€|euro)?\s*(?:ist\s+)?(?:ok|passt)\b/.test(t)) return 'agree';
  if (/noch (?:da|verfügbar|zu haben)|ist noch|ja,? (?:ist|gibt)/.test(t)) return 'available';
  if (/\bpasst\b|^\s*(?:ja|ok|okay|gerne)\b/.test(t)) return 'agree';
  if (/\?/.test(t)) return 'question';
  return 'other';
}

function kaGemini_(text, subject, key) {
  const prompt = 'Це лист-сповіщення Kleinanzeigen про нове повідомлення (тема: "' + subject + '"). Користувач і купує, і продає.\n' +
    'Поверни JSON: {"from": "seller" — пише продавець чужого оголошення у відповідь користувачу (користувач — покупець) | ' +
    '"buyer" — пише зацікавлений покупець щодо оголошення САМОГО користувача («zu deiner Anzeige», питає, чи ще є, пропонує ціну), ' +
    '"seller": ім\'я того, хто пише, або "", "listing": назва оголошення або "", "message": лише текст повідомлення ' +
    'мовою оригіналу (без шаблону листа), "uk": переклад повідомлення українською, коротко й точно, "intent": одне з ' +
    'hacked|agree|counter|sold|available|question|other (hacked — пише, що не викладав оголошення / акаунт зламано; agree — згоден на запропоновану ціну/купівлю; counter — називає іншу ціну; ' +
    'sold — продано/зарезервовано; available — лише каже, що ще є; question — питає), "price": число в євро, якщо в ' +
    'повідомленні названо ціну (ціна продавця або пропозиція покупця), інакше null}.\n\nЛист:\n' + text;
  const models = ['gemini-flash-lite-latest', 'gemini-flash-latest'];
  for (let i = 0; i < models.length; i++) {
    const r = UrlFetchApp.fetch('https://generativelanguage.googleapis.com/v1beta/models/' + models[i] + ':generateContent', {
      method: 'post', contentType: 'application/json', muteHttpExceptions: true, headers: { 'x-goog-api-key': key },
      payload: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }],
        generationConfig: { temperature: 0, responseMimeType: 'application/json' } }) });
    if (r.getResponseCode() !== 200) { console.log('Gemini ' + models[i] + ': ' + r.getResponseCode()); continue; }
    try {
      const out = JSON.parse(JSON.parse(r.getContentText()).candidates[0].content.parts[0].text.replace(/^```(?:json)?|```$/g, ''));
      if (out && out.message) return out;
    } catch (e) { console.log('Gemini: не JSON'); }
  }
  return null;
}

function esc_(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

function kaDigestText_(subject, text, g) {
  const listing = (g && g.listing) || ((subject.match(/[„"“]([^“"]+)[“"]/) || [])[1]) || '';
  const seller = (g && g.seller) || ((subject.match(/\bvon\s+(.+?)(?:\s+(?:zu|zur|zum|für)\b|$)/i) || [])[1]) || '';
  const intent = (g && KA_INTENTS[g.intent]) ? g.intent : kaIntent_(text);
  const msg = (g && g.message) || text.slice(0, 600);
  const lines = ['💬 <b>Відповідь продавця</b>' + (seller ? ' · ' + esc_(seller) : ''),
    listing ? '<i>' + esc_(listing.slice(0, 90)) + '</i>' : '', '',
    '<b>' + KA_INTENTS[intent] + (g && g.price ? ' — ' + g.price + ' €' : '') + '</b>'];
  if (g && g.uk) lines.push('🇺🇦 ' + esc_(g.uk.slice(0, 700)));
  lines.push('🇩🇪 ' + esc_(msg.slice(0, 700)));
  return lines.filter(function (l, i) { return l !== '' || i === 2; }).join('\n');
}

/** 23.10 (користувач: «мені написав ПОКУПЕЦЬ, а написано — відповідь продавця»): лист про повідомлення щодо СВОГО
 *  оголошення (Patriot Viper на KA). Тема / шапка «… deiner Anzeige» або Gemini каже «buyer». */
function kaFromBuyer_(subject, body, g) {
  if (/\bdeine[rnms]?\s+anzeige\b/i.test(String(subject || '') + ' ' + String(body || '').slice(0, 400))) return true;
  return !!(g && g.from === 'buyer');
}

/** 24.10 (користувач: «у мене „лише самовивіз“, а пише „20 € з доставкою?“ — тупе питання, не треба в Telegram»):
 *  покупець просить доставку, а наше оголошення «Nur Abholung» → без сповіщення (лист лишається в KA).
 *  Сторінку оголошення читаємо раз на добу (кеш KA_PICKUP_<id>). */
const KA_ASKS_SHIPPING = /versand|verschick|zuschick|schicken|porto|zusenden|senden\s+sie|per\s+post|paket|dhl|hermes|shipping/i;
function kaAdInfo_(id) {
  if (!id) return {};
  const props = PropertiesService.getScriptProperties(), key = 'KA_AD_' + id, day = new Date().toISOString().slice(0, 10);
  try { const c = JSON.parse(props.getProperty(key) || 'null'); if (c && c.d === day) return c; } catch (e) { /* перечитаємо */ }
  let page = '';
  try {
    page = UrlFetchApp.fetch('https://www.kleinanzeigen.de/s-anzeige/' + id, { muteHttpExceptions: true,
      headers: { 'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'de-DE' } }).getContentText();
  } catch (e) { console.log('KA сторінка: ' + e); return {}; }
  const info = { d: day, pickup: /nur\s*(?:selbst)?abholung/i.test(page) && !/\+\s*versand\s+ab|versand\s+m(?:ö|oe)glich|direkt\s+kaufen/i.test(page),
    price: Number((page.match(/itemprop="price"\s+content="(\d+(?:\.\d+)?)"/) || [])[1]) || null };
  props.setProperty(key, JSON.stringify(info));
  return info;
}
function kaPickupOnly_(id) { return !!kaAdInfo_(id).pickup; }

/** 24.10 (користувач: «занизькі теж відсіювати — поріг як на eBay»): пороги пропозиції на НАШЕ оголошення KA.
 *  Ціна — з «виставив N ka ціна» (подія kaPrice) або зі сторінки; мінімум прибутку — від ціни купівлі в обліку.
 *  → {price, accept, decline} або null (не наше / ціни не знаємо). */
function kaOfferBounds_(ids) {
  for (let i = 0; i < (ids || []).length; i++) {
    const id = String(ids[i]);
    let n = 0, ev = {};
    if (typeof events_ === 'function') {
      const all = events_();
      n = Number(Object.keys(all).filter(function (k) { return String((all[k] || {}).ka) === id; })[0] || 0);
      ev = n ? all[n] : {};
    }
    const price = Number(ev.kaPrice) || kaAdInfo_(id).price;
    if (!price) continue;
    const r = n && typeof rowByN_ === 'function' ? rowByN_(n) : null, cost = r ? Number(r.spent) || 0 : 0;
    const floor = cost ? Math.ceil(cost + Math.max(5, 0.1 * cost) + 1) : 0;
    const accept = Math.max(floor, Math.round(price * 0.93));
    return { price: price, accept: accept, decline: Math.min(accept, Math.max(floor, Math.round(price * 0.87))) };
  }
  return null;
}

function kaShippingOnPickup_(msg, ids) {
  return KA_ASKS_SHIPPING.test(String(msg || '')) && (ids || []).some(kaPickupOnly_);
}

function kaBuyerText_(subject, body, g, bounds) {
  const listing = kaListing_(subject, g), who = (g && g.seller) || '';
  const msg = (g && g.message) || String(body || '').slice(0, 600), scam = kaScamReason_(msg);
  const offer = g && Number(g.price);
  return ['🛍 <b>Покупець пише</b>' + (who ? ' · ' + esc_(who) : ''), listing ? '<i>' + esc_(listing.slice(0, 90)) + '</i>' : '', '',
    scam ? '⛔ <b>Схоже на шахрая</b> («' + esc_(scam) + '») — не відповідай і не відкривай посилань' : '',
    offer ? '💶 Пропонує <b>' + offer + ' €</b>' + (bounds ? (offer >= bounds.accept
      ? ' — ✅ не нижче порогу (від ' + bounds.accept + ' € при ціні ' + bounds.price + ' €): можна погоджуватись'
      : ' — зустрічна: <b>' + bounds.accept + ' €</b> (ціна ' + bounds.price + ' €)') : '') : '',
    g && g.uk ? '🇺🇦 ' + esc_(g.uk.slice(0, 700)) : '', '🇩🇪 ' + esc_(msg.slice(0, 700)),
    scam ? '' : 'Продаєш — лише через «Direkt kaufen» / «Sicher bezahlen» в KA; посилань покупця не відкривай.']
    .filter(function (l, i) { return l !== '' || i === 2; }).join('\n');
}

function kaDigest_(m, props) {
  const tok = props.getProperty('TELEGRAM_BOT_TOKEN'), chat = props.getProperty('TELEGRAM_CHAT_ID');
  if (!tok || !chat) return;
  const body = kaMessageText_(m.getPlainBody());
  const key = props.getProperty('GEMINI_API_KEY');
  const g = key ? kaGemini_(body.slice(0, 3000), m.getSubject(), key) : null;
  const link = (String(m.getBody() || '').match(/https:\/\/(?:www\.)?kleinanzeigen\.de\/m-nachrichten[^"'\s<>]*/) ||
                String(m.getBody() || '').match(/https:\/\/(?:www\.)?kleinanzeigen\.de\/s-anzeige\/[^"'\s<>]*/) || [])[0];
  if (kaFromBuyer_(m.getSubject(), body, g)) {   // покупець щодо НАШОГО оголошення — у бот «Облік і продаж», без реплаю на картку
    const ids = kaAdIds_(String(m.getBody() || '') + ' ' + body);
    if (kaShippingOnPickup_((g && g.message) || body, ids)) {
      console.log('KA: покупець просить доставку, а в нас лише самовивіз — без сповіщення');
      return;
    }
    const bounds = g && Number(g.price) ? kaOfferBounds_(ids) : null;
    if (bounds && Number(g.price) < bounds.decline) {
      console.log('KA: пропозиція нижче порогу (' + g.price + ' < ' + bounds.decline + ') — без сповіщення');
      return;
    }
    const bkb = { inline_keyboard: [[{ text: '💬 Відкрити чат на Kleinanzeigen', url: link || 'https://www.kleinanzeigen.de/m-nachrichten.html' }]] };
    if (typeof notify_ === 'function') { notify_(kaBuyerText_(m.getSubject(), body, g, bounds), bkb, 'HTML'); return; }
    UrlFetchApp.fetch('https://api.telegram.org/bot' + tok + '/sendMessage', { method: 'post', muteHttpExceptions: true,
      payload: { chat_id: chat, text: kaBuyerText_(m.getSubject(), body, g, bounds), parse_mode: 'HTML', disable_web_page_preview: 'true',
                 reply_markup: JSON.stringify(bkb) } });
    return;
  }
  const text = kaDigestText_(m.getSubject(), body, g);
  // 24.10 (користувач: «мухи окремо, котлети окремо»): ebay_bot / pc_bot — лише картки угод; усе, що далі (листування з
  // продавцем, покупки, продажі), — у бот «Облік і продаж». Відповідь продавця — туди ж, з кнопками на чат і оголошення
  // (раніше — реплаєм на картку в ebay_bot через research/ka_reply.py).
  const ad = kaAdIds_(String(m.getBody() || '') + ' ' + body)[0];
  const kb = { inline_keyboard: [[{ text: '💬 Відкрити чат на Kleinanzeigen', url: link || 'https://www.kleinanzeigen.de/m-nachrichten.html' }]]
    .concat(ad ? [[{ text: '🔗 Оголошення', url: 'https://www.kleinanzeigen.de/s-anzeige/' + ad }]] : []) };
  if (typeof notify_ === 'function') { notify_(text, kb, 'HTML'); return; }
  const markup = JSON.stringify(kb);
  UrlFetchApp.fetch('https://api.telegram.org/bot' + tok + '/sendMessage', { method: 'post', muteHttpExceptions: true,
    payload: { chat_id: chat, text: text, parse_mode: 'HTML', disable_web_page_preview: 'true', reply_markup: markup } });
}

function kaListing_(subject, g) {
  return (g && g.listing) || ((String(subject).match(/[„"“]([^“"]+)[“"]/) || [])[1]) || '';
}

function kaAdIds_(s) {
  const out = [];
  String(s).replace(/kleinanzeigen\.de\/s-anzeige\/(?:[^\/?#\s"'<>]+\/)?(\d{6,})/g, function (_, id) {
    if (out.indexOf(id) < 0) out.push(id);
    return _;
  });
  return out;
}

function kaReplyViaGitHub_(props, text, markup, listing, ids) {
  if (typeof seal_ !== 'function' || typeof officeToken_ !== 'function') return false;
  const token = props.getProperty('GITHUB_TOKEN') || (typeof GITHUB_TOKEN === 'undefined' ? '' : GITHUB_TOKEN);
  if (!token) return false;
  const sealed = seal_({ text: text, markup: markup, listing: listing, ids: ids }, officeToken_(props),
                       Utilities.getUuid().replace(/-/g, ''));
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/ka_reply.yml/dispatches', {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ ref: 'main', inputs: sealed }) });
  return r.getResponseCode() === 204;
}

// Ручна перевірка: 2 останні чесні відповіді → бот (навіть якщо вже надсилались)
function kaDigestTest() {
  const props = PropertiesService.getScriptProperties();
  let n = 0;
  GmailApp.search(KA_MSG_QUERY + ' newer_than:14d', 0, 20).some(function (th) {
    const msgs = th.getMessages(), last = msgs[msgs.length - 1];
    if (kaScamReason_(kaMessageText_(last.getPlainBody()))) return false;
    kaDigest_(last, props);
    n++;
    return n >= 2;
  });
  console.log(n ? 'надіслано в бот: ' + n : 'чесних відповідей за 14 днів не знайшов');
}
