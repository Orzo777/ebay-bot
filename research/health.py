"""Стан ботів: погодинні лічильники в state-файлах і службові сповіщення в бот «Облік і продаж» (30.09).

Лічильники (листи, картки, запити eBay, збої API, фото-перевірка) читає щоденний звіт research/daily_report.py,
а потім і тижневий. Службові сповіщення («квота eBay», «Gemini не відповідає») — не частіше, ніж раз на N год,
щоб не засмічувати; картки покупок і далі йдуть в основний бот.
"""
import os
from datetime import datetime, timedelta, timezone

KEEP_HOURS = 24 * 8


def _hour(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def bump(state: dict, key: str, n: int = 1, now: datetime | None = None):
    """state['stats'][година UTC][key] += n; старше 8 днів — прибираємо."""
    if not n:
        return
    now = now or datetime.now(timezone.utc)
    stats = state.setdefault("stats", {})
    h = stats.setdefault(_hour(now), {})
    h[key] = h.get(key, 0) + n
    cut = _hour(now - timedelta(hours=KEEP_HOURS))
    for k in [k for k in stats if k < cut]:
        del stats[k]


def totals(stats: dict, hours: int = 24, now: datetime | None = None) -> dict:
    """Сума лічильників за останні `hours` годин."""
    now = now or datetime.now(timezone.utc)
    cut = _hour(now - timedelta(hours=hours - 1))
    out: dict = {}
    for h, d in (stats or {}).items():
        if h >= cut:
            for k, v in d.items():
                out[k] = out.get(k, 0) + v
    return out


def office_send(text: str) -> bool:
    """Службове повідомлення в бот «Облік і продаж» (без нього — в основний)."""
    import requests

    import config
    token = os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN
    if not token or not config.TELEGRAM_CHAT_ID:
        print("[службове, без Telegram] " + text)
        return False
    try:
        r = requests.post(f"{config.TELEGRAM_API_BASE}/bot{token}/sendMessage", timeout=15,
                          data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": "true"})
        return r.status_code == 200
    except Exception as e:
        print(f"[службове] не надіслав: {e.__class__.__name__}")
        return False


def alert(state: dict, key: str, text: str, every_hours: float = 6, now: datetime | None = None,
          send=office_send) -> bool:
    """Сповіщення `key` не частіше, ніж раз на `every_hours` год (час останнього — у state['alerts'])."""
    now = now or datetime.now(timezone.utc)
    last = (state.get("alerts") or {}).get(key)
    if last and now - datetime.fromisoformat(last) < timedelta(hours=every_hours):
        return False
    state.setdefault("alerts", {})[key] = now.isoformat()
    send(text)
    return True


def track_photo(state: dict, before: dict, after: dict, now: datetime | None = None, send=office_send):
    """Облік фото-перевірки за цей прохід (photo_check.STATS до/після). ≥5 збоїв Gemini поспіль — сповіщення."""
    ok, fail = after["ok"] - before["ok"], after["fail"] - before["fail"]
    bump(state, "photo_ok", ok, now)
    bump(state, "photo_fail", fail, now)
    if ok:
        state["photo_fail_run"] = 0
    state["photo_fail_run"] = state.get("photo_fail_run", 0) + (fail if not ok else 0)
    if state["photo_fail_run"] >= 5:
        alert(state, "gemini", "📷 Фото-перевірка (Gemini) не відповідає вже " + str(state["photo_fail_run"]) +
              " разів поспіль. Картки йдуть без рядка «📷». Зазвичай це перевантаження або денний ліміт — "
              "мине саме; якщо довше доби — перевір ключ GEMINI_API_KEY.", 12, now, send)
