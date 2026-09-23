"""Щотижневе оновлення цін продажу RAM, від яких ram_alert рахує стелі купівлі.

Реальні продажі (Terapeak) знімаються вручну й рідко, а ціни рухаються (DDR5 ~+12% на місяць).
Між знімками зсуваємо p25/медіану продажу на ту ж величину, на яку зсунулись ціни ОГОЛОШЕНЬ на eBay:
  коефіцієнт = медіана мінімальних цін продавців зараз / та сама медіана в момент якоря.
Якір (anchor_ask) фіксується при першому запуску після знімка Terapeak; новий знімок Terapeak → скинути якір.
Запобіжники: ≥ MIN_SELLERS продавців, крок ≤ ±15% за тиждень, загалом у межах 0,6–1,8 від Terapeak.
Результат: research/ram_prices.json; ram_alert.py читає його, якщо він є.

Запуск: python research/price_refresh.py [--dry-run]   (16 викликів Browse API)
"""
import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
sys.path.insert(0, "research")

PRICES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ram_prices.json")
MIN_SELLERS = 12
STEP = 0.15
LO, HI = 0.6, 1.8
QUERIES = {  # ключ як у ram_alert.REAL → пошуковий запит eBay
    ("ddr5", "udimm", False, 32, 2): "DDR5 32GB 2x16GB",
    ("ddr5", "udimm", False, 64, 2): "DDR5 64GB 2x32GB",
    ("ddr5", "udimm", False, 16, 1): "DDR5 16GB",
    ("ddr5", "sodimm", False, 32, 2): "DDR5 SODIMM 32GB 2x16GB",
    ("ddr5", "sodimm", False, 32, 1): "DDR5 SODIMM 32GB",
    ("ddr5", "sodimm", False, 16, 1): "DDR5 SODIMM 16GB",
    ("ddr4", "udimm", False, 32, 2): "DDR4 32GB 2x16GB",
    ("ddr4", "udimm", False, 64, 2): "DDR4 64GB 2x32GB",
    ("ddr4", "sodimm", False, 32, 1): "DDR4 SODIMM 32GB",
    ("ddr4", "sodimm", False, 64, 2): "DDR4 SODIMM 64GB 2x32GB",
}


def key_str(key) -> str:
    g, f, e, t, m = key
    return f"{g}|{f}|{int(e)}|{t}|{m}"


def next_ratio(prev_ratio: float, anchor_ask: float | None, ask: float | None, sellers: int) -> float:
    """Новий коефіцієнт із запобіжниками. Мало продавців або немає даних → лишаємо попередній."""
    if not anchor_ask or not ask or sellers < MIN_SELLERS:
        return prev_ratio
    raw = ask / anchor_ask
    stepped = min(max(raw, prev_ratio * (1 - STEP)), prev_ratio * (1 + STEP))
    return min(max(stepped, LO), HI)


def market_ask(key, fetch) -> tuple[float | None, int]:
    """Медіана мінімальних цін продавців (нове + вживане, з доставкою) для точного типу."""
    from ram_alert import NOISE_BRANDS
    from ram_parse import parse_title

    by_seller = {}
    for cond in ("1000|1500|1750", "2750|3000|4000|5000"):
        for it in fetch(QUERIES[key], cond):
            p, _ = parse_title(it["title"])
            if not p or p["brand"] in NOISE_BRANDS or p["ecc"]:
                continue
            if (p["gen"], p["form"], p["ecc"], p["total"], p["modules"]) != key:
                continue
            s = it["seller"] or "?"
            by_seller[s] = min(it["total"], by_seller.get(s, 1e9))
    vals = sorted(by_seller.values())
    if len(vals) >= 5:
        m = statistics.median(vals)
        vals = [v for v in vals if 0.35 * m <= v <= 3 * m]
    return (statistics.median(vals) if vals else None), len(vals)


def ebay_fetch(q: str, cond: str) -> list[dict]:
    import config
    import check
    from main import _request_with_backoff, _to_float

    hdr = ebay_fetch.client._headers() if hasattr(ebay_fetch, "client") else None
    if hdr is None:
        ebay_fetch.client = check.Fetcher().client
        hdr = ebay_fetch.client._headers()
    params = {"q": q, "limit": 200,
              "filter": f"buyingOptions:{{FIXED_PRICE}},conditionIds:{{{cond}}},price:[3..],priceCurrency:EUR,itemLocationCountry:DE"}
    d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=hdr, params=params)
    out = []
    for it in d.get("itemSummaries") or []:
        price = _to_float((it.get("price") or {}).get("value"))
        if price is None:
            continue
        ship = _to_float(((it.get("shippingOptions") or [{}])[0].get("shippingCost") or {}).get("value")) or 0.0
        out.append({"title": it.get("title", ""), "seller": (it.get("seller") or {}).get("username"), "total": price + ship})
    return out


def refresh(data: dict, fetch, today: str) -> tuple[dict, list[str]]:
    """Оновлює структуру цін на місці; повертає (дані, рядки змін для Telegram)."""
    from ram_alert import REAL_BASE

    types = data.setdefault("types", {})
    changes = []
    for key, base in REAL_BASE.items():
        ks = key_str(key)
        e = types.setdefault(ks, {"name": base["name"], "p25_tp": base["p25"], "med_tp": base["med"], "ratio": 1.0,
                                  "anchor_ask": None})
        if (e["p25_tp"], e["med_tp"]) != (base["p25"], base["med"]):
            # новий знімок Terapeak у ram_alert.REAL_BASE → починаємо відлік зсуву заново від сьогодні
            e.update(name=base["name"], p25_tp=base["p25"], med_tp=base["med"], ratio=1.0, anchor_ask=None,
                     p25=base["p25"], med=base["med"])
        ask, sellers = market_ask(key, fetch)
        if e["anchor_ask"] is None and ask and sellers >= MIN_SELLERS:
            e["anchor_ask"] = round(ask, 2)
            e["anchor_date"] = today
        old_p25 = round(e["p25_tp"] * e["ratio"])
        e["ratio"] = round(next_ratio(e["ratio"], e["anchor_ask"], ask, sellers), 4)
        e.update(ask=round(ask, 2) if ask else None, sellers=sellers, updated=today,
                 p25=round(e["p25_tp"] * e["ratio"]), med=round(e["med_tp"] * e["ratio"]))
        if old_p25 and abs(e["p25"] / old_p25 - 1) >= 0.02:
            changes.append(f"{base['name']}: швидкий продаж €{old_p25} → €{e['p25']} ({(e['p25'] / old_p25 - 1) * 100:+.0f}%)")
    data["updated"] = today
    return data, changes


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
    for ks, e in data["types"].items():
        print(f"{e['name']:32} продавців {e['sellers']:3} ask {e['ask']} якір {e['anchor_ask']} ×{e['ratio']} → p25 €{e['p25']} мед €{e['med']}")
    if args.dry_run:
        return
    with open(PRICES_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    if changes:
        import config
        import main as botmain
        if config.TELEGRAM_CREDS_OK:
            botmain.send_telegram("💶 Ціни RAM оновлено (стелі купівлі зсунулись разом із ринком):\n" + "\n".join(changes))


if __name__ == "__main__":
    main()
