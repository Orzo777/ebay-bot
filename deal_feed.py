"""Стрічка «mydealz → перевір → звіт» (v0).

Джерело — публічний RSS mydealz (`/rss/new`, останні 30 пропозицій, ≈300/добу). Для КОЖНОЇ
фізичної пропозиції з ціною:
  1) добуваємо запит із назви, 1 виклик eBay → ринок нового товару (check.build_market);
  2) економіка з реальною комісією категорії (econ_model) при ціні закупівлі = ціна акції
     (+ доставка до нас: припущення, див. INBOUND_SHIP);
  3) лише для кандидатів (ринок читабельний + економіка сходиться) — вимір швидкості
     продажів (≈10 викликів) і вердикт BUY/SKIP/UNKNOWN.
Результат кожної пропозиції пишеться в feed_log.jsonl; `--report` групує по категоріях mydealz і
показує, ЯКІ категорії реально дають кандидатів — це і є відповідь «на чому заробляти».

ЧЕСНІ ОБМЕЖЕННЯ
- Акцію перевіряє ринок eBay, а не сама акція: cashback/купон/«для нових клієнтів»/локально/B-Ware
  інструмент не бачить — кандидата підтверджує людина. B-Ware/refurbished/Retoure відсіюємо за словами.
- Запит із назви — евристика; неоднозначні → UNKNOWN (безпечний напрям).
- Ціна закупівлі: доставка до нас невідома; Amazon = 0 (Prime), решта = INBOUND_SHIP.
- Квота eBay спільна з ботом: обмеження MAX_STAGE1 / MAX_STAGE2 на запуск.

Запуск:  python deal_feed.py --once [--max 40] [--dry]     |     python deal_feed.py --report
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

import check
import econ_model as E

RSS_URLS = ("https://www.mydealz.de/rss/new",)
HEADERS = {"User-Agent": "Mozilla/5.0 (personal price research)"}
STATE_FILE = "feed_state.json"
LOG_FILE = "feed_log.jsonl"

MAX_STAGE1 = 40            # перевірок ринку (1 виклик) за запуск
MAX_STAGE2 = 6             # вимірів швидкості (~10 викликів) за запуск
INBOUND_SHIP = 4.99        # доставка до нас, якщо не Amazon (ПРИПУЩЕННЯ)
PRICE_MIN, PRICE_MAX = 12.0, 450.0   # межі капіталу/сенсу
MIN_PRICE_FRAC = 0.6       # eBay-лоти дешевші за 60% ціни акції — аксесуари/не той товар

SKIP_CATEGORIES = {"Urlaub & Reisen"}
# Нефізичне / не порівнюване з ринком нового товару
DENY_TITLE = re.compile(
    r"\b(eshop|gutschein|code|key|download|digital|abo|spar-abo|guthaben|geschenkkarte|gift ?card|"
    r"esim|tarif|vertrag|hotel|flug|reise|kostenlos|gratis|freebie|cashback|payback|"
    r"b-ware|refurbished|generalüberholt|generalueberholt|retoure|gebraucht|used|"
    r"ausstellungsst|vorführ|vorfuehr|defekt|pfand|lokal|filial|abholung)\b", re.I)
DENY_MERCHANT = re.compile(r"(eshop|playstation store|steam|google play|app store|microsoft store|"
                           r"nintendo)", re.I)
FLAG_TITLE = re.compile(r"\b(neukunden|app|code|coupon|mitglieder|prime|cdiscount|amazon\.(fr|it|es|nl))\b", re.I)


# --------------------------------------------------------------------------- #
# Розбір RSS
# --------------------------------------------------------------------------- #
def parse_price(s):
    """'2.559,98€' → 2559.98; '67,99€' → 67.99; '250€' → 250.0; порожньо → None."""
    if not s:
        return None
    m = re.search(r"(\d[\d.]*)(?:,(\d{1,2}))?", s.replace(" ", ""))
    if not m:
        return None
    whole = m.group(1).replace(".", "")
    try:
        return float(f"{whole}.{m.group(2) or '0'}")
    except ValueError:
        return None


def parse_rss(xml: str) -> list:
    out = []
    for raw in re.findall(r"<item>(.*?)</item>", xml, flags=re.S):
        def g(rx):
            m = re.search(rx, raw, flags=re.S)
            return m.group(1) if m else None
        out.append({
            "cat": html.unescape(g(r"<category><!\[CDATA\[(.*?)\]\]></category>") or "") or None,
            "title": html.unescape(g(r"<title><!\[CDATA\[(.*?)\]\]></title>") or "").strip(),
            "merchant": html.unescape(g(r'<pepper:merchant name="(.*?)"') or "") or None,
            "price": parse_price(g(r'<pepper:merchant[^>]*price="(.*?)"')),
            "link": g(r"<link>(.*?)</link>"),
            "pub": g(r"<pubDate>(.*?)</pubDate>"),
        })
    return out


def fetch_deals(urls=RSS_URLS) -> list:
    deals, seen = [], set()
    for u in urls:
        r = requests.get(u, headers=HEADERS, timeout=25)
        r.raise_for_status()
        for d in parse_rss(r.text):
            if d["link"] and d["link"] not in seen:
                seen.add(d["link"])
                deals.append(d)
    return deals


# --------------------------------------------------------------------------- #
# Фільтр і запит
# --------------------------------------------------------------------------- #
def prefilter(d: dict):
    """None = пропозиція придатна; інакше — причина відсіву (для звіту)."""
    if not d.get("price"):
        return "без ціни"
    if d["cat"] in SKIP_CATEGORIES:
        return "категорія: подорожі"
    if not (PRICE_MIN <= d["price"] <= PRICE_MAX):
        return "ціна поза межами"
    if DENY_TITLE.search(d["title"]):
        return "не нове/не фізичне/локальне"
    if d.get("merchant") and DENY_MERCHANT.search(d["merchant"]):
        return "цифровий магазин"
    if re.search(r"\b(ab|bis zu|verschiedene|sorten)\b", d["title"], re.I):
        return "ціна змінна"
    return None


_CUT = re.compile(r"\s(für|statt|inkl\.?|zzgl\.?|mit|bei|nur|@)\s|[|€]|\s-\s|\s\+\s", re.I)


def make_query(title: str) -> str:
    """Назва акції → пошуковий запит (евристика; погано → UNKNOWN, це безпечно)."""
    t = re.sub(r"\[[^\]]*\]", " ", title)          # [Prime], [CB] …
    t = re.sub(r"\([^)]*\)", " ", t)               # (UVP 23€), (GPS Version) …
    t = _CUT.split(t)[0]
    t = re.sub(r"[\"'“”„,;]", " ", t)
    words = [w for w in re.split(r"\s+", t.strip()) if w]
    return " ".join(words[:7])


_TOK = re.compile(r"[A-Za-zÄÖÜäöüß0-9][A-Za-zÄÖÜäöüß0-9.\-/]*")
_GENERIC_FIRST = {"neu", "new", "original", "set", "der", "die", "das", "the", "ein", "eine"}


def build_product(query: str):
    """Запит → Product. Обов'язкові слова: бренд (перше значуще слово) + токени з цифрами
    (номер моделі/артикул; для однозначних номерів типу «6», «2» — ще й слово перед ними: «iO 6»,
    «Toniebox 2»). Загальні іменники («Smartwatch», «Sport», «Rasierer») НЕ обов'язкові: їх немає в
    частині назв лотів, і ринок «зникав» (Garmin Forerunner 255: 1 лот замість десятків)."""
    toks = _TOK.findall(query)
    nrm = check.identity.norm
    brand = None
    for t in toks:
        first = re.split(r"[^A-Za-z0-9ÄÖÜäöüß]+", t)[0]          # «Oral-B» → «Oral»
        if len(first) >= 3 and not any(c.isdigit() for c in first) and first.lower() not in _GENERIC_FIRST:
            brand = first
            break
    groups = []
    for i, t in enumerate(toks):
        if not any(c.isdigit() for c in t):
            continue
        g = nrm(t)
        if g and {g} not in groups:
            groups.append({g})
        if len(re.sub(r"\D", "", t)) == 1 and len(t) == 1 and i > 0:   # одноцифровий номер моделі
            prev = nrm(re.split(r"[^A-Za-z0-9ÄÖÜäöüß]+", toks[i - 1])[0])
            if prev and len(prev) >= 2 and {prev} not in groups and prev != nrm(brand or ""):
                groups.append({prev})
    if brand and groups:
        must = [{nrm(brand)}] + groups[:4]
        return check.Product(query=query, must=must, exclude=[], gtin=None, label=query, source="назва")
    return check.product_from_args(query=query)          # без моделі — стандартна логіка check


def inbound_ship(merchant) -> float:
    return 0.0 if merchant and "amazon" in merchant.lower() else INBOUND_SHIP


# --------------------------------------------------------------------------- #
# Класифікація результату перевірки (без мережі)
# --------------------------------------------------------------------------- #
def classify(res, *, min_profit=check.MIN_PROFIT, min_roi=check.MIN_ROI) -> str:
    m = res.market
    if m.low5 is None:
        return "no-market"
    if m.n_kept < check.MIN_LOTS or m.n_sellers < check.MIN_SELLERS:
        return "thin-market"
    band_total = m.n_kept + m.band_dropped
    if (m.dispersion is not None and m.dispersion > check.MAX_DISPERSION) or \
            (band_total and m.band_dropped / band_total > check.MAX_BAND_DROP):
        return "mixed-market"
    d = res.deals.get("ebay")
    if d is None or d.net is None:
        return "no-market"
    if d.net >= min_profit and (d.roi or 0) >= min_roi:
        return "candidate"
    return "uneconomic"


def score(net, weeks):
    """Прибуток на тиждень утримання капіталу — головний критерій ранжування."""
    if net is None:
        return None
    return round(net / max(weeks or 1.0, 1.0), 2)


# --------------------------------------------------------------------------- #
# Обробка
# --------------------------------------------------------------------------- #
def process(deal: dict, fetcher, *, stage2_left: list) -> dict:
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **deal}
    why = prefilter(deal)
    if why:
        row.update(outcome="filtered", reason=why)
        return row
    query = make_query(deal["title"])
    buy = round(deal["price"] + inbound_ship(deal.get("merchant")), 2)
    row.update(query=query, buy=buy, flags=sorted({m.group(0).lower() for m in FLAG_TITLE.finditer(deal["title"])}))
    if len(query.split()) < 2:
        row.update(outcome="no-query", reason="запит закороткий")
        return row
    try:
        product = build_product(query)
        res = check.run_check(fetcher, product, min_price=max(1, int(deal["price"] * MIN_PRICE_FRAC)),
                              velocity_sample=0, buy_price=buy)
    except Exception as exc:                                    # noqa: BLE001
        row.update(outcome="error", reason=str(exc)[:160])
        return row
    out = classify(res)
    e = res.deals.get("ebay")
    row.update(outcome=out, market=dict(n=res.market.n_kept, sellers=res.market.n_sellers,
               fast=res.market.low5, median=res.market.median, disp=res.market.dispersion,
               cat=res.market.category_path), fee=res.fee.rate,
               net=getattr(e, "net", None), roi=getattr(e, "roi", None),
               warnings=res.warnings[:2])
    if out == "candidate" and stage2_left[0] > 0:
        stage2_left[0] -= 1
        details = []
        for it in res.market.velocity_items[:check.VELOCITY_SAMPLE]:
            try:
                details.append(fetcher.item(it["itemId"]))
            except Exception:                                    # noqa: BLE001
                pass
        vel = check.build_velocity(details, n_market_lots=res.market.n_kept)
        res2 = check.evaluate(product, res.market, vel, buy_price=buy)
        row.update(verdict=res2.verdict, weeks=vel.weeks_to_sell, vconf=vel.confidence,
                   reasons=res2.reasons[:2],
                   score=score(row["net"], vel.weeks_to_sell))
    elif out == "candidate":
        row.update(verdict="PENDING", reasons=["ліміт вимірів швидкості на запуск"],
                   score=score(row["net"], None))
    return row


def load_seen(path=STATE_FILE) -> set:
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f).get("seen", []))
    except (OSError, ValueError):
        return set()


def save_seen(seen: set, path=STATE_FILE, keep=5000):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"seen": sorted(seen)[-keep:]}, f)


def run_once(max_stage1=MAX_STAGE1, max_stage2=MAX_STAGE2, dry=False, log_path=LOG_FILE):
    deals = fetch_deals()
    seen = load_seen()
    fresh = [d for d in deals if d["link"] not in seen]
    print(f"RSS: {len(deals)} пропозицій, нових {len(fresh)}", file=sys.stderr)
    check._logs_to_stderr()
    fetcher = check.Fetcher()
    stage2_left = [max_stage2]
    stage1 = 0
    rows = []
    for d in fresh:
        pre = prefilter(d)
        if not pre and stage1 >= max_stage1:
            continue                      # відкладено до наступного запуску (не позначаємо seen)
        row = process(d, fetcher, stage2_left=stage2_left) if not dry else {**d, "outcome": pre or "dry"}
        if not pre and not dry:
            stage1 += 1
        rows.append(row)
        seen.add(d["link"])
        time.sleep(0.1)
    if not dry:
        with open(log_path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        save_seen(seen)
    print(f"оброблено {len(rows)}, викликів eBay ≈ {fetcher.calls}", file=sys.stderr)
    return rows


# --------------------------------------------------------------------------- #
# Звіт по категоріях
# --------------------------------------------------------------------------- #
def report(log_path=LOG_FILE, top=12) -> str:
    rows = []
    try:
        with open(log_path, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except OSError:
        return "Лог порожній: спершу python deal_feed.py --once"
    by = defaultdict(lambda: defaultdict(int))
    for r in rows:
        c = r.get("cat") or "?"
        by[c]["всього"] += 1
        by[c][r.get("outcome", "?")] += 1
        if r.get("verdict") == "BUY":
            by[c]["BUY"] += 1
    keys = ["всього", "filtered", "no-market", "thin-market", "mixed-market", "uneconomic", "candidate", "BUY"]
    out = [f"{'категорія mydealz':26}" + "".join(f"{k:>13}" for k in keys)]
    for c, v in sorted(by.items(), key=lambda x: -x[1]["candidate"]):
        out.append(f"{c[:25]:26}" + "".join(f"{v[k]:>13}" for k in keys))
    cands = sorted((r for r in rows if r.get("outcome") == "candidate"),
                   key=lambda r: -(r.get("score") or 0))
    out.append("")
    out.append("КАНДИДАТИ (ринок читабельний + економіка сходиться), за прибутком на тиждень:")
    for r in cands[:top]:
        out.append(f"  {r.get('verdict', '?'):8} закуп €{r['buy']:>7.2f} | eBay швидка €{r['market']['fast']:>7.2f} "
                   f"(n={r['market']['n']}/{r['market']['sellers']}) | чисто €{r['net']:>6.1f} ({(r['roi'] or 0) * 100:.0f}%) "
                   f"| тиж {r.get('weeks')} | {r.get('cat')} | {r['merchant'] or '?'} | {r['title'][:60]}"
                   + (f" | ⚠ {','.join(r['flags'])}" if r.get("flags") else ""))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="mydealz → check → звіт")
    ap.add_argument("--once", action="store_true", help="один прохід по нових пропозиціях")
    ap.add_argument("--report", action="store_true", help="звіт по категоріях із feed_log.jsonl")
    ap.add_argument("--max", type=int, default=MAX_STAGE1, help="макс. перевірок ринку за запуск")
    ap.add_argument("--max2", type=int, default=MAX_STAGE2, help="макс. вимірів швидкості за запуск")
    ap.add_argument("--dry", action="store_true", help="лише фільтр, без викликів eBay")
    a = ap.parse_args(argv)
    if a.once:
        run_once(a.max, a.max2, dry=a.dry)
    if a.report or not a.once:
        print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
