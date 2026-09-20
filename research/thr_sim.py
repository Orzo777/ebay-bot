"""Офлайн: скільки «знахідок» дає різний поріг знижки (ANOMALY_THRESHOLD) на збереженому знімку.
Без API. Використовує quality.assess з підміною cfg."""
import json, sys, types
from collections import defaultdict
from datetime import datetime, timezone, timedelta
sys.path.insert(0, r"D:\ebay-bot")
import config, identity, quality
from main import total_price

SP = sys.argv[1]
corpus = json.load(open(SP + "/corpus_de.json", encoding="utf-8"))


def created(it):
    try:
        return datetime.fromisoformat(it["itemCreationDate"].replace("Z", "+00:00"))
    except Exception:
        return None


allc = [created(it) for q in corpus for it in corpus[q]["items"] if created(it)]
snap = max(allc)
print("знімок ≈", snap.isoformat()[:16], " (вік рахуємо від нього)")

def cfg_with(thr, min_saving=25, cluster_max=2):
    d = {k: getattr(config, k) for k in dir(config) if k.isupper()}
    d["ANOMALY_THRESHOLD"] = thr
    d["QUALITY_MIN_SAVING"] = min_saving
    d["QUALITY_CLUSTER_MAX"] = cluster_max
    return types.SimpleNamespace(**d)

# базовий пул за 18 категоріями (усі, для більшої вибірки) — ключ + ціни
pools = []
for cat in config.CATEGORIES + [{"query": q, "min_price": 0} for q in ()]:
    pass
import subprocess
# використовуємо ВСІ 18 старих запитів із корпусу; профілі лишились у identity.PROFILES
for q, blob in corpus.items():
    cat = {"query": q, "min_price": next((c["min_price"] for c in config.CATEGORIES if c["query"] == q), None)}
    if cat["min_price"] is None:
        # повернути min_price зі старого списку (з identity/коміту не потрібні — беремо 60 як консервативний)
        cat["min_price"] = 60
    pool = []
    for it in blob["items"]:
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
    pools.append((q, by))

print(f"{'поріг':>6} {'saving':>6} | {'уся база: ok':>12} {'вік≤7д':>7} {'вік≤30д':>8} | категорії з ≥1 (вік≤30д)")
for thr in (0.60, 0.70, 0.75, 0.80, 0.85, 0.90):
    for ms in (25, 15):
        cfg = cfg_with(thr, ms)
        ok = f7 = f30 = 0
        cats = defaultdict(int)
        for q, by in pools:
            for k, rows in by.items():
                for (_k, tp, s, it, r) in rows:
                    comps = [(p, ss, i["itemId"]) for (_kk, p, ss, i, _r) in rows if i["itemId"] != it["itemId"]]
                    v = quality.assess(tp, comps, s, cfg=cfg)
                    if v.ok and r.spec_ok:
                        ok += 1
                        c = created(it)
                        age = (snap - c).days if c else 9999
                        if age <= 7:
                            f7 += 1
                        if age <= 30:
                            f30 += 1
                            cats[q] += 1
        print(f"{thr:>6.2f} {ms:>6} | {ok:>12} {f7:>7} {f30:>8} | {dict(cats) if len(cats) < 6 else len(cats)}")
