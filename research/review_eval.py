"""Перевірка ШІ-читання оголошень (19.10) на відомих випадках — запускається вручну workflow review_eval.yml.

Погані (мають бути ⛔): KA 3543763957 — DDR4 2×32, «Selten kam es zu Abstürzen … Speicherdiagnose Probleme»;
KA 3541673511 — ціна за одну планку; eBay 336845197747 — шахрай (рейтинг −1, «Neu» за третину ціни).
Нормальні (мають бути без ⛔): наші оголошення й кілька свіжих звичайних з eBay. Друкує лише висновки (публічні оголошення).

    python research/review_eval.py
"""
import os
import re
import sys

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]

import requests

import photo_check as pc

BAD = [("ka", "3543763957"), ("ka", "3541673511"), ("ebay", "336845197747")]
GOOD = [("ebay", "336844624233"), ("ebay", "336849011147"), ("ebay", "336849477931"), ("ebay", "336849525826")]
UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "de-DE"}


def ka_case(ad: str):
    import ka_listing_check as kc
    from ram_alert import evaluate
    page = requests.get(f"https://www.kleinanzeigen.de/s-anzeige/{ad}", headers=UA, timeout=20).text
    title = re.search(r'<h1[^>]*id="viewad-title"[^>]*>(.*?)</h1>', page, re.S)
    price = re.search(r'itemprop="price" content="(\d+)', page)
    if not title or not price:
        return None
    title = re.sub(r"<[^>]+>|\s+", " ", title.group(1)).strip()
    res = evaluate(title, float(price.group(1)))
    if "quick_sale" not in res:
        return title, res.get("verdict"), None
    risk = kc.parse_listing(page, float(price.group(1)), res["quick_sale"])
    res["desc"], res["risk_lines"] = risk.get("desc"), kc.risk_lines(risk)
    return title, res["verdict"], pc.photo_line(res, title, pc.ka_images(page))


def ebay_case(item: str):
    import ebay_watch as ew
    from main import _request_with_backoff
    client = ew._client()
    it = _request_with_backoff("GET", ew.ITEM_URL + "get_item_by_legacy_id", headers=client._headers(), params={"legacy_item_id": item})
    lst = ew.listing_of(it)
    r = ew.evaluate_ebay(lst)
    if "quick_sale" not in r:
        return lst["title"], r.get("verdict"), None
    r["desc"] = ew.fetch_desc(client, lst["id"])
    r["ai_seller"] = ew.seller_line(lst)
    risk = ew.risk_of(lst, r["desc"], r.get("quick_sale", 0))   # повне рішення: правила проти шахраїв + ШІ
    if risk["hard"]:
        return lst["title"], r["verdict"], ("правила: " + "; ".join(risk["hard"]), True)
    return lst["title"], r["verdict"], pc.photo_line(r, lst["title"], lst.get("images") or [])


def market_cases(n: int = 8) -> list[tuple[str, str]]:
    """Свіжі звичайні оголошення з eBay (вживані кіти) — перевірка, що ШІ не відсіює чесні лоти."""
    import config
    import ebay_watch as ew
    from main import _request_with_backoff
    client, out = ew._client(), []
    for q in ("ddr4 32gb 2x16gb", "ddr5 32gb 2x16gb", "ddr4 so-dimm 32gb 2x16gb"):
        d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=client._headers(), params={
            "q": q, "category_ids": "170083", "limit": 10, "filter": "buyingOptions:{FIXED_PRICE},itemLocationCountry:DE,conditionIds:{3000}"})
        for it in d.get("itemSummaries") or []:
            m = re.search(r"v1\|(\d+)\|", it.get("itemId", ""))
            if m and len(out) < n:
                out.append(("ebay", m.group(1)))
    return out


def main():
    bad_ok = good_ok = 0
    good = GOOD + market_cases()
    for group, cases in (("ПОГАНІ", BAD), ("НОРМАЛЬНІ", good)):
        print(f"\n===== {group} =====")
        for kind, cid in cases:
            try:
                got = ka_case(cid) if kind == "ka" else ebay_case(cid)
            except Exception as e:
                print(f"{kind} {cid}: помилка {e.__class__.__name__}: {str(e)[:120]}")
                continue
            if not got:
                print(f"{kind} {cid}: оголошення не відкрилось")
                continue
            title, verdict, line = got
            flagged = bool(line and line[1])
            print(f"{kind} {cid} | {verdict} | {title[:70]}\n   → {'⛔' if flagged else 'ok'}: {(line or ('—',))[0][:300]}")
            if group == "ПОГАНІ":
                bad_ok += flagged
            else:
                good_ok += not flagged
    print(f"\nПІДСУМОК: погані зловлено {bad_ok}/{len(BAD)}, нормальні пропущено {good_ok}/{len(good)}")


if __name__ == "__main__":
    main()
