"""Ринок оперативної пам'яті на eBay.de: нове vs вживане, планки й кіти, DDR5/DDR4, UDIMM/SO-DIMM/RDIMM.
READ-ONLY, ~30 викликів. Показує: лотів, продавців, мін/швидка/медіана, розкид, категорію й комісію, швидкість продажів."""
import re
import statistics
import sys

sys.path.insert(0, ".")
import config
import check
import econ_model as E

f = check.Fetcher()
NEW = "1000|1500|1750"
USED = "2750|3000|4000|5000"
NOISE = re.compile(r"\b(defekt|bastler|ersatzteil|kühler|kuehler|heatsink|blende|leer|nur verpackung|zubeh|lüfter)\b", re.I)

CONFIGS = [
    # (мітка, запит, обов'язкові regex (усі), заборонені regex)
    ("DDR5 UDIMM 32GB кіт 2x16GB", "DDR5 32GB 2x16GB 5600 UDIMM", [r"ddr\s?-?5", r"2\s?x\s?16\s?gb|16\s?gb\s?x\s?2|2\s?x\s?16\b"],
     [r"so-?dimm|laptop|notebook|rdimm|ecc|server|2x32|64\s?gb"]),
    ("DDR5 UDIMM 16GB планка", "DDR5 16GB 5600 UDIMM", [r"ddr\s?-?5", r"(?<![\dx])16\s?gb"],
     [r"2\s?x|kit|32\s?gb|so-?dimm|laptop|notebook|rdimm|ecc|server|64\s?gb"]),
    ("DDR5 UDIMM 32GB планка", "DDR5 32GB 5600 UDIMM", [r"ddr\s?-?5", r"(?<![\dx])32\s?gb"],
     [r"2\s?x|kit|16\s?gb|so-?dimm|laptop|notebook|rdimm|ecc|server|64\s?gb"]),
    ("DDR5 UDIMM 64GB кіт 2x32GB", "DDR5 64GB 2x32GB 5600", [r"ddr\s?-?5", r"2\s?x\s?32\s?gb|32\s?gb\s?x\s?2"],
     [r"so-?dimm|laptop|notebook|rdimm|ecc|server"]),
    ("DDR5 SO-DIMM 16GB (ноутбук)", "DDR5 SODIMM 16GB 5600 Laptop", [r"ddr\s?-?5", r"so-?dimm|laptop|notebook", r"(?<![\dx])16\s?gb"],
     [r"2\s?x|kit|32\s?gb"]),
    ("DDR5 SO-DIMM 32GB (ноутбук)", "DDR5 SODIMM 32GB 5600 Laptop", [r"ddr\s?-?5", r"so-?dimm|laptop|notebook", r"(?<![\dx])32\s?gb"],
     [r"2\s?x|kit|16\s?gb|64\s?gb"]),
    ("DDR4 UDIMM 32GB кіт 2x16GB", "DDR4 32GB 2x16GB 3200 UDIMM", [r"ddr\s?-?4", r"2\s?x\s?16\s?gb|16\s?gb\s?x\s?2"],
     [r"so-?dimm|laptop|notebook|rdimm|ecc|server"]),
    ("DDR5 RDIMM/ECC 64GB (сервер)", "DDR5 64GB RDIMM ECC 4800", [r"ddr\s?-?5", r"rdimm|ecc|reg", r"(?<![\dx])64\s?gb"],
     [r"2\s?x|kit"]),
    ("DDR4 RDIMM/ECC 32GB (сервер)", "DDR4 32GB RDIMM ECC 2666", [r"ddr\s?-?4", r"rdimm|ecc|reg", r"(?<![\dx])32\s?gb"],
     [r"2\s?x|kit"]),
]


def fetch(q, cond, cc="DE"):
    flt = f"buyingOptions:{{FIXED_PRICE}},conditionIds:{{{cond}}},price:[15..],priceCurrency:EUR,itemLocationCountry:{cc}"
    d = f._get(config.EBAY_BROWSE_SEARCH_URL, {"q": q, "filter": flt, "limit": 200})
    return d.get("itemSummaries") or [], int(d.get("total") or 0)


def total(it):
    return check._total_price(it)


def stats(rows):
    by_seller = {}
    for p, s, t in rows:
        by_seller[s] = min(p, by_seller.get(s, 1e9))
    v = sorted(by_seller.values())
    if len(v) < 3:
        return None
    return dict(n=len(rows), sellers=len(v), min=v[0], fast=statistics.median(v[:5]), med=statistics.median(v),
                disp=(statistics.quantiles(v, n=4)[2] - statistics.quantiles(v, n=4)[0]) / statistics.median(v) if len(v) >= 4 else None)


print(f"{'конфігурація':32}{'стан':7}{'лотів':>6}{'продавців':>10}{'мін':>7}{'швидка':>8}{'медіана':>8}{'розкид':>7}  категорія → комісія")
sample = {}
for label, q, req, forb in CONFIGS:
    for cond_name, cond in (("нове", NEW), ("вжив.", USED)):
        its, tot = fetch(q, cond)
        rows = []
        cat = None
        for it in its:
            t = it["title"].lower()
            if NOISE.search(t) or not all(re.search(r, t) for r in req) or any(re.search(r, t) for r in forb):
                continue
            p = total(it)
            if p is None or "3-4-3" in t:
                continue
            sl = (it.get("seller") or {})
            if (sl.get("feedbackScore") or 0) < 10:
                continue
            rows.append((p, sl.get("username"), it["title"][:70]))
            cat = cat or " > ".join(c["categoryName"] for c in (it.get("categories") or [])[:2])
        st = stats(rows)
        if not st:
            print(f"{label:32}{cond_name:7}{len(rows):>6}  (замало лотів/продавців; eBay total={tot})")
            continue
        fee = E.fee_category(cat, condition_id=("3000" if cond == USED else "1000"), price=st["fast"])
        print(f"{label:32}{cond_name:7}{st['n']:>6}{st['sellers']:>10}{st['min']:>7.0f}{st['fast']:>8.0f}{st['med']:>8.0f}"
              f"{(st['disp'] if st['disp'] is not None else 0):>7.2f}  {cat} → {fee.rate:.0%}")
        sample[(label, cond_name)] = sorted(rows)[:3]
print("\nНайдешевші придатні лоти по ключових конфігураціях:")
for k in [("DDR5 UDIMM 32GB кіт 2x16GB", "вжив."), ("DDR5 UDIMM 32GB кіт 2x16GB", "нове"), ("DDR5 UDIMM 16GB планка", "вжив."), ("DDR5 UDIMM 16GB планка", "нове")]:
    for r in sample.get(k, []):
        print(f"  {k[0][:26]:26} {k[1]:6} €{r[0]:>6.0f}  {r[2]}")
print("\nВикликів:", f.calls)
