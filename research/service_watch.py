"""Сторож сервісів (11.10, прохання користувача): попередити ЗАЗДАЛЕГІДЬ, що щось із того, до чого звертаємось, міняється.

Справжні збої ловить health.py / watchdog.gs; цей модуль — про анонси й застарілі версії, поки нічого не впало.
Раз на 7 днів (з price_refresh, ~25 запитів), стан — у ram_prices.json → data["services"]:
  • Gemini: чи живі псевдоніми моделей, на які посилається бот (photo_check.MODELS), і яка найновіша Flash;
  • GitHub: попередження «deprecated / will be removed / Node.js …» у журналах останніх запусків кожного робочого процесу;
  • eBay: блок «warnings» у відповіді Browse API (сторінку застарілих API eBay ботам не віддає — 403);
  • Telegram: остання версія Bot API (лише для звіту — Telegram зберігає сумісність).
Поштовх у бот «Облік і продаж» — лише про нове й таке, що може зламати; решта — рядок «🔧 Сервіси» в тижневому звіті.
"""
import os
import re
from datetime import date

EVERY_DAYS = 7
ALIASES = ("gemini-flash-lite-latest", "gemini-flash-latest")
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"
_DEPR = re.compile(r"deprecat|will be removed|no longer supported|end of life|node\.?js \d+ actions|set-output|save-state", re.I)


def gemini(get, key: str) -> dict:
    """→ {"aliases": {псевдонім: True/False}, "newest": "gemini-3.6-flash"…, "error": ""}."""
    out = {"aliases": {}, "newest": "", "error": ""}
    if not key:
        out["error"] = "немає GEMINI_API_KEY"
        return out
    try:
        r = get(GEMINI, headers={"x-goog-api-key": key}, params={"pageSize": 200}, timeout=20)
        if r.status_code != 200:
            out["error"] = f"список моделей: HTTP {r.status_code}"
            return out
        names = [m.get("name", "").split("/")[-1] for m in r.json().get("models", [])]
        flash = [n for n in names if re.fullmatch(r"gemini-\d+(?:\.\d+)?-flash", n)]
        out["newest"] = max(flash, key=lambda n: float(re.search(r"\d+(?:\.\d+)?", n).group()), default="")
        for a in ALIASES:
            out["aliases"][a] = a in names or get(f"{GEMINI}/{a}", headers={"x-goog-api-key": key}, timeout=20).status_code == 200
    except Exception as e:
        out["error"] = e.__class__.__name__
    return out


def github(get, repo: str, token: str) -> list[str]:
    """Унікальні попередження про застаріле з останнього запуску кожного робочого процесу."""
    if not repo:
        return []
    hdr = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
    msgs, seen_wf = [], set()
    try:
        runs = get(f"https://api.github.com/repos/{repo}/actions/runs", headers=hdr, params={"per_page": 60}, timeout=20)
        for run in runs.json().get("workflow_runs", []):
            if run.get("name") in seen_wf or run.get("status") != "completed":
                continue
            seen_wf.add(run["name"])
            for job in get(run["jobs_url"], headers=hdr, timeout=20).json().get("jobs", [])[:3]:
                url = (job.get("check_run_url") or "") + "/annotations"
                for a in get(url, headers=hdr, timeout=20).json() if job.get("check_run_url") else []:
                    m = re.sub(r"\s+", " ", str(a.get("message") or ""))
                    if _DEPR.search(m):
                        msgs.append(f"{run['name']}: {m[:220]}")
    except Exception as e:
        msgs.append(f"(GitHub не відповів: {e.__class__.__name__})")
    return sorted(set(msgs))


def ebay(get_json) -> list[str]:
    """get_json() → відповідь Browse API (dict). Повертає тексти попереджень eBay."""
    try:
        d = get_json() or {}
    except Exception as e:
        return [f"(eBay Browse не відповів: {e.__class__.__name__})"]
    return sorted({re.sub(r"\s+", " ", str(w.get("longMessage") or w.get("message") or ""))[:220]
                   for w in d.get("warnings") or [] if w.get("message") or w.get("longMessage")})


def telegram(get) -> str:
    try:
        t = get("https://core.telegram.org/bots/api-changelog", timeout=20).text
        m = re.search(r"Bot API (\d+\.\d+)", t)
        return m.group(1) if m else ""
    except Exception:
        return ""


def update(data: dict, today: str, get=None, ebay_json=None) -> list[str]:
    """Раз на тиждень — перевірка; → повідомлення для поштовху (порожньо — нічого нового/небезпечного)."""
    sv = data.setdefault("services", {})
    if sv.get("last") and (date.fromisoformat(today) - date.fromisoformat(sv["last"])).days < EVERY_DAYS:
        return []
    if get is None:
        import requests
        get = requests.get
    g = gemini(get, os.getenv("GEMINI_API_KEY", ""))
    gh = github(get, os.getenv("GITHUB_REPOSITORY", ""), os.getenv("GH_API_TOKEN", ""))
    eb = ebay(ebay_json) if ebay_json else []
    tg = telegram(get)
    alerts = []
    dead = [a for a, ok in g["aliases"].items() if not ok]
    if g["error"] or dead:
        alerts.append("Gemini: " + (g["error"] or "зникли моделі " + ", ".join(dead)) +
                      " — фото й тексти продавцям можуть перестати працювати (бот спробує інші моделі сам).")
    first = not sv.get("last")   # перший запуск — лише точка відліку (старі запуски ще з попередніми версіями дій)
    new_gh = [] if first else [m for m in gh if m not in sv.get("gh", [])]
    if new_gh:
        alerts.append("GitHub попереджає про застаріле (запуски поки працюють):\n" + "\n".join("• " + m for m in new_gh[:5]))
    new_eb = [] if first else [m for m in eb if m not in sv.get("ebay", [])]
    if new_eb:
        alerts.append("eBay API попереджає:\n" + "\n".join("• " + m for m in new_eb[:5]))
    info = []
    if g["newest"] and sv.get("gemini_newest") and g["newest"] != sv["gemini_newest"]:
        info.append(f"нова модель Gemini: {g['newest']} (бот перейде сам — звертається до «найновішої»)")
    if tg and sv.get("telegram") and tg != sv["telegram"]:
        info.append(f"Telegram Bot API {tg}")
    sv.update(last=today, gemini_newest=g["newest"] or sv.get("gemini_newest", ""), gemini_ok=not (g["error"] or dead),
              gh=gh, ebay=eb, telegram=tg or sv.get("telegram", ""), info=info, alerts=len(alerts))
    return ["🔧 Сторож сервісів — варто глянути, поки нічого не впало:\n" + "\n\n".join(alerts) +
            "\nНапиши Claude — оновимо."] if alerts else []


def lines(data: dict) -> list[str]:
    from html import escape
    sv = data.get("services") or {}
    if not sv.get("last"):
        return []
    bad = sv.get("alerts", 0)
    head = (f"🔧 <b>Сервіси</b> (перевірка {sv['last']}): " +
            ("нових попереджень немає" if not bad else f"попереджень {bad} — деталі були окремим повідомленням") +
            (f" · Gemini {sv['gemini_newest']}" if sv.get("gemini_newest") else "") +
            (f" · Telegram Bot API {sv['telegram']}" if sv.get("telegram") else ""))
    return [head] + [f"• {escape(x)}" for x in sv.get("info", [])]
