"""Автоматична перевірка листів Kleinanzeigen (Suchauftrag) поштою: парсить
назву/ціну/посилання, оцінює через console_alert (Xbox Series X) або ram_alert.evaluate() і шле в Telegram (той
самий бот/чат основного eBay-бота) картку з вердиктом, кнопкою-посиланням на
оголошення і кнопкою «скопіювати текст продавцю». Повідомлення НЕ надсилається
продавцю автоматично — це робить сам користувач з телефону.

Пошта відкривається ЛИШЕ для читання (readonly): бот не змінює позначки «прочитано»,
а дублікати відсіює за Message-ID у файлі стану. Тому лист, який користувач уже
відкрив на телефоні, бот все одно обробить (раніше шукали лише UNSEEN і пропускали такі).
Листи, старші за --max-age-hours, не сповіщаються: вигідний лот за стільки годин уже продано.

Потрібні секрети в оточенні:
    GMAIL_USER, GMAIL_APP_PASSWORD   — IMAP-доступ (пароль застосунку Google, НЕ звичайний пароль)
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — той самий бот, що й основний бот

Запуск: python research/ram_mail_check.py [--dry-run] [--state <path>] [--max-age-hours 6]
"""
import argparse
import email
import html
import imaplib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime

sys.path.insert(0, ".")
sys.path.insert(0, "research")
import config
from console_alert import evaluate_console
from ka_listing_check import check_listing, risk_lines
from ram_alert import SEND_VERDICTS, evaluate, format_html, offer_template, seller_template

FROM_FILTER = os.getenv("KA_MAIL_FROM_FILTER", "kleinanzeigen.de")


def _decode(s):
    if not s:
        return ""
    parts = decode_header(s)
    return "".join(p.decode(enc or "utf-8", errors="ignore") if isinstance(p, bytes) else p for p, enc in parts)


def _body_text(msg):
    if msg.is_multipart():
        chunks = []
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype in ("text/plain", "text/html") and "attachment" not in str(part.get("Content-Disposition") or ""):
                try:
                    chunks.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore"))
                except Exception:
                    pass
        return "\n".join(chunks)
    try:
        return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="ignore")
    except Exception:
        return ""


# Реальний формат листа-сповіщення Kleinanzeigen (Suchauftrag), звірено на живому
# зразку 22.09.2026: одна email може містити КІЛЬКА нових оголошень підряд, кожне —
# один блок виду: <img alt="Bild zur Anzeige <НАЗВА>"> ... "<ЦІНА> €" ... "Von Privat"
# / "Von Gewerblich" ... <a href="…/s-anzeige/<ID>?…" title="Anzeige ansehen">.
_TITLE_RE = re.compile(r'alt="Bild zur Anzeige ([^"]+)"')
_ADLINK_RE = re.compile(r'href="(https://www\.kleinanzeigen\.de/s-anzeige/\d+)[^"]*"[^>]*title="Anzeige ansehen"')
_PRICE_RE = re.compile(r"([\d.,]+)\s?€")
_GEWERBLICH_RE = re.compile(r"Von Gewerblich")
_SEARCH_RE = re.compile(r"m-suche-verwenden\.html\?id=(\d+)")
HINT_GAP_MIN = 45   # тиха підказка «глянь пошук» — не частіше разу на 45 хв на одну підписку


def _parse_price(s: str) -> float | None:
    s = s.strip().rstrip(".,")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def extract_listings(subject: str, body: str) -> list[dict]:
    """Розбиває тіло листа на блоки за міткою кожного оголошення (alt="Bild zur
    Anzeige …") і витягує з кожного блоку назву, ціну, посилання й тип продавця."""
    title_matches = list(_TITLE_RE.finditer(body))
    out = []
    for i, tm in enumerate(title_matches):
        start = tm.start()
        end = title_matches[i + 1].start() if i + 1 < len(title_matches) else len(body)
        segment = body[start:end]
        price_m = _PRICE_RE.search(segment)
        if not price_m:
            continue
        price = _parse_price(price_m.group(1))
        if price is None:
            continue
        link_m = _ADLINK_RE.search(segment)
        out.append(dict(
            title=html.unescape(tm.group(1)).strip(),   # «&amp;» у назві → «&»
            price=price,
            link=link_m.group(1) if link_m else None,
            gewerblich=bool(_GEWERBLICH_RE.search(segment)),
            vb=bool(re.search(r"€\s*VB", segment[price_m.start():price_m.end() + 12])),
        ))
    return out


