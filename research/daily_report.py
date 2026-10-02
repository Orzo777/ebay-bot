"""Щоденний звіт «✅ все працює» (30.09) → бот «Облік і продаж».

Запускає сторож в Apps Script (tools/watchdog.gs) щоранку о 9:00 за Берліном через daily_report.yml.
Джерела: лічильники зі state-файлів eBay-сторожа й KA-пошти (research/health.py, кеш GitHub Actions) і
список запусків workflow за 24 год (GitHub API). Якщо звіт не прийшов зранку — щось зламалось у самому
Apps Script / GitHub: це й є сигнал.

    python research/daily_report.py --ebay ebay_watch_state.json --ka ram_mail_state.json --runs runs.json [--dry-run]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import health

try:
    from zoneinfo import ZoneInfo
    BERLIN = ZoneInfo("Europe/Berlin")
except Exception:   # Windows без tzdata
    BERLIN = timezone(timedelta(hours=2))

BAD = ("failure", "timed_out", "startup_failure")
FLAKY = ("ram-mail-alert",)
NAMES = {"ebay-watch": "eBay-сторож", "ram-mail-alert": "KA-пошта", "ka-share": "«поділитися»", "tests": "тести",
         "daily-report": "звіт", "sell": "«продати»", "weekly-report": "тижневий звіт", "price-refresh": "сторож цін", "ka-reply": "відповідь продавця"}


def _load(path: str | None) -> dict:
    try:
        return json.load(open(path, encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        return {}


def run_stats(runs: list[dict], now: datetime, hours: int = 24) -> dict:
    """→ {'by': {workflow: [усього, збоїв, посилання на останній збій]}, 'ebay_minutes': хвилин роботи eBay-сторожа}."""
    cut = now - timedelta(hours=hours)
    by, ebay_min, spans = {}, 0.0, []
    for r in runs:
        t0 = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        t1 = now if r.get("status") != "completed" else             datetime.fromisoformat((r.get("updated_at") or r["created_at"]).replace("Z", "+00:00"))
        if t1 < cut or r.get("conclusion") == "cancelled":   # запуск, що почався до межі доби, рахуємо частково
            continue
        name = r.get("name") or r.get("path") or "?"
        d = by.setdefault(name, [0, 0, None])
        d[0] += 1
        if r.get("conclusion") in BAD:
            d[1] += 1
            d[2] = d[2] or r.get("html_url")
        if name == "ebay-watch":   # від фактичного старту (у черзі чекає, поки йде попередній)
            ts = datetime.fromisoformat((r.get("run_started_at") or r["created_at"]).replace("Z", "+00:00"))
            spans.append((max(ts, cut), min(t1, now)))
    end = cut
    for a, b in sorted(spans):   # об'єднання відрізків: запасний cron-запуск не рахуємо двічі
        if b > end:
            ebay_min += (b - max(a, end)).total_seconds() / 60
            end = b
    return {"by": by, "ebay_minutes": ebay_min}


def build(ebay: dict, ka: dict, runs: list[dict], now: datetime) -> str:
    e = health.totals(ebay.get("stats"), 24, now)
    k = health.totals(ka.get("stats"), 24, now)
    rs = run_stats(runs, now)
    problems = []
    up = min(100, round(rs["ebay_minutes"] / (24 * 60) * 100))
    if up < 90:
        problems.append(f"eBay-сторож працював лише {up}% часу")
    if e.get("ebay_rounds") and e.get("ebay_api_errors", 0) > e["ebay_rounds"]:   # у середньому >1 збою на коло
        problems.append(f"збоїв eBay API: {e['ebay_api_errors']}")
    first = min(list((ebay.get("stats") or {})) + list((ka.get("stats") or {})) or ["9999"])
    young = first > (now - timedelta(hours=20)).strftime("%Y-%m-%dT%H")   # лічильники ведуться менше доби
    if not k.get("ka_mails") and not young:
        problems.append("жодного листа від Kleinanzeigen за добу")
    notes = []
    for name, (total, bad, url) in rs["by"].items():
        if not bad:
            continue
        # KA-пошта: разовий збій Gmail IMAP («System Error») наступний запуск підбирає сам — не тривога (як у сторожі)
        if name in FLAKY and bad == 1 and total > 3:
            notes.append(f"{NAMES.get(name, name)}: 1 разовий збій з {total} запусків, наступні пройшли")
        else:
            problems.append(f"{NAMES.get(name, name)}: збоїв {bad} ({url})")
    photo_ok = e.get("photo_ok", 0) + k.get("photo_ok", 0)
    photo_fail = e.get("photo_fail", 0) + k.get("photo_fail", 0)
    if photo_fail > photo_ok and photo_fail >= 3:
        problems.append(f"фото-перевірка майже не працює ({photo_fail} збоїв)")
    shares = rs["by"].get("ka-share", [0])[0]
    day = now.astimezone(BERLIN).strftime("%d.%m")
    lines = [("⚠️ Є що перевірити" if problems else "✅ Все працює") + f" · звіт за добу до {day}", "",
             f"🟢 Kleinanzeigen: листів {k.get('ka_mails', 0)} → карток {k.get('ka_cards', 0)}",
             f"🔵 eBay: перевірок {e.get('ebay_rounds', 0)} (працював {up}% часу) → карток {e.get('ebay_cards', 0)}, "
             f"аукціонів {e.get('ebay_auctions', 0)}",
             f"   запитів API {e.get('ebay_calls', 0)} з 5 000",
             f"📷 Фото-перевірка: ок {photo_ok}, без відповіді {photo_fail}"]
    if young:
        lines.append("ℹ️ Лічильники листів і карток ведуться з сьогодні — повна доба буде в завтрашньому звіті.")
    if shares:
        lines.append(f"🔗 Оцінено посилань («поділитися»): {shares}")
    if problems:
        lines += ["", *("• " + p for p in problems)]
    if notes:
        lines += [*("ℹ️ " + n for n in notes)]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ebay")
    ap.add_argument("--ka")
    ap.add_argument("--runs")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    runs = _load(a.runs)
    runs = runs.get("workflow_runs", []) if isinstance(runs, dict) else runs
    text = build(_load(a.ebay), _load(a.ka), runs or [], datetime.now(timezone.utc))
    # коли востаннє йшли службові сповіщення (лише назва й час — для розбору хибних тривог)
    print("службові сповіщення:", json.dumps({"ebay": _load(a.ebay).get("alerts"), "ka": _load(a.ka).get("alerts")}))
    print(text)
    if not a.dry_run:
        health.office_send(text)


if __name__ == "__main__":
    main()
