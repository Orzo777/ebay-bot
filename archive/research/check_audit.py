"""Приймальна перевірка check.py на реальних SKU (READ-ONLY, ~7 викликів на SKU).

Запуск: python research/check_audit.py <папка-для-виводу>
Друкує таблицю «SKU → ринок → швидкість → комісія → вердикт» і складає JSON,
щоб можна було вручну звірити 10 позицій із eBay у браузері.
"""
import json
import sys
import time

sys.path.insert(0, r"D:\ebay-bot")
import check

OUT = sys.argv[1] if len(sys.argv) > 1 else "."

# (як звати, kwargs для product_from_args, ціна закупівлі або None)
SKUS = [
    ("LEGO 10354 Auenland",        dict(lego="10354"), 214.00),
    ("LEGO 10316 Bruchtal",        dict(lego="10316"), None),
    ("LEGO 42177 Technic",         dict(lego="42177"), None),
    ("LEGO 75404 Star Wars",       dict(lego="75404"), None),
    ("LEGO 21034 Architecture",    dict(lego="21034"), None),
    ("LEGO 76269 Avengers Tower",  dict(lego="76269"), None),
    ("Pokemon ETB Maskerade",      dict(query="Pokemon Top Trainer Box Maskerade im Zwielicht",
                                        must=["maskerade"]), 39.99),
    ("Pokemon Tin Box DE",         dict(query="Pokemon Tin Box Deutsch"), None),
    ("Oral-B iO 6",                dict(query="Oral-B iO 6 Zahnbuerste", exclude=["kids"]), 69.99),
    ("Philips Sonicare 9000",      dict(query="Philips Sonicare DiamondClean 9000"), None),
    ("Sony DualSense weiss",       dict(query="Sony DualSense Wireless Controller Weiss",
                                        must=["weiss"]), None),
    ("Nespresso Vertuo Next",      dict(query="Nespresso Vertuo Next Maschine"), None),
    ("Tonies Toniebox 2",          dict(query="Toniebox 2 Starterset"), None),
    ("Tonies Hoerfigur Benjamin",  dict(query="Tonies Hoerfigur Benjamin Bluemchen"), None),
    ("Jabra Evolve2 65",           dict(query="Jabra Evolve2 65"), None),
    ("Rode NT1",                   dict(query="Rode NT1"), None),
    ("Ravensburger tiptoi Start",  dict(query="Ravensburger tiptoi Starterset"), None),
    ("Zelda TotK Collector",       dict(query="Zelda Tears of the Kingdom Collector's Edition"), None),
    ("Dyson Airwrap",              dict(query="Dyson Airwrap Complete"), None),
    ("Nintendo Switch 2 Mario",    dict(query="Mario Kart World Switch 2"), None),
]

fetcher = check.Fetcher()
rows = []
print(f"{'SKU':28}{'лотів':>7}{'прод':>6}{'мін':>9}{'швидка':>9}{'медіана':>9}"
      f"{'розк':>7}{'тиж':>8}{'комісія':>9} вердикт")
for name, kw, buy in SKUS:
    try:
        p = check.product_from_args(**kw)
        r = check.run_check(fetcher, p, buy_price=buy, carrier="dhl_paket_2kg")
    except Exception as exc:
        print(f"{name:28} ПОМИЛКА: {exc}")
        rows.append({"sku": name, "error": str(exc)})
        continue
    m, v = r.market, r.velocity
    fee = f"{r.fee.rate:.0%}" + ("" if r.fee.known else "?")
    vw = "—" if v.weeks_to_sell is None else f"{v.weeks_to_sell:.1f}"
    print(f"{name:28}{m.n_kept:>7}{m.n_sellers:>6}"
          f"{(m.minimum or 0):>9.2f}{(m.low5 or 0):>9.2f}{(m.median or 0):>9.2f}"
          f"{(m.dispersion if m.dispersion is not None else 0):>7.2f}{vw:>8}{fee:>9}"
          f" {r.verdict}  {'; '.join(r.reasons)[:60]}", flush=True)
    d = check._as_dict(r, fetcher.calls)
    d["sku"] = name
    d["samples"] = [{"title": t, "price": pr, "seller": s} for t, pr, s in m.samples]
    rows.append(d)
    json.dump(rows, open(OUT + "/check_audit.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    time.sleep(0.2)

print(f"\nВикликів eBay API разом: {fetcher.calls}")
ok = sum(1 for r in rows if r.get("verdict") == "BUY")
sk = sum(1 for r in rows if r.get("verdict") == "SKIP")
un = sum(1 for r in rows if r.get("verdict") == "UNKNOWN")
print(f"BUY={ok} SKIP={sk} UNKNOWN={un} помилок={sum(1 for r in rows if 'error' in r)}")
