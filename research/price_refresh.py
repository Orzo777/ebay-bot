"""Сторож ринкових цін (з 01.10 — щодня; RAM і консолі): ціни продажу, від яких рахуються стелі купівлі.

Реальні продажі (Terapeak) знімаються вручну й рідко, а ціни рухаються (DDR5 ~+12% на місяць, а може й впасти).
Між знімками зсуваємо p25/медіану продажу на ту ж величину, на яку зсунулись ціни ОГОЛОШЕНЬ на eBay:
  коефіцієнт = ask / anchor_ask, де ask — медіана мінімальних цін продавців (згладжена: медіана останніх 3 замірів).
Якір (anchor_ask) фіксується першим заміром після знімка Terapeak; новий знімок Terapeak → якір скидається.
Запобіжники: ≥ MIN_SELLERS продавців; за один день ціна може впасти до −10% (стелю треба знижувати швидко — інакше
переплатимо) і зрости лише до +5% (підвищувати обережно); загалом 0,6–1,8 від Terapeak.
Сповіщення в бот «Облік і продаж»: тип подешевшав на ≥5% за тиждень (стелі вже знижено) або подорожчав на ≥8%;
повторне — лише якщо ціна зсунулась ще на стільки ж від попереднього сповіщення.
Раз на місяць — замір типів, яких ми НЕ купуємо (CANDIDATES): якщо оголошення подорожчали на ≥15% — можливо, нова
прибуткова категорія; її треба заміряти продажами (Terapeak) вручну.
Результат: research/ram_prices.json (types — RAM, consoles — консолі, scan — місячний замір); ram_alert і console_alert
читають його при запуску. Запускає eBay-сторож раз на добу (research/ebay_watch.py), cron — запасний.

Запуск: python research/price_refresh.py [--dry-run]   (~35 викликів Browse API; місячний замір — ще ~20)
"""
import argparse
import re
import json
import os
import statistics
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PRICES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ram_prices.json")
MIN_SELLERS = 12
MIN_SELLERS_CONSOLE = 8
UP_STEP, DOWN_STEP = 0.05, 0.10
LO, HI = 0.6, 1.8
SMOOTH = 3                       # медіана останніх N замірів ask
DROP_ALERT, RISE_ALERT = 0.05, 0.08
ALERT_GAP_DAYS = 3
SCAN_EVERY_DAYS, SCAN_RISE = 28, 0.15
NEW_CONDS, USED_CONDS = "1000|1500|1750", "2750|3000|4000|5000"
RAM_CAT, CONSOLE_CAT = "170083", "139971"
QUERIES = {  # ключ як у ram_alert.REAL → пошуковий запит eBay
    ("ddr5", "udimm", False, 32, 2): "DDR5 32GB 2x16GB",
    ("ddr5", "udimm", False, 64, 2): "DDR5 64GB 2x32GB",
    ("ddr5", "udimm", False, 16, 1): "DDR5 16GB",
    ("ddr5", "udimm", False, 32, 1): "DDR5 32GB 1x32GB",
    ("ddr5", "sodimm", False, 32, 2): "DDR5 SODIMM 32GB 2x16GB",
    ("ddr5", "sodimm", False, 64, 2): "DDR5 SODIMM 64GB 2x32GB",
    ("ddr5", "sodimm", False, 32, 1): "DDR5 SODIMM 32GB",
    ("ddr5", "sodimm", False, 16, 1): "DDR5 SODIMM 16GB",
    ("ddr5", "udimm", False, 48, 2): "DDR5 48GB 2x24GB",
    ("ddr5", "udimm", False, 16, 2): "DDR5 16GB 2x8GB",
    ("ddr4", "sodimm", False, 32, 2): "DDR4 SODIMM 32GB 2x16GB",
    ("ddr4", "udimm", False, 32, 2): "DDR4 32GB 2x16GB",
    ("ddr4", "udimm", False, 64, 2): "DDR4 64GB 2x32GB",
    ("ddr4", "sodimm", False, 32, 1): "DDR4 SODIMM 32GB",
    ("ddr4", "sodimm", False, 64, 2): "DDR4 SODIMM 64GB 2x32GB",
}
CONSOLE_QUERIES = {   # ключ як у console_alert.CONSOLE_TYPES → запит (лише вживані)
    "XBOX_SERIES_X": "xbox series x konsole", "PS5_DISC": "ps5 konsole", "PS5_DIGITAL": "ps5 digital konsole",
    "SWITCH2": "nintendo switch 2 konsole", "SWITCH_OLED": "nintendo switch oled konsole",
    "SWITCH_V2": "nintendo switch konsole", "SWITCH_LITE": "nintendo switch lite",
}
# перша Switch не купується з 04.10 — її ціни не оновлюємо (SWITCH1_ON=1 у console_alert поверне і цей рядок)
if os.getenv("SWITCH1_ON", "0") != "1":
    CONSOLE_QUERIES = {k: v for k, v in CONSOLE_QUERIES.items() if not k.startswith("SWITCH_")}