def build_keyboard(link: str | None, seller_text: str, search: str | None = None, offer_text: str | None = None) -> dict:
    """Кнопки під карткою: відкрити оголошення + скопіювати ЛИШЕ текст продавцю
    (copy_text, Bot API 7.11+, ліміт 256 символів) + уся підписка (у листі лише одне з кількох нових)."""
    rows = []
    if link:
        rows.append([{"text": "🔗 Відкрити оголошення", "url": link}])
    rows.append([{"text": "📋 Скопіювати текст продавцю", "copy_text": {"text": seller_text[:256]}}])
    if offer_text:
        price = re.search(r"Wären (\d+) €", offer_text)
        label = f"📋 Текст із пропозицією {price.group(1)} €" if price else "📋 Текст із пропозицією ціни"
        rows.append([{"text": label, "copy_text": {"text": offer_text[:256]}}])
    if search:
        rows.append([{"text": "🔎 Інші нові збіги цієї підписки", "url": search}])
    return {"inline_keyboard": rows}


def send_telegram_card(html_text: str, link: str | None, seller_text: str, search: str | None = None,
                       silent: bool = False, offer_text: str | None = None):
    import requests

    url = f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": html_text, "parse_mode": "HTML",
               "disable_web_page_preview": "true", "disable_notification": "true" if silent else "false",
               "reply_markup": json.dumps(build_keyboard(link, seller_text, search, offer_text), ensure_ascii=False)}
    r = requests.post(url, data=payload, timeout=15)
    if r.status_code == 400:   # старий клієнт/API без copy_text — картка важливіша за кнопку
        print("   Telegram 400:", r.text[:200], "→ повтор без кнопки копіювання")
        kb = build_keyboard(link, seller_text, search, offer_text)
        kb["inline_keyboard"] = [row for row in kb["inline_keyboard"] if "url" in row[0]]
        payload["reply_markup"] = json.dumps(kb, ensure_ascii=False)
        r = requests.post(url, data=payload, timeout=15)
    r.raise_for_status()


def load_state(path):
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return {"seen_ids": []}


def save_state(path, state):
    state["seen_ids"] = state["seen_ids"][-2000:]
    state["seen_ads"] = state.get("seen_ads", [])[-3000:]
    json.dump(state, open(path, "w", encoding="utf-8"), ensure_ascii=False)


def _mail_age_hours(msg) -> float | None:
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return None


def _special_folder(m, flag: str, default: str | None) -> str | None:
    """Gmail-папка за службовим атрибутом (\\All, \\Trash): назви локалізовані ([Gmail]/Уся пошта…)."""
    typ, folders = m.list()
    for f in folders or []:
        line = f.decode(errors="ignore")
        if flag in line:
            name = line.rsplit(' "/" ', 1)[-1].strip()
            return name if name.startswith('"') else f'"{name}"'
    return default


def _all_mail_folder(m) -> str:
    return _special_folder(m, "\\All", "INBOX")


def _ad_key(lst: dict) -> str:
    """Одне оголошення приходить кількома листами (підпадає під кілька підписок) — ключ за
    номером оголошення + ціною: дубль відкидаємо, а зниження ціни сповіщаємо знову."""
    m = re.search(r"/s-anzeige/(\d+)", lst.get("link") or "")
    return f"{m.group(1) if m else lst['title'][:60]}@{lst['price']:.0f}"


def search_link(body: str) -> tuple[str | None, str | None]:
    """Посилання «Neue Treffer ansehen» → (id підписки, чисте посилання на неї)."""
    m = _SEARCH_RE.search(body)
    if not m:
        return None, None
    return m.group(1), f"https://www.kleinanzeigen.de/m-suche-verwenden.html?id={m.group(1)}"


def search_name(subject: str) -> str:
    m = re.search(r"„(.+?)“", subject)
    name = m.group(1) if m else subject
    return re.sub(r"^(PC-Zubehör & Software|Konsolen) - | in Ganz Deutschland$", "", name)


