import json, sys, statistics
SP = sys.argv[1]
rows = json.load(open(SP + "/xmarket.json", encoding="utf-8"))
FEE = {"LEGO": 0.14, "TECH": 0.07}
SHIP_OUT = 6.19


def ok_seller(x):
    return x[1] >= 25 and x[2] >= 97.0


res = []
for r in rows:
    de = r["mk"]["EBAY_DE"]["cheapest"]
    de_good = [x for x in de if ok_seller(x)]
    if len(de_good) < 3:
        res.append((r["kind"], r["name"], None, "DE<3 ref"))
        continue
    ref = statistics.median(x[0] for x in de_good[:5])         # медіана 5 найдешевших у DE (наш «ринок продажу»)
    best = None
    for mk, v in r["mk"].items():
        if mk == "EBAY_DE":
            continue
        for x in v["cheapest"]:
            if not ok_seller(x):
                continue
            if x[0] < 0.4 * ref:                                # відсікаємо явні аксесуари/не той товар
                continue
            if best is None or x[0] < best[0]:
                best = (x[0], mk, x[4], x[3])
            break
    if not best:
        res.append((r["kind"], r["name"], ref, "no cross"))
        continue
    buy = best[0]
    fee = FEE[r["kind"]] * ref + 0.45
    profit = ref - fee - SHIP_OUT - buy
    res.append((r["kind"], r["name"], ref, buy, best[1], profit, profit / buy * 100, best[2]))

print("kind  name  DE_ref  buy(cross,incl.ship-to-DE)  market  profit€  ROI%")
for x in sorted([x for x in res if len(x) > 4], key=lambda x: -x[6]):
    print(f"{x[0]:5} {x[1][:34]:34} {x[2]:>7.1f} {x[3]:>7.1f} {x[4][5:]:>3} {x[5]:>7.1f} {x[6]:>6.0f}%  {x[7][:40]}")
print("\nbez cross/ref:", [(x[1], x[3]) for x in res if len(x) <= 4])
pos = [x for x in res if len(x) > 4 and x[5] > 0]
for kind in ("LEGO", "TECH"):
    k = [x for x in res if len(x) > 4 and x[0] == kind]
    print(kind, "n", len(k), "profit>0:", sum(1 for x in k if x[5] > 0), "median profit", round(statistics.median([x[5] for x in k]), 1) if k else None)
# по маркетплейсах: як часто найдешевший саме там
from collections import Counter
print(Counter(x[4] for x in res if len(x) > 4 and x[5] > 0))