# Типи, яких ми НЕ купуємо (на 21–26.09 продавались дешево/рідко) — раз на місяць дивимось, чи не подорожчали
CANDIDATES = {
    ("ddr4", "udimm", False, 16, 2): "DDR4 16GB 2x8GB",
    ("ddr4", "sodimm", False, 16, 2): "DDR4 SODIMM 16GB 2x8GB",
    ("ddr4", "sodimm", False, 16, 1): "DDR4 SODIMM 16GB",
    ("ddr4", "udimm", False, 16, 1): "DDR4 16GB",
    ("ddr4", "udimm", False, 64, 4): "DDR4 64GB 4x16GB",
    ("ddr5", "sodimm", False, 16, 2): "DDR5 SODIMM 16GB 2x8GB",
    ("ddr5", "udimm", False, 96, 2): "DDR5 96GB 2x48GB",
    ("ddr4", "server", True, 32, 1): "DDR4 32GB RDIMM ECC",
}


def key_str(key) -> str:
    g, f, e, t, m = key
    return f"{g}|{f}|{int(e)}|{t}|{m}"


def next_ratio(prev_ratio: float, anchor_ask: float | None, ask: float | None, sellers: int,
               min_sellers: int = MIN_SELLERS) -> float:
    """Новий коефіцієнт: вниз до −10% за раз, вгору до +5%, загалом 0,6–1,8. Мало продавців / немає даних → як був."""
    if not anchor_ask or not ask or sellers < min_sellers:
        return prev_ratio
    raw = ask / anchor_ask
    stepped = min(max(raw, prev_ratio * (1 - DOWN_STEP)), prev_ratio * (1 + UP_STEP))
    return min(max(stepped, LO), HI)


def _min_by_seller(items, keep) -> tuple[float | None, int]:
    by_seller = {}
    for it in items:
        if keep(it["title"], it["total"]):
            s = it["seller"] or "?"
            by_seller[s] = min(it["total"], by_seller.get(s, 1e9))
    vals = sorted(by_seller.values())
    if len(vals) >= 5:
        m = statistics.median(vals)
        vals = [v for v in vals if 0.35 * m <= v <= 3 * m]
    return (statistics.median(vals) if vals else None), len(vals)


def market_ask(key, fetch, query: str | None = None) -> tuple[float | None, int]:
    """RAM: медіана мінімальних цін продавців (нове + вживане, з доставкою) для точного типу."""
    from ram_alert import NOISE_BRANDS
    from ram_parse import parse_title

    def keep(title, total):
        p, _ = parse_title(title)
        return bool(p) and p["brand"] not in NOISE_BRANDS and (p["gen"], p["form"], p["ecc"], p["total"], p["modules"]) == key
    q = query or QUERIES[key]
    return _min_by_seller([it for cond in (NEW_CONDS, USED_CONDS) for it in fetch(q, cond, RAM_CAT)], keep)


def console_ask(ckey: str, fetch) -> tuple[float | None, int]:
    """Консолі: вживані, лише ті оголошення, які наш оцінювач визнає саме цим типом (не гра/аксесуар/дефект)."""
    import console_alert
    name = console_alert.CONSOLE_TYPES[ckey]["name"]

    def keep(title, total):
        r = console_alert.evaluate_console(title, total)
        return bool(r) and "quick_sale" in r and r.get("type") == name
    return _min_by_seller(fetch(CONSOLE_QUERIES[ckey], USED_CONDS, CONSOLE_CAT), keep)


