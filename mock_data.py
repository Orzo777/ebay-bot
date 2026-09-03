"""Фікстурні (mock) відповіді Browse API для dry-run / --simulate-days.

mock_search(query, now, day, total_days, sort, min_price) імітує ПОДВІЙНИЙ запит:
  * sort=None (best_match)  — репрезентативна вибірка ≥ min_price;
  * sort="price"            — найдешевший зріз + аксесуарний шум НИЖЧЕ порогу
                              (його потім відсіє per-category min_price у пайплайні).

Кожна категорія в _CATALOG має:
  profile   — форма життєвого циклу лістингів (fast / slow_glut / relisty / late);
  deal_rate — частка лістингів з аномально низькою ціною (кандидати);
  epid_rate — частка лістингів, що несуть epid (решта → ключ за назвою, lower_confidence);
  b_only    — якщо True, дешевий лот НЕ показується в best_match, лише в price-asc
              (тест: обʼєднання A+B розширює покриття, а не просто дублює).
"""

import hashlib
from datetime import timedelta

import config

CURRENCY = "EUR"

#            epid,        query,                              base,   label,                                  profile,     deal, epid_rate, b_only
_CATALOG = [
    ("71010042", "Dyson Filter Ersatz",                 22.0,  "Dyson HEPA Filter V-Serie",             "fast",      0.30, 0.10, False),
    ("71010043", "Dyson Zubehör Set neu",               45.0,  "Dyson Zubehoer Set",                    "fast",      0.16, 0.30, False),
    ("71010044", "Roomba Bürsten Ersatzteile",          25.0,  "iRobot Roomba Buersten Kit",            "fast",      0.24, 0.10, False),
    ("71010045", "Shark Staubsauger Filter",            20.0,  "Shark Staubsauger HEPA Filter",         "fast",      0.32, 0.10, False),
    ("72020051", "Ninja Blender neu",                    95.0,  "Ninja Standmixer Blender BN800",        "fast",      0.22, 0.55, False),
    ("18045512", "Vorwerk Thermomix TM6 Zubehör",        46.0,  "Vorwerk Thermomix TM6 Varoma",         "fast",      0.14, 0.55, False),
    ("23088190", "Kärcher Fenstersauger",                55.0,  "Kaercher WV5 Fenstersauger",           "late",      0.30, 0.55, False),
    ("73030061", "Gardena Gartengeräte Set neu",        130.0,  "Gardena Gartengeraete Set",            "relisty",   0.50, 0.30, False),
    ("74040073", "Widerstandsband Set Fitness",          30.0,  "Widerstandsband Set Bodybuilding",     "fast",      0.35, 0.10, True),
    ("74040074", "Yogamatte Set neu",                    40.0,  "Yogamatte Set Block Gurt",             "fast",      0.22, 0.10, False),
    ("30030003", "Camping Kochgeschirr Set neu",        20.0,  "Camping Kochgeschirr Set Anodisiert",  "slow_glut", 0.24, 0.02, False),
    ("30010001", "Pokemon Booster Box versiegelt",      85.0,  "Pokemon Karmesin Purpur Booster Box",  "relisty",   0.30, 0.21, True),
    ("30010003", "Magic The Gathering Booster Box sealed", 120.0, "Magic Booster Box Play Sammelkarten", "relisty", 0.30, 0.41, False),
    ("30020007", "Pokemon TCG Starter Deck versiegelt", 24.0,  "Pokemon TCG Starter Deck EN",          "fast",      0.22, 0.32, False),
    ("30020006", "Catan Brettspiel neu",               28.0,  "Catan Basisspiel Brettspiel",          "fast",      0.18, 0.59, False),
    ("30060001", "Dune Imperium Brettspiel",           55.0,  "Dune Imperium Brettspiel Uprising",    "fast",      0.22, 0.69, False),
    ("30060002", "Everdell Brettspiel",                62.0,  "Everdell Brettspiel Grundspiel",       "fast",      0.20, 0.57, False),
    ("30060003", "Ark Nova Brettspiel",                64.0,  "Ark Nova Brettspiel",                  "fast",      0.20, 0.38, False),
    ("30060010", "LEGO 10307",                        700.0,  "LEGO 10307 Eiffelturm",               "fast",      0.16, 0.61, False),
    ("30060011", "LEGO 71043 Hogwarts",              440.0,  "LEGO 71043 Hogwarts Schloss",         "fast",      0.16, 0.68, False),
    ("30060012", "LEGO 75313 AT-AT",               1150.0,  "LEGO 75313 AT-AT UCS",                "fast",      0.14, 0.57, False),
    ("30060020", "Pokemon 151 Elite Trainer Box",    90.0,  "Pokemon 151 Elite Trainer Box EN",    "relisty",   0.28, 0.16, True),
    ("30060021", "Disney Lorcana Booster Display",   150.0,  "Disney Lorcana Booster Display",       "relisty",   0.25, 0.34, False),
    ("30060022", "Flesh and Blood Booster Box",      120.0,  "Flesh and Blood Booster Box",          "relisty",   0.28, 0.27, False),
    ("30060030", "Xenoblade Chronicles Definitive Edition Switch", 68.0, "Xenoblade Chronicles Definitive Switch", "fast", 0.22, 0.22, False),
    ("30060031", "Fire Emblem Three Houses Switch",  100.0,  "Fire Emblem Three Houses Switch",      "fast",      0.20, 0.14, False),
    ("30060032", "Metroid Prime Remastered Switch",   45.0,  "Metroid Prime Remastered Switch",      "fast",      0.20, 0.57, False),
    ("30060040", "Warhammer 40k Leviathan",          230.0,  "Warhammer 40k Leviathan Box",          "relisty",   0.24, 0.12, False),
    ("2260847",  "LEGO 75192",                         755.0,  "LEGO Star Wars 75192 Millennium Falcon", "fast",    0.12, 0.55, False),
    ("26056401", "iPhone 15",                          720.0,  "Apple iPhone 15 128GB",                "fast",      0.06, 0.55, False),
    ("26057402", "iPhone 15",                          830.0,  "Apple iPhone 15 256GB",                "fast",      0.05, 0.55, False),
    ("31500999", "iPhone 15",                          520.0,  "Google Pixel 8 Pro 256GB",             "fast",      0.04, 0.55, False),
]

