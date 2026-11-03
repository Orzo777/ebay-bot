"""Запасна пошта (03.11, після блокування Gmail 02–03.11): листи Kleinanzeigen — через IMAP іншої скриньки.

Спить, доки немає секретів MAIL_USER / MAIL_PASSWORD (MAIL_HOST — за замовчуванням IMAP Hostinger). Адреса — лише в
секретах (репозиторій публічний). Коли увімкнено:
  • ram_mail_check.py читає і Gmail, і цю скриньку (той самий розбір, дублі — за Message-ID);
  • eBay-сторож щокола (~3 хв) дивиться, чи прийшли нові листи KA, і запускає ram_mail_alert.yml — те, що для Gmail
    робить check() в Apps Script.
У скриньці можуть бути й чужі листи (інший проєкт) — читаємо лише FROM kleinanzeigen.de, нічого не позначаємо прочитаним.
"""
import imaplib
import os
import re
from datetime import datetime, timedelta, timezone

HOST_DEFAULT = "imap.hostinger.com"
FROM_FILTER = "kleinanzeigen.de"


def configured() -> bool:
    return bool(os.getenv("MAIL_USER") and os.getenv("MAIL_PASSWORD"))


def connect(imap=imaplib.IMAP4_SSL):
    m = imap(os.getenv("MAIL_HOST") or HOST_DEFAULT, 993, timeout=30)
    m.login(os.getenv("MAIL_USER", ""), os.getenv("MAIL_PASSWORD", ""))
    return m


def _name(line: str) -> str:
    name = line.rsplit(' "/" ', 1)[-1].rsplit(' "." ', 1)[-1].strip()
    return name if name.startswith('"') else f'"{name}"'


def folders(m) -> list[str]:
    """Вхідні + кошик і спам (службові атрибути \\Trash / \\Junk або назва): листи, які видалили / які сервер вважав спамом."""
    out = ['"INBOX"']
    for f in m.list()[1] or []:
        line = f.decode(errors="ignore") if isinstance(f, bytes) else str(f)
        if "\\Noselect" in line:
            continue
        name = _name(line)
        if ("\\Trash" in line or "\\Junk" in line or re.search(r"trash|junk|spam|papierkorb", name, re.I)) and name not in out:
            out.append(name)
    return out


def criteria(frm: str, since: str) -> str:
    """IMAP SEARCH: від кого + з якої дати; MAIL_ALIAS (адреса бота, якщо скринька спільна з іншим проєктом) — лише листи на неї."""
    alias = os.getenv("MAIL_ALIAS", "").strip()
    return f'(FROM "{frm}"' + (f' TO "{alias}"' if alias else "") + f" SINCE {since})"