def ebay_fetch(q: str, cond: str, cat: str | None = None) -> list[dict]:
    import config
    import check
    from main import _request_with_backoff, _to_float

    hdr = ebay_fetch.client._headers() if hasattr(ebay_fetch, "client") else None
    if hdr is None:
        ebay_fetch.client = check.Fetcher().client
        hdr = ebay_fetch.client._headers()
    params = {"q": q, "limit": 200,
              "filter": f"buyingOptions:{{FIXED_PRICE}},conditionIds:{{{cond}}},price:[3..],priceCurrency:EUR,itemLocationCountry:DE"}
    if cat:
        params["category_ids"] = cat
    d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=hdr, params=params)
    out = []
    for it in d.get("itemSummaries") or []:
        price = _to_float((it.get("price") or {}).get("value"))
        if price is None:
            continue
        ship = _to_float(((it.get("shippingOptions") or [{}])[0].get("shippingCost") or {}).get("value")) or 0.0
        out.append({"title": it.get("title", ""), "seller": (it.get("seller") or {}).get("username"), "total": price + ship})
    return out


def _cap(med: float, console: bool, ship: float | None = None) -> float:
    """Стеля купівлі (повна вартість), як у ram_alert / console_alert: від медіани, ≥ 50 € і ≥ 20% (10.10)."""
    import console_alert
    import ram_alert
    net = med - (console_alert.costs(med, ship or console_alert.SHIP) if console else ram_alert.costs(med))
    return ram_alert.caps_from(net)[0]


def _update(e: dict, base: dict, ask: float | None, sellers: int, today: str, min_sellers: int,
            console: bool, ship: float | None) -> str | None:
    """Один тип: заміри, коефіцієнт, ціни, історія; → рядок сповіщення (або None)."""
    if (e.get("p25_tp"), e.get("med_tp")) != (base["p25"], base["med"]):
        # новий знімок Terapeak у коді → відлік зсуву заново від сьогодні
        e.update(name=base["name"], p25_tp=base["p25"], med_tp=base["med"], ratio=1.0, anchor_ask=None,
                 p25=base["p25"], med=base["med"], asks=[], hist=[])
    asks = [a for a in e.get("asks", []) if a[0] != today]
    if ask and sellers >= min_sellers:
        asks.append([today, round(ask, 2)])
    e["asks"] = asks[-7:]
    smooth = statistics.median([a[1] for a in e["asks"][-SMOOTH:]]) if e["asks"] else None
    if e.get("anchor_ask") is None and smooth:
        e["anchor_ask"], e["anchor_date"] = round(smooth, 2), today
    e["ratio"] = round(next_ratio(e.get("ratio", 1.0), e.get("anchor_ask"), smooth, sellers if ask else 0, min_sellers), 4)
    e.update(ask=round(ask, 2) if ask else None, sellers=sellers, updated=today,
             p25=round(e["p25_tp"] * e["ratio"]), med=round(e["med_tp"] * e["ratio"]))
    hist = [h for h in e.get("hist", []) if h[0] != today] + [[today, e["p25"]]]
    e["hist"] = hist[-30:]
    week_ago = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    past = [h for h in e["hist"] if h[0] <= week_ago] or e["hist"][:1]
    then = past[-1][1]
    last = e.get("last_alert")
    since = (date.fromisoformat(today) - date.fromisoformat(last)).days if last else 99
    if since < ALERT_GAP_DAYS:
        return None
    if since <= 7 and e.get("alert_p25"):   # уже повідомляли цього тижня — лише про НОВИЙ зсув від тієї ціни
        then = e["alert_p25"]
    change = e["p25"] / then - 1 if then else 0
    prev = e["hist"][-2][1] if len(e["hist"]) >= 2 else e["p25"]
    if abs(e["p25"] / prev - 1) > 0.02 and abs(change) < 0.15:   # ціна ще рухається — повідомимо, коли встане (стелі вже зсунуто)
        return None
    if change <= -DROP_ALERT or change >= RISE_ALERT:
        e["last_alert"], e["alert_p25"] = today, e["p25"]
        arrow = "📉" if change < 0 else "📈"
        return (f"{arrow} {base['name']}: {100 * change:+.0f}% за тиждень — швидкий продаж {then} → {e['p25']} €, "
                f"стеля купівлі {_cap(then * e['med'] / e['p25'], console, ship):.0f} → {_cap(e['med'], console, ship):.0f} €")
    return None


