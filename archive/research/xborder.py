"""Міжкраїнний арбітраж: eBay US / GB → Німеччина. Landed cost = (ціна + доставка ДО DE) × курс × 1.19 (ПДВ на імпорті;
мито для іграшок/техніки ≈0, тому не враховане). Порівняння з «швидкою» ціною нового товару на eBay.de.
READ-ONLY, ~4 виклики на товар. Запуск: python research/xborder.py"""
import sys

import requests

sys.path.insert(0, ".")
import config
import check
import identity
from main import _request_with_backoff, _to_float

f = check.Fetcher()
VAT = 1.19


_FALLBACK = {"USD": 0.87032, "GBP": 1.1658}      # курс ECB, знятий 21.09.2026 (frankfurter.app), якщо API недоступне


def fx(cur):
    try:
        r = requests.get(f"https://api.frankfurter.app/latest?from={cur}&to=EUR", timeout=20)
        return r.json()["rates"]["EUR"]
    except Exception:
        return _FALLBACK[cur]


RATE = {"USD": fx("USD"), "GBP": fx("GBP")}
print("курси:", RATE)

# (назва, запит для DE, must-групи DE, запит для зарубіжного ринку, must-групи, exclude)
SKUS = [
    ("Pokémon ETB English", "Pokemon Elite Trainer Box Englisch", [["pokemon"], ["elite", "etb"], ["englisch", "english"]],
     "Pokemon Elite Trainer Box", [["pokemon"], ["elite"], ["trainer"]], ["japanese", "korean", "lot", "case", "bundle", "german", "deutsch", "custom", "proxy", "empty", "pin", "promo"]),
    ("Pokémon Booster Bundle English", "Pokemon Booster Bundle Englisch", [["pokemon"], ["bundle"], ["englisch", "english"]],
     "Pokemon Booster Bundle 6 pack", [["pokemon"], ["bundle"]], ["japanese", "lot", "case", "german", "custom"]),
    ("Pokémon Booster Box English", "Pokemon Booster Box 36 Englisch", [["pokemon"], ["display", "box"], ["englisch", "english"]],
     "Pokemon Booster Box 36 packs", [["pokemon"], ["booster"], ["box"]], ["japanese", "lot", "case", "german", "custom", "korean", "elite", "bundle", "half"]),
    ("Magic Play Booster Box", "Magic the Gathering Play Booster Box", [["magic"], ["booster"], ["box", "display"]],
     "Magic the Gathering Play Booster Box", [["magic"], ["booster"], ["box"]], ["lot", "case", "japanese", "collector", "bundle", "custom"]),
    ("LEGO 10331", "LEGO 10331", [["10331"]], "LEGO 10331", [["10331"]], ["moc", "light", "led"]),
    ("LEGO 10318 Concorde", "LEGO 10318", [["10318"]], "LEGO 10318", [["10318"]], ["moc", "light", "led"]),
    ("LEGO 75192 Falcon", "LEGO 75192", [["75192"]], "LEGO 75192", [["75192"]], ["moc", "light", "led", "ucs 7"]),
    ("LEGO 21348 D&D", "LEGO 21348", [["21348"]], "LEGO 21348", [["21348"]], ["moc", "light", "led"]),
    ("Nintendo Switch OLED", "Nintendo Switch OLED Konsole", [["switch"], ["oled"]], "Nintendo Switch OLED console",
     [["switch"], ["oled"]], ["game", "case", "dock", "used", "joy", "cover", "pro controller"]),
    ("Sony DualSense", "Sony DualSense Wireless Controller", [["dualsense"]], "Sony DualSense Wireless Controller PS5",
     [["dualsense"]], ["edge", "charging", "station", "case", "skin"]),
    ("Sony WH-1000XM5", "Sony WH-1000XM5", [["xm5"]], "Sony WH-1000XM5", [["xm5"]], ["case", "pads", "refurb"]),
    ("GoPro HERO13 Black", "GoPro HERO13 Black", [["gopro"], ["hero13", "13"]], "GoPro HERO13 Black", [["gopro"], ["hero13", "13"]], ["case", "mount", "used"]),
    ("Raspberry Pi 5 8GB", "Raspberry Pi 5 8GB", [["raspberry"], ["5"], ["8gb", "8"]], "Raspberry Pi 5 8GB", [["raspberry"], ["5"], ["8gb", "8"]], ["case", "kit", "cooler"]),
    ("Kindle Paperwhite", "Kindle Paperwhite", [["kindle"], ["paperwhite"]], "Kindle Paperwhite", [["kindle"], ["paperwhite"]], ["case", "cover", "used"]),
    ("Steam Deck OLED 512GB", "Steam Deck OLED 512GB", [["steam"], ["deck"], ["oled"]], "Steam Deck OLED 512GB", [["steam"], ["deck"], ["oled"]], ["case", "dock"]),
    ("Nintendo Mario Kart 8 Deluxe", "Mario Kart 8 Deluxe Switch", [["mario"], ["kart"], ["deluxe"]], "Mario Kart 8 Deluxe Switch", [["mario"], ["kart"], ["deluxe"]], ["used", "digital"]),
]


