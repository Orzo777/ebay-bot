"""Швидкість продажів за даними eBay: getItem -> estimatedSoldQuantity (для лотів із кількістю >1).
READ-ONLY, ~260 викликів."""
import sys, json, time
from datetime import datetime, timezone
sys.path.insert(0, r"D:\ebay-bot")
import config
from main import EbayClient, _request_with_backoff, total_price
SP = sys.argv[1]
c = EbayClient()
NOW = datetime.now(timezone.utc)
SKUS = [  # (назва, запит, мін.ціна)
    ("LEGO Icons Botanicals", "LEGO Botanicals", 15),
    ("LEGO Speed Champions", "LEGO Speed Champions", 15),
    ("LEGO Harry Potter", "LEGO Harry Potter", 15),
    ("LEGO Icons", "LEGO Icons", 30),
    ("Pokemon Top-Trainer-Box DE", "Pokemon Top Trainer Box Deutsch", 35),
    ("Pokemon 3-Pack Blister DE", "Pokemon 3er Booster Blister Deutsch", 12),
    ("Pokemon Booster Bundle DE", "Pokemon Booster Bundle Deutsch", 20),
    ("Pokemon Tin DE", "Pokemon Tin Box Deutsch", 15),
    ("Mario Kart 8 Deluxe", "Mario Kart 8 Deluxe Switch", 25),
    ("EA Sports FC 26 PS5", "EA Sports FC 26 PS5", 20),
    ("Tonies Figur", "Tonies Figur Hoerfigur", 10),
    ("Toniebox 2", "Toniebox 2 Starterset", 50),
    ("Oral-B iO Series", "Oral-B iO Zahnbuerste", 40),
    ("Philips Sonicare", "Philips Sonicare DiamondClean", 40),
    ("Braun Series 9", "Braun Series 9 Rasierer", 70),
    ("Nespresso Vertuo", "Nespresso Vertuo Kapseln", 10),
    ("Ravensburger Puzzle 1000", "Ravensburger Puzzle 1000 Teile", 10),
    ("Playmobil", "Playmobil Set", 20),
    ("Panini Sticker/Karten", "Panini Adrenalyn XL Display", 20),
    ("Apple AirPods (контроль)", "Apple AirPods Pro 2", 100),
    ("Sony PS5 DualSense", "Sony DualSense Controller", 40),
    ("Bosch Professional Akku", "Bosch Professional GBA 18V Akku", 30),
    ("Kaffee Lavazza/Jura", "Lavazza Kaffeebohnen 1kg", 8),
    ("Vitamine Doppelherz", "Doppelherz Nahrungsergaenzung", 8),
]
calls = 0


def get(url, params=None):
    global calls
    calls += 1
    for a in range(3):
        try:
            return _request_with_backoff("GET", url, headers=c._headers(), params=params or {})
        except Exception:
            time.sleep(2)
    return {}


out = []
for name, q, mp in SKUS:
    d = get(config.EBAY_BROWSE_SEARCH_URL, {"q": q, "filter": config.search_filter(mp) + ",itemLocationCountry:DE", "limit": 14})
    its = d.get("itemSummaries") or []
    total = int(d.get("total") or 0)
    rows = []
    for it in its[:12]:
        det = get("https://api.ebay.com/buy/browse/v1/item/" + it["itemId"])
        ea = (det.get("estimatedAvailabilities") or [{}])[0]
        try:
            cr = datetime.fromisoformat(det.get("itemCreationDate", "").replace("Z", "+00:00"))
            age = max(1.0, (NOW - cr).total_seconds() / 86400)
        except Exception:
            age = None
        rows.append(dict(t=it["title"][:50], price=total_price(it), sold=ea.get("estimatedSoldQuantity"),
                         avail=ea.get("estimatedAvailableQuantity"), age=age,
                         seller=(it.get("seller") or {}).get("username")))
        time.sleep(0.05)
    multi = [r for r in rows if (r["avail"] or 0) > 1 or (r["sold"] or 0) > 0]
    per_week = sum((r["sold"] or 0) / r["age"] * 7 for r in multi if r["age"])
    out.append(dict(name=name, total=total, n=len(rows), multi=len(multi), sold_total=sum((r["sold"] or 0) for r in multi),
                    per_week=per_week, rows=rows))
    print(f"{name:28} DE={total:>6} лотів_зразок={len(rows):>2} з_запасом/продажами={len(multi):>2} "
          f"продано_разом={sum((r['sold'] or 0) for r in multi):>5} ~шт/тиж(12 лотів)={per_week:>6.1f} calls={calls}", flush=True)
    json.dump(out, open(SP + "/velocity.json", "w", encoding="utf-8"), ensure_ascii=False)
print("DONE", calls, flush=True)