def refresh(data: dict, fetch, today: str) -> tuple[dict, list[str]]:
    """Оновлює структуру цін на місці; повертає (дані, рядки сповіщень)."""
    import console_alert
    from ram_alert import REAL_BASE

    changes = []
    types = data.setdefault("types", {})
    for key, base in REAL_BASE.items():
        e = types.setdefault(key_str(key), {"name": base["name"], "p25_tp": base["p25"], "med_tp": base["med"],
                                            "ratio": 1.0, "anchor_ask": None})
        try:
            ask, sellers = market_ask(key, fetch)
        except Exception as ex:   # один тип не зірвав — решта оновлюється
            print(f"{base['name']}: {ex.__class__.__name__}")
            ask, sellers = None, 0
        msg = _update(e, base, ask, sellers, today, MIN_SELLERS, False, None)
        if msg:
            changes.append(msg)
    cons = data.setdefault("consoles", {})
    for ckey, (p25, med) in console_alert.CONSOLE_BASE.items():
        t = console_alert.CONSOLE_TYPES[ckey]
        base = {"p25": p25, "med": med, "name": t["name"]}
        e = cons.setdefault(ckey, {"name": t["name"], "p25_tp": p25, "med_tp": med, "ratio": 1.0, "anchor_ask": None})
        try:
            ask, sellers = console_ask(ckey, fetch)
        except Exception as ex:
            print(f"{t['name']}: {ex.__class__.__name__}")
            ask, sellers = None, 0
        msg = _update(e, base, ask, sellers, today, MIN_SELLERS_CONSOLE, True, t.get("ship"))
        if msg:
            changes.append(msg)
    data["updated"] = today
    return data, changes


def scan(data: dict, fetch, today: str) -> list[str]:
    """Раз на ~місяць: типи, яких ми не купуємо. Подорожчали на ≥15% — можливо, нова прибуткова категорія."""
    s = data.setdefault("scan", {})
    if s.get("date") and (date.fromisoformat(today) - date.fromisoformat(s["date"])).days < SCAN_EVERY_DAYS:
        return []
    out = []
    types = s.setdefault("types", {})
    for key, q in CANDIDATES.items():
        try:
            ask, sellers = market_ask(key, fetch, q)
        except Exception as ex:
            print(f"замір {q}: {ex.__class__.__name__}")
            continue
        if not ask or sellers < MIN_SELLERS:
            continue
        prev = types.get(key_str(key))
        types[key_str(key)] = {"ask": round(ask, 2), "sellers": sellers, "date": today,
                               "prev_ask": prev["ask"] if prev else None, "prev_date": prev["date"] if prev else None}
        if prev and ask / prev["ask"] - 1 >= SCAN_RISE:
            g, f, _, t, m = key
            label = f"{g.upper()} {'SO-DIMM' if f == 'sodimm' else 'серверна' if f == 'server' else 'DIMM'} {t} ГБ" + \
                (f" ({m}×{t // m})" if m > 1 else "")
            out.append(f"• {label}: оголошення {prev['ask']:.0f} → {ask:.0f} € ({100 * (ask / prev['ask'] - 1):+.0f}% з {prev['date'][8:10]}.{prev['date'][5:7]})")
    s["date"] = today
    if out:
        out = ["🔭 Можливо, нова прибуткова категорія — ці типи ми не купуємо, але оголошення помітно подорожчали:", *out,
               "Варто заміряти реальні продажі (Terapeak) — скажи Claude, він перевірить і, якщо вигідно, додасть у пошук."]
    return out


# ----------------------------------------------------------------------------- деталі ПК (09.10)
# Ціни відеокарт і процесорів у ПК-боті були «зашиті» (дві хибні картки 09.10 — саме звідти). Раз на місяць:
# 0.65 × p25 вживаних оголошень eBay.de (відеокарти) і 0.7 × p25 (процесори) — та сама методика, якою таблицю заповнили;
# зміна за раз — не більше ±25% (один шумний замір не перекидає оцінки). ~60 запитів API на місяць.
PC_EVERY_DAYS, PC_MIN_ITEMS, PC_MAX_STEP = 30, 8, 0.25
CPU_MODELS = {"i5|8": "i5-8400", "i5|9": "i5-9400", "i5|10": "i5-10400", "i5|11": "i5-11400", "i5|12": "i5-12400",
              "i5|13": "i5-13400", "i7|8": "i7-8700", "i7|9": "i7-9700", "i7|10": "i7-10700", "i7|11": "i7-11700",
              "i7|12": "i7-12700", "i7|13": "i7-13700", "i9|9": "i9-9900", "i9|10": "i9-10900", "i9|12": "i9-12900",
              "i9|13": "i9-13900", "r5|2": "ryzen 5 2600", "r5|3": "ryzen 5 3600", "r5|5": "ryzen 5 5600", "r5|7": "ryzen 5 7600",
              "r7|2": "ryzen 7 2700", "r7|3": "ryzen 7 3700", "r7|5": "ryzen 7 5800", "r7|7": "ryzen 7 7700", "r9|5": "ryzen 9 5900",
              "r9|7": "ryzen 9 7900"}