JUNK = ['part', 'parts', 'replacement', 'lcd', 'screen', 'minifigure', 'minifig', 'sticker', 'card', 'sleeve', 'charging', 'port', 'adapter',
        'supply', 'connector', 'cable', 'strap', 'lens', 'mount', 'holder', 'keychain', 'miniature', 'shell', 'button', 'pad', 'pads',
        'headband', 'skin', 'guide', 'manual', 'poster', 'sign', 'plate', 'wall', 'display case', 'stand']


def prod(q, must, excl):
    return check.Product(query=q, must=[{identity.norm(w) for w in g} for g in must],
                         exclude=[identity.norm(w) for w in excl], label=q, source="назва")


def foreign(mk, cur, q, must, excl, floor=0.0):
    hdr = f.client._headers()
    hdr["X-EBAY-C-MARKETPLACE-ID"] = mk
    hdr["X-EBAY-C-ENDUSERCTX"] = "contextualLocation=country=DE,zip=10115"
    p = {"q": q, "limit": 100, "sort": "price",
         "filter": f"buyingOptions:{{FIXED_PRICE}},conditionIds:{{1000}},deliveryCountry:DE,priceCurrency:{cur}"}
    f.calls += 1
    d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=hdr, params=p)
    pr = prod(q, must, excl)
    out = []
    for it in d.get("itemSummaries") or []:
        if check.match_reasons(it["title"], pr):
            continue
        price = _to_float((it.get("price") or {}).get("value"))
        if price is None or (it.get("price") or {}).get("currency") != cur:
            continue
        so = (it.get("shippingOptions") or [{}])[0]
        ship = _to_float((so.get("shippingCost") or {}).get("value"))
        if ship is None:
            continue                                       # доставки до DE невідомо — не рахуємо
        sl = it.get("seller") or {}
        if (sl.get("feedbackScore") or 0) < 50 or float(sl.get("feedbackPercentage") or 0) < 98:
            continue
        landed = (price + ship) * RATE[cur] * VAT
        if landed < floor:
            continue                                       # запчастина/аксесуар/картка, а не товар
        out.append(((price + ship) * RATE[cur] * VAT, price, ship, it["title"][:60], (it.get("itemLocation") or {}).get("country")))
    out.sort()
    return out, int(d.get("total") or 0)


print(f"\n{'товар':30}{'DE швидка':>10}{'DE n':>5} | {'US мін landed':>14}{'GB мін landed':>14} | найкраще / чистий (комісія eBay DE, пересилка, 3% повернень)")
for name, qde, mde, qf, mf, excl in SKUS:
    try:
        r = check.run_check(f, prod(qde, mde, excl), velocity_sample=0)
    except Exception as e:
        print(name, "DE помилка", e); continue
    fast = r.market.low5
    if fast is None or r.market.n_kept < 4:
        print(f"{name:30} DE-ринок нечитабельний (n={r.market.n_kept})"); continue
    fee = r.fee.rate
    cells, best = [], None
    for mk, cur in (("EBAY_US", "USD"), ("EBAY_GB", "GBP")):
        try:
            lst, tot = foreign(mk, cur, qf, mf, excl + JUNK, floor=0.45 * fast)
        except Exception as e:
            cells.append("err"); continue
        if lst:
            cells.append(f"€{lst[0][0]:.0f} (n={len(lst)})")
            if best is None or lst[0][0] < best[0]:
                best = (lst[0][0], mk, lst[0])
        else:
            cells.append("—")
    line = f"{name:30}{fast:>9.0f} {r.market.n_kept:>4} | {cells[0]:>14}{cells[1]:>14} |"
    if best:
        ship_out = 6.19
        net = fast - fee * fast - 0.45 - ship_out - best[0] - 0.03 * fast
        line += f" {best[1][5:]} €{best[0]:.0f} → чистий {net:+.0f} ({net / best[0] * 100:+.0f}%)  {best[2][3]}"
    print(line)
print("\nВикликів:", f.calls)
