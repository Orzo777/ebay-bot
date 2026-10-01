"""Оцінка потоку угод для ПОТОЧНИХ 18 категорій: скільки нових лотів/тиждень і скільки
з них були б знахідкою (лише якість, без ліквідності). Використовує знімок DE+AT."""
import json, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
sys.path.insert(0, r"D:\ebay-bot")
import config, identity, quality
from main import total_price

corpus = json.load(open(sys.argv[1] + "/corpus_de.json", encoding="utf-8"))
now = datetime.now(timezone.utc)

def created(it):
    try:
        return datetime.fromisoformat(it["itemCreationDate"].replace("Z", "+00:00"))
    except Exception:
        return None

tot_new7 = tot_new7_ev = 0
deals = []
print(f"{'категорія':<46}{'нових/7д':>9}{'з еталоном':>11}{'знахідок стоїть':>16}")
for cat in config.CATEGORIES:
    q = cat["query"]
    pool = []
    for it in corpus[q]["items"]:
        if quality.listing_flags(it):
            continue
        r = identity.describe(it["title"], cat)
        tp = total_price(it)
        if r.exclude or tp is None or tp < cat["min_price"]:
            continue
        pool.append((r.key, tp, (it.get("seller") or {}).get("username") or "?", it, r))
    by = defaultdict(list)
    for row in pool:
        by[row[0]].append(row)
    new7 = new7_ev = 0
    standing = 0
    for k, rows in by.items():
        for (_k, tp, s, it, r) in rows:
            c = created(it)
            is_new = c is not None and (now - c) <= timedelta(days=7)
            comps = [(p, ss, i["itemId"]) for (_kk, p, ss, i, _r) in rows if i["itemId"] != it["itemId"]]
            v = quality.assess(tp, comps, s)
            ev = "thin-ref" not in v.reasons
            new7 += is_new
            new7_ev += is_new and ev
            if v.ok and r.spec_ok:
                standing += 1
                deals.append((q, tp, v, it, c))
    tot_new7 += new7; tot_new7_ev += new7_ev
    print(f"{q[:45]:<46}{new7:>9}{new7_ev:>11}{standing:>16}")
print(f"{'РАЗОМ':<46}{tot_new7:>9}{tot_new7_ev:>11}{len(deals):>16}")
print("\nВік «знахідок, що зараз стоять» (коли створено лот):")
for q, tp, v, it, c in deals:
    age = (now - c).days if c else None
    print(f"  {q[:30]:<30} {tp:>7.2f} vs {v.median:>7.2f}  створено {age} дн. тому  {it['title'][:60]}")
