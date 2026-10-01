"""Скан оперативної пам'яті на eBay.de за €/ГБ: усі формати (фікс + аукціони), нове й вживане.
Нормування: покоління (DDR3/4/5) × форм-фактор (UDIMM/SO-DIMM/сервер) × ємність (ГБ) → €/ГБ; викиди нижче медіани групи.
READ-ONLY. Запуск: python research/ram_scan.py"""
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, ".")
import config
import check

f = check.Fetcher()
NOW = datetime.now(timezone.utc)
QUERIES = ["DDR5 RAM", "DDR5 Arbeitsspeicher 32GB", "DDR5 16GB", "DDR5 5600 Kit", "DDR5 6000 Kit", "DDR5 UDIMM", "DDR5 SODIMM", "DDR4 32GB Kit",
           "DDR4 RAM 16GB", "DDR4 3200 Kit", "SK Hynix DDR5", "Samsung DDR5 RAM", "Kingston Fury DDR5", "Corsair Vengeance DDR5"]
JUNK = re.compile(r"\b(defekt|bastler|ersatzteil|kühler|kuehler|heatsink|leer|nur verpackung|lüfter|gehäuse|mainboard|motherboard|"
                  r"gaming pc|komplett|laptop\b(?!.*so-?dimm)|notebook\b(?!.*so-?dimm)|grafikkarte|ssd|festplatte|cpu|prozessor|bundle|set mit)\b", re.I)


def parse(title):
    t = title.lower()
    gen = "ddr5" if re.search(r"ddr\s?-?5|pc5", t) else "ddr4" if re.search(r"ddr\s?-?4|pc4", t) else None
    if not gen:
        return None
    form = "sodimm" if re.search(r"so-?dimm|laptop|notebook", t) else "server" if re.search(r"rdimm|lrdimm|ecc reg|reg\.? ?ecc|server", t) else "udimm"
    m = re.search(r"(\d)\s?x\s?(\d{1,3})\s?gb", t)
    if m:
        gb = int(m.group(1)) * int(m.group(2))
    else:
        m = re.search(r"(?<![\d.x])(\d{1,3})\s?gb", t)
        if not m:
            return None
        gb = int(m.group(1))
    if gb not in (4, 8, 16, 24, 32, 48, 64, 96, 128):
        return None
    return gen, form, gb


def fetch(q, opts, cond):
    flt = f"buyingOptions:{{{opts}}},conditionIds:{{{cond}}},price:[10..],priceCurrency:EUR,itemLocationCountry:DE"
    d = f._get(config.EBAY_BROWSE_SEARCH_URL, {"q": q, "filter": flt, "limit": 200})
    return d.get("itemSummaries") or []


rows = {}
for q in QUERIES:
    for opts, cond in (("FIXED_PRICE", "1000|1500|1750"), ("FIXED_PRICE", "2750|3000|4000|5000"), ("AUCTION", "1000|1500|1750|2750|3000|4000|5000")):
        for it in fetch(q, opts, cond):
            iid = it["itemId"]
            if iid in rows or JUNK.search(it["title"]):
                continue
            pr = parse(it["title"])
            if not pr:
                continue
            bid = (it.get("currentBidPrice") or it.get("price") or {}).get("value")
            try:
                price = float(bid)
            except (TypeError, ValueError):
                continue
            so = (it.get("shippingOptions") or [{}])[0]
            ship = float(((so.get("shippingCost") or {}).get("value")) or 0)
            end = None
            if it.get("itemEndDate"):
                end = (datetime.fromisoformat(it["itemEndDate"].replace("Z", "+00:00")) - NOW).total_seconds() / 3600
            sl = it.get("seller") or {}
            rows[iid] = dict(gen=pr[0], form=pr[1], gb=pr[2], price=price + ship, auction=opts == "AUCTION", used=cond.startswith("2750"),
                             bids=it.get("bidCount") or 0, end=end, fb=sl.get("feedbackScore") or 0, pct=sl.get("feedbackPercentage"),
                             title=it["title"][:90], url=it.get("itemWebUrl"), seller=sl.get("username"))
print("лотів із розпізнаною ємністю:", len(rows), " викликів:", f.calls)

# референс €/ГБ по групах — за ФІКС-ціною, найдешевші 5 різних продавців (швидка ціна)
groups = defaultdict(list)
for r in rows.values():
    if not r["auction"]:
        groups[(r["gen"], r["form"], r["used"])].append(r)
ref = {}
print(f"\n{'група':28}{'лотів':>6}{'медіана €/ГБ':>14}{'швидка €/ГБ':>13}")
for k, v in sorted(groups.items()):
    ppg = sorted(x["price"] / x["gb"] for x in v)
    if len(ppg) >= 5:
        ref[k] = (statistics.median(ppg[:5]), statistics.median(ppg))
        print(f"{k[0]+' '+k[1]+(' вжив.' if k[2] else ' нове'):28}{len(v):>6}{ref[k][1]:>14.2f}{ref[k][0]:>13.2f}")

# викиди: €/ГБ значно нижче швидкої ціни групи «нове» того ж типу (продаж по новій ціні)
out = []
for r in rows.values():
    kn = (r["gen"], r["form"], False)
    if kn not in ref:
        continue
    fast = ref[kn][0]
    ppg = r["price"] / r["gb"]
    if r["fb"] >= 20 and ppg <= 0.65 * fast:
        out.append((ppg / fast, r))
print("\nЛОТИ з €/ГБ ≤ 65% швидкої ціни НОВОГО того ж типу (перевір вручну: справжній товар, стан, продавець):")
for ratio, r in sorted(out, key=lambda x: x[0])[:30]:
    kind = f"АУКЦІОН {r['bids']} ставок, за {r['end']:.1f} год" if r["auction"] else "фікс"
    print(f"  {ratio * 100:>4.0f}%  €{r['price']:>6.1f} {r['gen']} {r['form']} {r['gb']}ГБ  {'вжив.' if r['used'] else 'нове'}  {kind:34} fb={r['fb']} {r['title']}")
print("\nЗнайдено:", len(out), "із", len(rows))