_specs_cache: dict = {}


def _rng(*parts) -> float:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:8], 16) / 0xFFFFFFFF


def _price(epid, tag, i, base, deal_rate):
    if _rng(epid, tag, i, "deal") < deal_rate:
        return round(base * (0.42 + 0.15 * _rng(epid, tag, i, "dp")), 2)   # аномалія
    return round(base * (0.93 + 0.14 * _rng(epid, tag, i, "np")), 2)       # норма


def _specs(epid, base, profile, deal_rate, total_days):
    key = (epid, profile, deal_rate, total_days)
    if key in _specs_cache:
        return _specs_cache[key]
    out = []

    if profile == "fast":
        npool = 8
        s = i = 0
        while s < total_days:
            life = max(3, 6 + int(_rng(epid, "F", i, "l") * 5) - 2)
            out.append(dict(id=f"v1|{epid}-F{i}|0", start=s, life=life,
                            price=_price(epid, "F", i, base, deal_rate),
                            seller=f"de_{epid[-3:]}_{i % npool}"))
            s += 2
            i += 1

    elif profile == "slow_glut":
        s = i = 0
        while s < total_days:
            out.append(dict(id=f"v1|{epid}-S{i}|0", start=s, life=40,
                            price=_price(epid, "S", i, base, deal_rate),
                            seller=f"de_{epid[-3:]}_{i % 4}"))
            s += 2
            i += 1

    elif profile == "relisty":
        for c, seller in enumerate((f"de_{epid[-3:]}_a", f"de_{epid[-3:]}_b")):
            chain_deal = _rng(epid, "rc", c) < deal_rate
            s = c * 4
            i = 0
            while s < total_days:
                life = max(4, 8 + int(_rng(epid, "R", c, i, "l") * 4) - 2)
                if chain_deal:
                    price = round(base * (0.44 + 0.08 * _rng(epid, "R", c, i, "dp")), 2)
                else:
                    price = round(base * (0.90 + 0.03 * _rng(epid, "R", c, i, "np")), 2)
                out.append(dict(id=f"v1|{epid}-R{c}{i}|0", start=s, life=life,
                                price=price, seller=seller))
                s += life + 2
                i += 1

    elif profile == "late":
        appear = max(0, total_days - 6)
        s = i = 0
        while appear + s < total_days:
            out.append(dict(id=f"v1|{epid}-T{i}|0", start=appear + s, life=10,
                            price=_price(epid, "T", i, base, deal_rate),
                            seller=f"de_{epid[-3:]}_{i % 2}"))
            s += 1
            i += 1

    _specs_cache[key] = out
    return out


