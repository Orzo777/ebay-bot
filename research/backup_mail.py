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


def new_ka_uids(m, seen: list, days: int = 1) -> list[str]:
    """UID листів KA у вхідних за `days` днів, яких ще не бачили."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    if m.select('"INBOX"', readonly=True)[0] != "OK":
        return []
    typ, data = m.uid("SEARCH", None, f'(FROM "{FROM_FILTER}" SINCE {since})')
    uids = (data[0] or b"").decode().split() if typ == "OK" and data else []
    return [u for u in uids if u not in set(seen)]


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