def new_ka_uids(m, seen: list, days: int = 1) -> list[str]:
    """UID листів KA у вхідних за `days` днів, яких ще не бачили."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    if m.select('"INBOX"', readonly=True)[0] != "OK":
        return []
    typ, data = m.uid("SEARCH", None, criteria(FROM_FILTER, since))
    uids = (data[0] or b"").decode().split() if typ == "OK" and data else []
    return [u for u in uids if u not in set(seen)]


# ------------------------------------------------------------------ решта листів → Apps Script (03.11)
# Ті самі відправники, що в запитах Gmail обробників Apps Script (облік, перевізники, листи eBay, чати KA). Листи підписок KA
# (картки робить ram_mail_check.py) — лише заголовок, без тексту: Apps Script бачить, що листи KA приходять (сторож).
RELAY_FROM = ("ebay", "kleinanzeigen.de", "paypal", "dhl.de", "dhl.com", "deutschepost.de", "myhermes.de", "hermesworld.com",
              "hermes-europe.de", "dpd.de", "dpd.com", "gls-pakete.de", "gls-group", "ups.com")
_ALERT = re.compile(r"neue treffer|suchauftrag|neue anzeigen", re.I)


def _to_me(msg) -> bool:
    alias = os.getenv("MAIL_ALIAS", "").strip().lower()
    if not alias:
        return True
    return alias in " ".join(str(msg.get(h, "")) for h in ("To", "Cc", "Delivered-To", "X-Original-To", "Envelope-To")).lower()


def _parts(msg) -> tuple[str, str]:
    text = html = ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").lower().startswith("attachment"):
            continue
        try:
            body = part.get_payload(decode=True) or b""
            body = body.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, ValueError):
            continue
        if ctype == "text/plain" and not text:
            text = body
        elif ctype == "text/html" and not html:
            html = body
    return text, html


def mail_dict(raw: bytes) -> dict | None:
    """Лист → те, що бачать обробники Apps Script (getFrom / getSubject / getDate / getPlainBody / getBody)."""
    import email
    from email.header import decode_header, make_header
    from email.utils import parsedate_to_datetime
    msg = email.message_from_bytes(raw)
    if not _to_me(msg):
        return None
    dec = lambda v: str(make_header(decode_header(v))) if v else ""
    subject = dec(msg.get("Subject"))
    try:
        date = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        date = datetime.now(timezone.utc).isoformat()
    text, html = ("", "") if _ALERT.search(subject) else _parts(msg)
    return {"id": (msg.get("Message-ID") or "").strip() or f"{subject}|{date}", "from": dec(msg.get("From")),
            "to": dec(msg.get("To")), "subject": subject, "date": date, "text": text[:30000], "html": html[:60000]}


def relay(state: dict, key: str, connect_fn=connect, send_fn=None, batch: int = 5, days: int = 3) -> int:
    """Щокола eBay-сторожа: нові листи потрібних відправників, адресовані боту → зашифровано в Apps Script (до `batch` за раз).
    → скільки переслано. Apps Script не прийняв — наступне коло спробує ще."""
    if not configured() or not key:
        return 0
    seen = set(state.get("relay_uids", []))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    m = connect_fn()
    mails, done = [], []
    try:
        if m.select('"INBOX"', readonly=True)[0] != "OK":
            return 0
        uids = set()
        for frm in RELAY_FROM:
            typ, data = m.uid("SEARCH", None, criteria(frm, since))
            if typ == "OK" and data and data[0]:
                uids |= set(data[0].decode().split())
        for u in sorted((u for u in uids if u not in seen), key=int)[:batch]:
            typ, data = m.uid("FETCH", u, "(BODY.PEEK[])")
            raw = next((x[1] for x in data or [] if isinstance(x, tuple)), None) if typ == "OK" else None
            done.append(u)
            d = mail_dict(raw) if raw else None
            if d:
                mails.append(d)
    finally:
        try:
            m.logout()
        except Exception:
            pass
    if mails:
        res = (send_fn or _send)({"kind": "mail", "mails": mails}, key)
        if not res or res.get("relay") is None:
            print(f"   [запасна пошта] Apps Script не прийняв {len(mails)} лист(ів) — спробую наступного кола")
            return 0
        print(f"   [запасна пошта] переслав в Apps Script: {len(mails)} (нових там: {res['relay']})")
    if done:
        state["relay_uids"] = (state.get("relay_uids", []) + done)[-500:]
    return len(mails)


def _send(data: dict, key: str) -> dict | None:
    """Зашифровано → вебзастосунок Apps Script (адреса — з вебхука бота «Облік і продаж»); відповідь — JSON після 302."""
    import json
    import uuid
    import requests
    import as_health
    from sell import seal
    url = as_health.webapp_url(key)
    if not url:
        return None
    nonce = uuid.uuid4().hex
    blob, mac = seal(data, key, nonce)
    r = requests.post(url, data=json.dumps({"sealed": {"blob": blob, "mac": mac, "nonce": nonce}}),
                      headers={"Content-Type": "application/json"}, timeout=120, allow_redirects=False)
    if r.status_code in (301, 302, 303) and r.headers.get("Location"):
        r = requests.get(r.headers["Location"], timeout=60)
    try:
        return r.json()
    except ValueError:
        return None


def poll(state: dict, now: datetime, dispatch, connect_fn=connect, send=None) -> int:
    """Щокола eBay-сторожа: нові листи KA у запасній скриньці → запустити ram_mail_alert.yml. → скільки нових."""
    if not configured():
        return 0
    try:
        m = connect_fn()
        try:
            new = new_ka_uids(m, state.get("backup_uids", []))
        finally:
            try:
                m.logout()
            except Exception:
                pass
    except (imaplib.IMAP4.error, OSError) as e:
        state["backup_fail"] = state.get("backup_fail", 0) + 1
        print(f"   [запасна пошта] {e.__class__.__name__}")
        if state["backup_fail"] >= 5 and send:   # ~15 хв поспіль — пароль змінився / скринька вимкнена
            import health
            health.alert(state, "backup_mail", "⚠️ Запасна пошта не відкривається (IMAP, " + e.__class__.__name__ + ") — листи KA "
                         "з неї не приходять. Перевір пароль скриньки (секрет MAIL_PASSWORD у GitHub) і чи активна скринька.", 6, now, send)
        return 0
    state["backup_fail"] = 0
    if not new:
        return 0
    code = dispatch("ram_mail_alert.yml")
    print(f"   [запасна пошта] нових листів KA: {len(new)} → обробка: {code}")
    if code == 204:
        state["backup_uids"] = (state.get("backup_uids", []) + new)[-300:]
    return len(new)