def _mk(epid, label, price, item_id, seller, created_iso, *,
        cond="Neu", cond_id="1000", seller_score=None, seller_pct=None,
        shipping=0.0, title=None) -> dict:
    if seller_score is None:
        seller_score = 200 + int(_rng(item_id, "s") * 700)
    if seller_pct is None:
        seller_pct = round(98.0 + _rng(item_id, "p") * 2.0, 1)
    slug = int(hashlib.sha1(item_id.encode()).hexdigest()[:12], 16)
    return {
        "itemId": item_id,
        "epid": epid,                       # None → пайплайн будує ключ за назвою
        "title": title or f"{label} neu OVP",
        "condition": cond,
        "conditionId": cond_id,
        "price": {"value": f"{price:.2f}", "currency": CURRENCY},
        "shippingOptions": [{
            "shippingCostType": "FIXED",
            "shippingCost": {"value": f"{shipping:.2f}", "currency": CURRENCY},
        }],
        "seller": {
            "username": seller,
            "feedbackScore": int(seller_score),
            "feedbackPercentage": f"{float(seller_pct):.1f}",
        },
        "itemCreationDate": f"{created_iso}T10:00:00.000Z",
        "itemWebUrl": f"https://www.ebay.de/itm/{slug % 10**12}",
        "buyingOptions": ["FIXED_PRICE"],
    }


def mock_search(query, now, day=0, total_days=1, sort=None, min_price=0.0):
    is_last = day == total_days - 1
    today = now.date().isoformat()
    rows: list[dict] = []

    for epid, q, base, label, profile, deal_rate, epid_rate, b_only in _CATALOG:
        if q != query:
            continue
        for sp in _specs(epid, base, profile, deal_rate, total_days):
            if not (sp["start"] <= day < sp["start"] + sp["life"]):
                continue
            is_deal = sp["price"] < base * 0.65
            if b_only and is_deal and sort != "price":
                continue                     # best_match не показує цей дешевий лот
            has_epid = _rng(sp["id"], "e") < epid_rate
            created = (now - timedelta(days=day - sp["start"])).date().isoformat()
            rows.append(_mk(epid if has_epid else None, label, sp["price"],
                            sp["id"], sp["seller"], created))

        # спец-інжекції останнього дня — лише через best_match (щоб не дублювати)
        if is_last and sort != "price":
            if epid == "2260847":
                rows.append(_mk("2260847", label, round(base * 0.30, 2),
                                "v1|2260847-BL|0", "de_847_x", today,
                                seller_score=300, seller_pct=99.0,
                                title="LEGO 75192 Millennium Falcon Bastler Teile fehlen"))
            if epid == "30010003":     # MTG box від продавця з рейтингом 2/78%
                rows.append(_mk(None, label, round(base * 0.42, 2),
                                "v1|30010003-SCAM|0", "de_mtg_new", today,
                                seller_score=2, seller_pct=78.0))

    if query == "iPhone 15" and sort != "price":
        p = round(600 * (0.80 + 0.5 * _rng("noepid", day)), 2)
        rows.append(_mk(None, "Apple iPhone 15", p, f"v1|noepid-d{day}|0",
                        "de_noepid", today,
                        title="iPhone 15 Beschreibung lesen Zustand siehe Fotos"))

    if sort == "price":
        # аксесуарний шум нижче будь-якого порогу — має відсіятись min_price
        for j in range(2):
            jp = round(1 + 2 * _rng(query, day, j, "junk"), 2)
            rows.append(_mk(None, query, jp,
                            f"v1|junk-{int(_rng(query, day, j) * 1e8)}|0",
                            "de_junk", today,
                            title=f"{query} Ersatz Kleinteil Zubehoer"))
        rows.sort(key=lambda r: float(r["price"]["value"]))
        return rows[: config.SEARCH_LIMIT]

    rows = [r for r in rows if float(r["price"]["value"]) >= min_price * 0.9]
    return rows[: config.SEARCH_LIMIT]