def should_hint(search_id: str, now: datetime, hint_times: dict, min_gap_min: int = HINT_GAP_MIN) -> bool:
    """Не частіше за одну тиху підказку на підписку за min_gap_min хвилин."""
    last = hint_times.get(search_id)
    return not last or (now - datetime.fromisoformat(last)).total_seconds() >= min_gap_min * 60


def hint_text(subject: str, shown: dict | None, verdict: str | None) -> str:
    from html import escape

    lines = [f"🔕 Нові збіги: <b>{escape(search_name(subject))}</b>"]
    if shown:
        lines.append(f"У листі Kleinanzeigen показує лише одне: <i>{escape(shown['title'][:70])}</i> — "
                     f"{shown['price']:.0f} € — не той товар.")
    lines.append("Решту збігів Kleinanzeigen у лист не кладе — серед них може бути вигідне. Глянь пошук.")
    return "\n".join(lines)


def send_telegram_hint(html_text: str, link: str):
    import requests

    url = f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": html_text, "parse_mode": "HTML",
               "disable_web_page_preview": "true", "disable_notification": "true",
               "reply_markup": json.dumps({"inline_keyboard": [[{"text": "🔎 Відкрити пошук", "url": link}]]},
                                          ensure_ascii=False)}
    requests.post(url, data=payload, timeout=15).raise_for_status()


def _process(msg, max_age_hours: float, dry_run: bool, seen_ads: set, hint_times: dict | None = None) -> int:
    """Один лист → вердикти всіх оголошень у ньому; повертає кількість надісланих карток.
    Kleinanzeigen кладе в лист лише ОДНЕ оголошення з пачки («2/5 neue Ergebnisse» у дзвіночку) —
    якщо воно невигідне, шлемо тиху підказку з кнопкою на підписку, щоб приховані збіги не губились."""
    age = _mail_age_hours(msg)
    stale = age is not None and age > max_age_hours
    subject = _decode(msg.get("Subject"))
    body = _body_text(msg)
    listings = extract_listings(subject, body)
    sid, slink = search_link(body)
    shown, shown_verdict, wrong_type = None, None, False
    if dry_run:   # діагностика: чи не губимо оголошення, які є в листі, але не розпізнані парсером
        ad_ids = sorted(set(re.findall(r"/s-anzeige/(?:[^/\"'\s]+/)?(\d{8,})", body)))
        count = re.search(r"(\d+)\s+neue", re.sub(r"<[^>]+>", " ", body))
        print(f" · {subject[27:75]} | розпізнано {len(listings)}, id оголошень у листі {len(ad_ids)}"
              f"{', «' + count.group(0) + '»' if count else ''}")
    if not listings:
        print(f" · [{age or 0:.1f} год] без оголошень: {subject[:70]}")
    sent = 0
    for lst in listings:
        ak = _ad_key(lst)
        if ak in seen_ads:
            continue
        seen_ads.add(ak)
        if lst.get("gewerblich"):
            print(" - (gewerblich, пропущено)", lst["title"][:70])
            continue
        vb = lst.get("vb", False)
        res = evaluate_console(lst["title"], lst["price"], vb=vb) or evaluate(lst["title"], lst["price"], vb=vb)
        reason = f" ({res['reason']})" if res.get("reason") else ""
        print(f" - [{age or 0:.1f} год] {lst['title'][:70]} | {lst['price']:.0f}€ -> {res['verdict']}{reason}")
        shown, shown_verdict = lst, res["verdict"]
        wrong_type = wrong_type or res["verdict"] == "UNKNOWN" or bool(res.get("wrong_type"))
        if res["verdict"] not in SEND_VERDICTS:
            continue
        if stale:
            print(f"   старіший за {max_age_hours:.0f} год — не сповіщаю")
            continue
        risk = check_listing(lst["link"], lst["price"], res.get("quick_sale", 0))
        print(f"   продавець: {risk['level'] + ' ' + '; '.join(risk['reasons']) if risk else 'не перевірено'}")
        if risk and risk["level"] == "gone":
            continue
        res["risk_lines"] = risk_lines(risk)
        silent = bool(risk and risk["level"] == "high")   # схоже на шахрая — картка без звуку
        if dry_run:
            print(f"   [DRY RUN] надіслав би картку{' (тихо, високий ризик)' if silent else ''}")
        else:
            send_telegram_card(format_html(res), lst["link"], seller_template(res), slink, silent,
                               offer_template(res))
        sent += 1
    # Підказка лише коли в листі НЕ той товар (Series S у пошуку Xbox, 2×8 у пошуку 16 ГБ): тоді справжній
    # кандидат ймовірно схований у тій самій пачці. Правильний товар, просто дорожчий, — не привід.
    if hint_times is not None and not sent and not stale and sid and listings and wrong_type:
        now = datetime.now(timezone.utc)
        if should_hint(sid, now, hint_times):
            hint_times[sid] = now.isoformat()
            if dry_run:
                print("   [DRY RUN] тиха підказка: " + search_name(subject))
            else:
                send_telegram_hint(hint_text(subject, shown, shown_verdict), slink)
    return sent


