import sys, statistics, re
sys.path.insert(0, r"D:\ebay-bot")
import config
from main import EbayClient, _request_with_backoff, total_price
c = EbayClient()


def search(params):
    d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=c._headers(), params=params)
    return d.get("itemSummaries") or [], int(d.get("total") or 0)


# 1) чи працює пошук за GTIN (точна ідентифікація без парсингу назв)
its, tot = search({"gtin": "5702017423623", "limit": 5})
print("GTIN-пошук 5702017423623 (LEGO 40586):", tot, [i["title"][:50] for i in its[:3]])

# 2) Oral-B iO: ціни mydealz (Amazon) проти ринку eBay (нове, DE)
DEALS = [("iO 3", "Oral-B iO Series 3 Zahnbürste", 74.99), ("iO 5", "Oral-B iO 5 Zahnbürste", 79.99),
         ("iO 6", "Oral-B iO 6 Zahnbürste", 69.99)]
FEE_EBAY = 0.14
for name, q, src in DEALS:
    its, tot = search({"q": q, "filter": config.search_filter(45) + ",itemLocationCountry:DE", "limit": 100, "sort": "price"})
    rows = []
    for it in its:
        t = it["title"].lower()
        if "io" not in t.split() and "io" not in t:
            continue
        if name.split()[1] not in t:
            continue
        if any(w in t for w in ("aufsteck", "bürstenkopf", "buerstenkopf", "ersatz", "kids", "2x", "doppel", "duo", "ladestation", "reiseetui", "mehrfachpack")):
            continue
        p = total_price(it)
        if p:
            rows.append(p)
    rows.sort()
    if len(rows) < 3:
        print(name, "мало даних", len(rows))
        continue
    low5 = statistics.median(rows[:5])
    med = statistics.median(rows)
    for label, fee, extra in (("eBay 14%", 0.14, 0.45), ("Kleinanzeigen ~0% (+ €2 оголош.)", 0.0, 2.0)):
        ship = 4.5   # Hermes/GLS ~ €4.4; покупець платить ці ж гроші, тому нейтрально; тут вважаємо як витрату
        sale = low5      # продаємо близько до нижньої п'ятірки (щоб швидко продатись)
        profit = sale - fee * sale - extra - src
        print(f"{name}: закупівля {src:.2f} | eBay: мін {rows[0]:.0f}, ~5 найдешевших {low5:.0f}, медіана {med:.0f} (n={len(rows)}) | "
              f"{label}: чистий {profit:+.1f} € ({profit / src * 100:+.0f}%)")