_PC_JUNK = re.compile(r"defekt|bastler|laptop|notebook|\bpc\b|komplett|bundle|set\b|kühler|lüfter|ohne|nur\s|tausch|suche", re.I)


def _p25(prices: list[float]) -> float | None:
    prices = sorted(prices)
    return prices[len(prices) // 4] if len(prices) >= PC_MIN_ITEMS else None


def _gpu_re(model: str) -> re.Pattern:   # «rtx 3060» без «ti», «rtx 3060 ti» — саме ti
    parts = model.split()
    base, num, suffix = parts[0], parts[1], parts[2] if len(parts) > 2 else ""
    tail = rf"\s?-?{suffix}\b" if suffix else r"(?!\s?-?(?:ti|super|xt)\b)"
    return re.compile(rf"\b{base}\s?-?{num}{tail}", re.I)


def pc_parts(data: dict, fetch, today: str) -> list[str]:
    """Раз на місяць оновити ціни деталей ПК-бота → data['pc_parts'] = {'gpu': {...}, 'cpu': {...}, 'date': …}."""
    import pc_alert
    pp = data.setdefault("pc_parts", {})
    if pp.get("date") and (date.fromisoformat(today) - date.fromisoformat(pp["date"])).days < PC_EVERY_DAYS:
        return []
    out = []
    for kind, base, share, cat in (("gpu", pc_alert.GPU_PART_BASE, 0.65, "27386"), ("cpu", pc_alert.CPU_PART_BASE, 0.7, "164")):
        cur = pp.setdefault(kind, {})
        for key, old in base.items():
            k = key if kind == "gpu" else f"{key[0]}|{key[1]}"
            q = key if kind == "gpu" else CPU_MODELS.get(k)
            if not q:
                continue
            want = _gpu_re(key) if kind == "gpu" else re.compile(re.escape(q).replace("\\ ", r"\s?-?").replace("\\-", r"[\s-]?"), re.I)
            try:
                items = fetch(q, "3000", cat)
            except Exception as ex:
                print(f"деталі {q}: {ex.__class__.__name__}")
                continue
            p = _p25([i["total"] for i in items if want.search(i["title"]) and not _PC_JUNK.search(i["title"])])
            if not p:
                continue
            prev = cur.get(k, old)
            new = round(min(max(share * p, prev * (1 - PC_MAX_STEP)), prev * (1 + PC_MAX_STEP)))
            cur[k] = new
            if abs(new - prev) >= max(5, 0.1 * prev):
                out.append(f"• {q.upper() if kind == 'gpu' else q}: {prev:.0f} → {new} €")
    pp["date"] = today
    return (["🖥 Ціни деталей ПК-бота оновлено (раз на місяць, eBay.de):", *out] if out else [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        with open(PRICES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {"terapeak_date": "2026-09-21"}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data, changes = refresh(data, ebay_fetch, today)
    found = scan(data, ebay_fetch, today)
    parts = pc_parts(data, ebay_fetch, today)
    import shortage_watch   # 10.10: сторож дефіциту — раз на 6 днів, ~40 запитів
    print(f"сторож дефіциту: заміряно {shortage_watch.update(data, ebay_fetch, today)} товарів")
    for group in ("types", "consoles"):
        for e in data[group].values():
            print(f"{e['name']:34} продавців {e['sellers']:3} ask {e['ask']} якір {e['anchor_ask']} ×{e['ratio']} → p25 €{e['p25']} мед €{e['med']}")
    if args.dry_run:
        print("\n".join(changes + found))
        return
    with open(PRICES_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    import health
    if changes:
        health.office_send("Сторож цін — стелі купівлі вже перераховано, бот рахує за новими цінами:\n" + "\n".join(changes))
    if found:
        health.office_send("\n".join(found))
    if parts:
        health.office_send("\n".join(parts))


if __name__ == "__main__":
    main()