def run(state_path: str, dry_run: bool = False, max_age_hours: float = 6.0, lookback_days: int = 2):
    state = load_state(state_path)
    seen = set() if dry_run else set(state["seen_ids"])   # діагностика бачить усе, навіть уже оброблене
    seen_ads = set() if dry_run else set(state.get("seen_ads", []))
    hint_times = {} if dry_run else dict(state.get("hint_times", {}))
    gmail_user = os.getenv("GMAIL_USER", "")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
    if not gmail_user or not gmail_pass:
        print("[SKIP] GMAIL_USER / GMAIL_APP_PASSWORD не задані — вихід.")
        return
    m = imaplib.IMAP4_SSL("imap.gmail.com")
    m.login(gmail_user, gmail_pass)
    if dry_run:   # діагностика: скільки листів від Kleinanzeigen лежить у кожній папці
        since_d = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
        for f in m.list()[1] or []:
            line = f.decode(errors="ignore")
            if "\\Noselect" in line:
                continue
            name = line.rsplit(' "/" ', 1)[-1].strip()
            name = name if name.startswith('"') else f'"{name}"'
            if m.select(name, readonly=True)[0] == "OK":
                n = len(m.search(None, f'(FROM "{FROM_FILTER}" SINCE {since_d})')[1][0].split())
                print(f"   папка {line.split(')')[0]}) {name}: {n}")
    # «Уся пошта» + кошик: користувач видаляє переглянуті сповіщення, а кошик у «Усю пошту» не входить.
    folders = [_all_mail_folder(m)]
    trash = _special_folder(m, "\\Trash", None)
    if trash:
        folders.append(trash)
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
    alerts_sent = new_mails = 0
    for folder in folders:
        if m.select(folder, readonly=True)[0] != "OK":    # readonly: нічого не позначаємо прочитаним
            print("Не відкрилась папка:", folder)
            continue
        ids = m.search(None, f'(FROM "{FROM_FILTER}" SINCE {since})')[1][0].split()
        print(f"Папка {folder}: листів від {FROM_FILTER} з {since}: {len(ids)}")
        for mid in ids:
            msg = email.message_from_bytes(m.fetch(mid, "(BODY.PEEK[])")[1][0][1])
            msgid = msg.get("Message-ID") or f"{folder}:{mid.decode()}"
            if msgid in seen:
                continue
            seen.add(msgid)
            new_mails += 1
            alerts_sent += _process(msg, max_age_hours, dry_run, seen_ads, hint_times)
    m.logout()
    if not dry_run:
        save_state(state_path, {"seen_ids": list(seen), "seen_ads": list(seen_ads), "hint_times": hint_times})
    print(f"Нових листів оброблено: {new_mails}; сповіщень: {alerts_sent}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="нічого не надсилати й не зберігати стан; показати вердикти всіх листів")
    ap.add_argument("--state", default="ram_mail_state.json")
    ap.add_argument("--max-age-hours", type=float, default=6.0)
    ap.add_argument("--lookback-days", type=int, default=2)
    args = ap.parse_args()
    run(args.state, args.dry_run, args.max_age_hours, args.lookback_days)
