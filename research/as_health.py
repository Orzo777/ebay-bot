"""Стан Apps Script ззовні (03.11): після розблокування акаунта Google таймери Apps Script (пошта KA, облік, сторож,
ранковий звіт) перестали спрацьовувати, а вебхук ботів працював — сторож всередині Apps Script сам про себе сказати не міг.

Звідси (GitHub, на початку кожного кола eBay-сторожа, ~щогодини) — зашифрований запит {"kind": "health"} на вебзастосунок.
Apps Script відповідає, скільки хвилин тому востаннє відпрацювали check / processLedger / watchdog і які тригери є; якщо
давно — перевстановлює тригери сам (не частіше разу на 2 год) і пише в бот «Облік і продаж». У лог — лише хвилини й назви
функцій (репозиторій публічний).
"""
import json
import os
import sys
import time
import uuid

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # config.py — у корені
import config  # noqa: E402
from sell import seal  # noqa: E402


def webapp_url(key: str, get=requests.get) -> str:
    info = get(f"{config.TELEGRAM_API_BASE}/bot{key}/getWebhookInfo", timeout=20).json()
    url = (info.get("result") or {}).get("url") or ""
    return url if url.startswith("https://script.google.com/") else ""


def ask(url: str, key: str, heal: bool = True, post=requests.post, get=requests.get) -> dict | None:
    """→ відповідь Apps Script (dict) або None (зайнятий / стара версія без «health»). Apps Script відповідає
    перенаправленням 302 на googleusercontent — відповідь там."""
    nonce = uuid.uuid4().hex
    blob, mac = seal({"kind": "health", "heal": 1 if heal else 0}, key, nonce)
    r = post(url, data=json.dumps({"sealed": {"blob": blob, "mac": mac, "nonce": nonce}}),
             headers={"Content-Type": "application/json"}, timeout=60, allow_redirects=False)
    if r.status_code in (301, 302, 303) and r.headers.get("Location"):
        r = get(r.headers["Location"], timeout=60)
    try:
        js = r.json()
    except ValueError:
        return None
    return js if isinstance(js, dict) and "triggers" in js else None


def line(res: dict) -> str:
    age = lambda k: "—" if res.get(k, -1) < 0 else f"{res[k]} хв"
    return (f"тригери: {', '.join(sorted(res.get('triggers') or [])) or 'немає'}; пошта KA (check) — {age('check')}, "
            f"облік — {age('ledger')}, сторож — {age('watchdog')}" + (f"; перевстановив: {', '.join(res['healed'])}"
                                                                        if res.get("healed") else ""))


def hook_info(key: str, get=requests.get, now: float | None = None) -> str:
    """04.11 («помилка вебзастосунку: Read timeout expired»): що Telegram знає про вебхук — черга й остання помилка (без адреси)."""
    try:
        r = (get(f"{config.TELEGRAM_API_BASE}/bot{key}/getWebhookInfo", timeout=20).json() or {}).get("result") or {}
    except Exception as e:
        return f"вебхук: {e.__class__.__name__}"
    now = now or time.time()
    err = r.get("last_error_date")
    line = (f"вебхук бота «Облік і продаж»: у черзі {r.get('pending_update_count', 0)}" +
            (f", остання помилка {round((now - err) / 60)} хв тому: {str(r.get('last_error_message') or '')[:80]}" if err else ", помилок немає"))
    print(line)
    return line


def main() -> int:
    key = os.getenv("OFFICE_BOT_TOKEN", "")
    if not key:
        print("немає OFFICE_BOT_TOKEN — пропускаю")
        return 0
    url = webapp_url(key)
    if not url:
        print("немає адреси Apps Script у вебхуку бота «Облік і продаж»")
        return 0
    hook_info(key)
    for i in range(2):   # вебзастосунок зайнятий (інший запит тримає замок) — ще раз за 20 с
        res = ask(url, key, heal="--no-heal" not in sys.argv)
        if res:
            print(("⚠️ " if res.get("stale") else "✅ ") + line(res))
            return 0
        time.sleep(20)
    print("Apps Script не відповів станом (зайнятий або ще стара версія)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
