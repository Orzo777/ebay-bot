import json, sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict
sys.path.insert(0, r"D:\ebay-bot")
import config, identity, quality
from main import total_price

print("=== ЧИСТИЙ ПРИБУТОК З ОДНІЄЇ УГОДИ (комісія eBay + 0,45 + пересилка 6,19 DHL, без ПДВ/податків) ===")
print("M = ціна продажу з доставкою (те, що платить покупець); купуємо на d% нижче M (включно з доставкою до нас)")
cats = [("LEGO/іграшки 14%", 0.14), ("TCG/ігри/модельбау 11-12%", 0.115), ("Техніка нова 7%", 0.07), ("Уживане/refurb 5%", 0.05)]
for M in (40, 80, 150, 300):
    print(f"\nМ = €{M}")
    print(f"{'категорія':28}" + "".join(f"  d={d:>2}%: €/ROI".rjust(17) for d in (15, 20, 25, 30, 40)))
    for name, f in cats:
        cells = []
        for d in (15, 20, 25, 30, 40):
            B = M * (1 - d / 100)
            p = M - f * M - 0.45 - 6.19 - B
            cells.append(f"{p:>6.1f}/{p / B * 100:>4.0f}%".rjust(17))
        print(f"{name:28}" + "".join(cells))
print("\nБезбитковість: LEGO M=80 → купити нижче", round(80 - 0.14 * 80 - 0.45 - 6.19, 1), "=", round((1 - (80 - 0.14 * 80 - 0.45 - 6.19) / 80) * 100), "% нижче ринку")
print("               Техніка M=200 → купити нижче", round(200 - 0.07 * 200 - 0.45 - 6.19, 1), "=", round((1 - (200 - 0.07 * 200 - 0.45 - 6.19) / 200) * 100), "% нижче ринку")

# --- Best Offer: скільки лотів пройшли б поріг, якщо пропозиція -10% ---
SP = sys.argv[1]
corpus = json.load(open(SP + "/corpus_de.json", encoding="utf-8"))
now = datetime.now(timezone.utc)
live = {c["query"]: c for c in config.CATEGORIES}
tot = bo_n = plain = bo_q = bo_fresh = pl_fresh = 0
for q, cat in live.items():
    pool = []
    for it in corpus[q]["items"]:
        if quality.listing_flags(it):
            continue
        r = identity.describe(it["title"], cat)
        tp = total_price(it)
        if r.exclude or tp is None or tp < cat["min_price"]:
            continue
        pool.append((r.key, tp, (it.get("seller") or {}).get("username") or "?", it))
    by = defaultdict(list)
    for row in pool:
        by[row[0]].append(row)
    for k, rows in by.items():
        for (_k, tp, s, it) in rows:
            comps = [(p, ss, i["itemId"]) for (_kk, p, ss, i) in rows if i["itemId"] != it["itemId"]]
            v = quality.assess(tp, comps, s)
            if "thin-ref" in v.reasons or v.median is None:
                continue
            tot += 1
            bo = "BEST_OFFER" in (it.get("buyingOptions") or [])
            ratio = tp / v.median
            if bo:
                bo_n += 1
                if ratio * 0.9 <= 0.80 and v.median - tp * 0.9 >= 25:
                    bo_q += 1
            if ratio <= 0.80 and v.median - tp >= 25:
                plain += 1
print(f"\nBest Offer: оцінних лотів {tot}, з них із BO {bo_n} ({bo_n / max(tot, 1) * 100:.0f}%); "
      f"проходять поріг −20% БЕЗ торгу: {plain}; додатково з пропозицією −10% (лише BO-лоти): {bo_q}")
