"""Аукціони нового товару, що скоро закінчуються: поточна ставка (+доставка) проти «швидкої» ціни фікс-лотів.
Це знімок ставок, а НЕ фінальних цін (їх API не дає): дивимось, де ще є простір і скільки таких лотів.
READ-ONLY, ~2 виклики на модель. Запуск: python research/auction_probe.py"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
import config
import check
import identity

f = check.Fetcher()
NOW = datetime.now(timezone.utc)
SKUS = [  # (запит, must-групи, exclude)
    ("Toniebox 2 Starterset", [["toniebox"], ["2"]], []),
    ("Oral-B iO 6 Zahnbürste", [["oral"], ["io"], ["6"]], ["kids"]),
    ("Sony DualSense Wireless Controller", [["dualsense"]], ["edge", "ladestation", "halter"]),
    ("Sony WH-1000XM5", [["xm5"]], []),
    ("Sony WH-1000XM4", [["xm4"]], []),
    ("Bose QuietComfort Ultra Kopfhörer", [["bose"], ["ultra"]], ["earbuds"]),
    ("Sonos Era 100", [["sonos"], ["era"], ["100"]], []),
    ("GoPro HERO13 Black", [["gopro"], ["hero13", "13"]], []),
    ("Garmin Forerunner 265", [["garmin"], ["265"]], []),
    ("Nintendo Switch OLED Konsole", [["switch"], ["oled"]], ["spiel", "dock", "hülle"]),
    ("Steam Deck OLED", [["steam"], ["deck"], ["oled"]], []),
    ("Xbox Series X Konsole", [["xbox"], ["series"]], ["controller", "spiel"]),
    ("PlayStation 5 Slim Konsole", [["playstation", "ps5"], ["slim"]], ["controller", "spiel"]),
    ("JBL Charge 5", [["jbl"], ["charge"], ["5"]], []),
    ("Kindle Paperwhite", [["kindle"], ["paperwhite"]], ["hülle"]),
]


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


rows_all = []
for q, must, excl in SKUS:
    prod = check.Product(query=q, must=[{identity.norm(w) for w in g} for g in must],
                         exclude=[identity.norm(w) for w in excl], label=q, source="назва")
    try:
        res = check.run_check(f, prod, velocity_sample=0)
    except Exception as e:
        print(q, "ринок: помилка", e); continue
    fast = res.market.low5
    if fast is None or res.market.n_kept < 5:
        print(f"{q[:34]:34} ринок нечитабельний (лотів {res.market.n_kept})")
        continue
    d = f._get(config.EBAY_BROWSE_SEARCH_URL, {
        "q": q, "filter": "buyingOptions:{AUCTION},conditionIds:{1000},priceCurrency:EUR,itemLocationCountry:DE",
        "sort": "endingSoonest", "limit": 50})
    auc = []
    for it in d.get("itemSummaries") or []:
        title = it["title"]
        if check.match_reasons(title, prod):
            continue
        bid = to_float((it.get("currentBidPrice") or it.get("price") or {}).get("value"))
        so = (it.get("shippingOptions") or [{}])[0]
        ship = to_float((so.get("shippingCost") or {}).get("value")) or 0.0
        try:
            end = datetime.fromisoformat(it["itemEndDate"].replace("Z", "+00:00"))
            hours = (end - NOW).total_seconds() / 3600
        except Exception:
            hours = None
        if bid is None or hours is None or hours < 0:
            continue
        auc.append((hours, bid + ship, it.get("bidCount") or 0, title[:60]))
    soon = [a for a in auc if a[0] <= 48]
    cheap = [a for a in soon if a[1] <= 0.65 * fast]
    print(f"{q[:34]:34} швидка €{fast:>6.0f} (n={res.market.n_kept}) | аукціонів разом {len(auc):>2}, ≤48 год {len(soon):>2}, "
          f"ставка ≤65% швидкої: {len(cheap)}")
    for a in sorted(cheap)[:3]:
        print(f"      за {a[0]:>5.1f} год  ставка+дост €{a[1]:>6.1f} ({a[1] / fast * 100:.0f}%)  ставок {a[2]}  {a[3]}")
    rows_all.append((q, fast, len(auc), len(soon), len(cheap)))
print("\nВикликів:", f.calls)
print("Разом: аукціонів ≤48 год:", sum(r[3] for r in rows_all), "з них ≤65% ринку:", sum(r[4] for r in rows_all))
