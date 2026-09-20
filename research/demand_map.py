"""Карта попиту (READ-ONLY): у категорії eBay беремо найкращі за релевантністю лоти нового товару,
читаємо лічильники продажів (getItem.estimatedSoldQuantity) і рахуємо, ЩО РЕАЛЬНО продається.
Демонстрація методу на кількох категоріях; ~25 викликів на категорію.
Запуск: python research/demand_map.py "запит-для-визначення-категорії" [...]"""
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
import config
import check

NOW = datetime.now(timezone.utc)
f = check.Fetcher()


def sweep(seed_query: str, n_items=25, min_price=25):
    its, _ = f.search(q=seed_query, min_price=min_price)
    cats = its[0].get("categories") or [] if its else []
    leaf = (its[0].get("leafCategoryIds") or [None])[0] if its else None
    if not leaf:
        return None, []
    d = f._get(config.EBAY_BROWSE_SEARCH_URL,
                {"category_ids": leaf, "filter": config.search_filter(min_price) + ",itemLocationCountry:DE",
                                            "limit": 100})
    listings = d.get("itemSummaries") or []
    rows = []
    seen_sellers = {}
    for it in listings:
        s = (it.get("seller") or {}).get("username")
        if seen_sellers.get(s, 0) >= 2:
            continue
        seen_sellers[s] = seen_sellers.get(s, 0) + 1
        if len(rows) >= n_items:
            break
        det = f.item(it["itemId"])
        ea = (det.get("estimatedAvailabilities") or [{}])[0]
        try:
            age = (NOW - datetime.fromisoformat(det["itemCreationDate"].replace("Z", "+00:00"))).total_seconds() / 86400
        except Exception:
            age = None
        rows.append(dict(title=it["title"][:70], price=check._total_price(it), sold=ea.get("estimatedSoldQuantity") or 0,
                         avail=ea.get("estimatedAvailableQuantity"), age=age, seller=s,
                         gtin=det.get("gtin"), mpn=det.get("mpn"), brand=det.get("brand")))
    cat_name = " > ".join(c["categoryName"] for c in cats[:2])
    return (leaf, cat_name), rows


for q in sys.argv[1:]:
    meta, rows = sweep(q)
    if not meta:
        print(q, "— категорію не визначено"); continue
    print(f"\n=== {meta[1]} (id {meta[0]}) — за запитом «{q}» ===")
    good = [r for r in rows if r["age"] and r["age"] >= 14 and r["sold"] >= 3]
    for r in sorted(good, key=lambda r: -(r["sold"] / r["age"] * 7))[:8]:
        print(f"  {r['sold'] / r['age'] * 7:>5.1f} шт/тиж  €{r['price']:>6.0f}  (продано {r['sold']}, {r['age']:.0f} д)  "
              f"gtin={r['gtin']} mpn={r['mpn']}  {r['title']}")
    print(f"  лотів із даними: {len(good)} з {len(rows)}; викликів: {f.calls}")
