"""Автоматична перевірка нових листів Kleinanzeigen (Suchauftrag) поштою: парсить
назву/ціну/посилання, оцінює через ram_alert.evaluate() і шле в Telegram (той
самий бот/чат основного eBay-бота) картку з вердиктом, кнопкою-посиланням на
оголошення і готовим текстом для копіювання. Повідомлення НЕ надсилається
продавцю автоматично — це робить сам користувач з телефону.

УВАГА: extract_listing() — ЧОРНОВИЙ парсер. Реальний формат листа Kleinanzeigen
ще не бачений (перший лист користувач надішле окремо) — тоді цю функцію треба
переписати під фактичну структуру теми/тіла листа. Решта пайплайну (оцінка,
дедуплікація, відправка з кнопкою) вже робоча і тестується без пошти.

Потрібні секрети в оточенні:
    GMAIL_USER, GMAIL_APP_PASSWORD   — IMAP-доступ (пароль застосунку Google, НЕ звичайний пароль)
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — той самий бот, що й основний бот

Запуск: python research/ram_mail_check.py [--dry-run] [--state <path>]
"""
import argparse
import email
import imaplib
import json
import os
import re
import sys
from email.header import decode_header

sys.path.insert(0, ".")
sys.path.insert(0, "research")
import config
from ram_alert import evaluate, format_message

FROM_FILTER = os.getenv("KA_MAIL_FROM_FILTER", "kleinanzeigen.de")
TEMPLATES = {
    True: 'Hallo, ist der Artikel noch verfügbar? Ich würde sofort per „Sicher bezahlen" mit Versand kaufen. '
          'Lief er bis zum Ausbau fehlerfrei? Könnten Sie mir noch ein aktuelles Foto mit Zettel (Datum) schicken? Danke!',
}


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
            title=tm.group(1).strip(),
            price=price,
            link=link_m.group(1) if link_m else None,
            gewerblich=bool(_GEWERBLICH_RE.search(segment)),
        ))
    return out


def send_telegram_card(text: str, link: str | None):
    import requests

    url = f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text}
    if link:
        payload["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": "Відкрити оголошення", "url": link}]]})
    r = requests.post(url, data=payload, timeout=15)
    r.raise_for_status()


def load_state(path):
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return {"seen_ids": []}


def save_state(path, state):
    state["seen_ids"] = state["seen_ids"][-2000:]
    json.dump(state, open(path, "w", encoding="utf-8"), ensure_ascii=False)


def run(state_path: str, dry_run: bool = False):
    state = load_state(state_path)
    seen = set(state["seen_ids"])
    gmail_user = os.getenv("GMAIL_USER", "")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
    if not gmail_user or not gmail_pass:
        print("[SKIP] GMAIL_USER / GMAIL_APP_PASSWORD не задані — dry-run на цьому не перевіриш, вихід.")
        return
    m = imaplib.IMAP4_SSL("imap.gmail.com")
    m.login(gmail_user, gmail_pass)
    m.select("INBOX")
    typ, data = m.search(None, f'(UNSEEN FROM "{FROM_FILTER}")')
    ids = data[0].split()
    print(f"Нових листів від {FROM_FILTER}: {len(ids)}")
    alerts_sent = 0
    for mid in ids:
        typ, msg_data = m.fetch(mid, "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        msgid = msg.get("Message-ID") or mid.decode()
        if msgid in seen:
            continue
        seen.add(msgid)
        subject = _decode(msg.get("Subject"))
        body = _body_text(msg)
        for lst in extract_listings(subject, body):
            if lst.get("gewerblich"):
                print(" - (гевербліх, пропущено)", lst["title"][:70])
                continue
            res = evaluate(lst["title"], lst["price"])
            print(" -", lst["title"][:70], lst["price"], "->", res["verdict"])
            if res["verdict"].startswith("BUY"):
                tpl = TEMPLATES[True]
                text = format_message(res) + f"\n\nШаблон повідомлення продавцю (скопіюйте):\n{tpl}"
                if dry_run:
                    print("[DRY RUN] would send:\n", text, "\nlink:", lst["link"])
                else:
                    send_telegram_card(text, lst["link"])
                alerts_sent += 1
        m.store(mid, "+FLAGS", "\\Seen")
    m.logout()
    save_state(state_path, {"seen_ids": list(seen)})
    print(f"Сповіщень надіслано: {alerts_sent}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--state", default="ram_mail_state.json")
    args = ap.parse_args()
    run(args.state, args.dry_run)
