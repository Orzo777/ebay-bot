"""Різниця цін між eBay-маркетплейсами (EUR): DE vs FR/IT/ES/NL/IE для ОДНАКОВИХ товарів.
READ-ONLY. Пересилка рахується ДО Німеччини (deliveryCountry:DE + contextualLocation DE).
Обмеження ~350 викликів, лог у файл."""
import sys, json, re, time, statistics
sys.path.insert(0, r"D:\ebay-bot")
import config
from main import EbayClient, _request_with_backoff, _to_float
SP = sys.argv[1]
c = EbayClient()
MK = ["EBAY_DE", "EBAY_FR", "EBAY_IT", "EBAY_ES", "EBAY_NL", "EBAY_IE"]

part = json.load(open(SP + "/lego_census_partial.json", encoding="utf-8"))
found = part["found"]
lego = [s for s, n in sorted(found.items(), key=lambda x: -x[1]) if int(s[:2]) not in (40, 30)][:36]
TECH = [  # (запит, must-слова, мін.ціна)
    ("Nintendo Switch OLED Konsole", ["oled"], 180),
    ("PlayStation 5 Slim Digital Edition", ["ps5", "playstation"], 250),
    ("Sony DualSense Wireless Controller", ["dualsense"], 40),
    ("Sony WH-1000XM5", ["xm5"], 150),
    ("Sony WH-1000XM4", ["xm4"], 110),
    ("Kindle Paperwhite 16 GB", ["paperwhite"], 80),
    ("Garmin Forerunner 265", ["265"], 200),
    ("Steam Deck OLED", ["steam"], 350),
    ("Raspberry Pi 5 8GB", ["raspberry"], 60),
    ("Bose QuietComfort Ultra", ["quietcomfort"], 180),
    ("Apple Watch SE 2", ["watch"], 150),
    ("Logitech MX Master 3S", ["master"], 50),
    ("Elgato Stream Deck MK.2", ["stream"], 100),
    ("Meta Quest 3S", ["quest"], 200),
    ("GoPro HERO13 Black", ["hero"], 250),
    ("DJI Mini 4 Pro", ["mini"], 500),
    ("Nintendo Switch Lite", ["lite"], 100),
    ("JBL Charge 5", ["charge"], 80),
    ("Anker Soundcore Liberty 4 Pro", ["liberty"], 60),
    ("Samsung Galaxy Buds3 Pro", ["buds"], 90),
]


def hdr(mk):
    h = c._headers()
    h["X-EBAY-C-MARKETPLACE-ID"] = mk
    h["X-EBAY-C-ENDUSERCTX"] = "contextualLocation=country=DE,zip=10115"
    return h


calls = 0


def search(q, mk, mp):
    global calls
    p = {"q": q, "filter": config.search_filter(mp) + ",deliveryCountry:DE", "limit": 50, "sort": "price"}
    calls += 1
    for a in range(3):
        try:
            d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=hdr(mk), params=p)
            return d.get("itemSummaries") or [], int(d.get("total") or 0)
        except Exception as e:
            time.sleep(2)
    return [], 0


def tp(it):
    pr = _to_float((it.get("price") or {}).get("value"))
    if pr is None or (it.get("price") or {}).get("currency") != "EUR":
        return None
    sh = 0.0
    so = it.get("shippingOptions") or []
    if so:
        v = _to_float((so[0].get("shippingCost") or {}).get("value"))
        if v is not None:
            sh = v
        else:
            return None
    else:
        return None      # невідома пересилка до DE => не рахуємо
    return round(pr + sh, 2)


def stats(rows):
    v = sorted(x[0] for x in rows)
    return v


items = [("LEGO " + s, [s], 15, "LEGO", s) for s in lego] + [(q, m, mp, "TECH", q) for q, m, mp in TECH]
out = []
for q, must, mp, kind, name in items:
    row = {"kind": kind, "name": name, "mk": {}}
    for mk in MK:
        its, total = search(q, mk, mp)
        good = []
        for it in its:
            t = it["title"].lower()
            if not all(w.lower() in t for w in must):
                continue
            if any(bad in t for bad in ("kompatibel", "compatible", "hülle", "huelle", "case", "cover", "ersatz", "defekt", "lot ", "2x", "3x", "bundle")):
                continue
            v = tp(it)
            if v is None:
                continue
            sl = (it.get("seller") or {})
            good.append((v, sl.get("feedbackScore") or 0, float(sl.get("feedbackPercentage") or 0), it["itemId"], it["title"][:70],
                         (it.get("itemLocation") or {}).get("country")))
        good.sort()
        row["mk"][mk] = dict(total=total, n=len(good), cheapest=good[:5])
        time.sleep(0.12)
    out.append(row)
    print(len(out), "/", len(items), name, "calls", calls, flush=True)
    json.dump(out, open(SP + "/xmarket.json", "w", encoding="utf-8"), ensure_ascii=False)
print("DONE", calls, flush=True)
