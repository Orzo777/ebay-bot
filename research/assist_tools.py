"""Інструменти бота-помічника (10.10): ті самі оцінювачі, що й у картках, — для Claude Code в assist.yml.
Помічник викликає їх через Bash (інших команд йому не дозволено) і відповідає користувачу.

    python research/assist_tools.py eval "Gaming PC i5-9400F GTX 1660 16GB 512GB SSD" 160
    python research/assist_tools.py listing https://www.kleinanzeigen.de/s-anzeige/…/3535815569-…
    python research/assist_tools.py parts            # таблиці цін деталей ПК, RAM, консолей
    python research/assist_tools.py market "i5-9400F" [--used]   # оголошення eBay.de зараз: скільки, ціни

Лише читання: нічого не надсилає в Telegram, нічого не змінює. Сторінку KA відкриває одну — як «поділитися».
"""
import html
import os
import re
import sys
from statistics import median

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERDICT = {"BUY-EXCELLENT": "дуже вигідно", "BUY-GOOD": "вигідно", "BUY": "бери", "NEGOTIATE": "торгуйся", "SKIP": "не бери",
           "UNKNOWN": "не розпізнав", "AUCTION": "аукціон"}


def _strip(h: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", h or ""))


def _fmt(d: dict, keys) -> str:
    out = []
    for k in keys:
        v = d.get(k)
        if v is None or v == "":
            continue
        out.append(f"{k}={round(v, 1) if isinstance(v, float) else v}")
    return ", ".join(out)


def eval_text(title: str, price: float, pickup: bool = False) -> str:
    import console_alert as ca
    import pc_alert
    import ram_alert as ra
    lines = [f"Оцінка «{title}» за {price:.0f} €" + (" (самовивіз Гамбург)" if pickup else "") + ":"]
    c = ca.evaluate_console(title, price)
    if c is not None:
        if pickup:
            ra.apply_pickup(c)
        lines.append(f"• консоль: {VERDICT.get(c['verdict'], c['verdict'])} — " +
                     _fmt(c, ("type", "reason", "cap", "good", "quick_sale", "median_sale", "profit_est", "buy_cost")))
    else:
        r = ra.evaluate(title, price)
        if r.get("verdict") != "UNKNOWN":
            if pickup and r.get("verdict") not in ("SKIP",):
                ra.apply_pickup(r)
            lines.append(f"• RAM: {VERDICT.get(r['verdict'], r['verdict'])} — " +
                         _fmt(r, ("type", "reason", "cap", "good", "excellent", "quick_sale", "median_sale", "profit_est", "buy_cost")))
    p = pc_alert.evaluate_pc(title, price)
    if p.get("verdict") != "UNKNOWN" or len(lines) == 1:
        lines.append(f"• ПК (ціле / на запчастини, самовивіз): {VERDICT.get(p['verdict'], p['verdict'])} — " +
                     _fmt(p, ("how", "net", "cost", "profit", "max_price", "reason")) + f"; розбір назви: {p.get('parsed')}")
    lines.append("Пороги: «бери» — заробіток ≥30% навіть при швидкому продажу (p25 продажів eBay за 30 днів), «вигідно» ≈60%; "
                 "комісії eBay немає (приватний продавець), пересилка/пакування/резерв повернень враховані.")
    return "\n".join(lines)


def listing(url: str) -> str:
    import requests
    from ebay_watch import ebay_item_id, share_ebay
    from ka_listing_check import UA, parse_listing
    import ka_share
    eid = ebay_item_id(url)
    if eid:
        msg, res, lst = share_ebay(eid)
        out = ["Оголошення eBay:", _strip(msg)]
        if lst:
            out.append(eval_text(lst["title"], float(lst.get("price") or 0)))
        return "\n".join(out)
    u = ka_share.listing_url(url)
    if not u:
        return "Не бачу посилання kleinanzeigen.de/s-anzeige/… чи ebay.de/itm/…"
    r = requests.get(u, headers={"User-Agent": UA, "Accept-Language": "de-DE"}, timeout=15)
    if r.status_code != 200:
        return f"Оголошення не відкривається (код {r.status_code}) — можливо, зняте."
    info = ka_share.parse_page(r.text)
    if not info:
        return "Не вдалося прочитати сторінку оголошення."
    det = parse_listing(r.text, info["price"] or 0, 0)
    desc = (det.get("desc") or "")[:2500]
    msg, _ = ka_share.evaluate_listing(r.text, u)
    out = [f"Kleinanzeigen: «{info['title']}» — {info['price']} €" + (" VB" if info.get("vb") else ""),
           f"місце: {info.get('loc') or info.get('location') or '?'}; лише самовивіз: {bool(info.get('pickup_only'))}; "
           f"доставка від: {info.get('ship_from')}; Гамбург: {bool(info.get('hamburg'))}; комерційний: {bool(info.get('commercial'))}",
           f"ризик (продавець, опис): {det.get('level')} {det.get('reasons') or ''}",
           "Опис продавця:", desc or "(порожній)", "", "Картка бота (RAM/консоль):", _strip(msg), "",
           eval_text(info["title"] + (" | " + desc[:600] if desc else ""), float(info["price"] or 0), bool(info.get("hamburg")))]
    return "\n".join(out)


def parts() -> str:
    import console_alert as ca
    import pc_alert as pc
    import ram_alert as ra
    out = ["Ціни продажу (eBay.de, вживане; p25 = швидкий продаж, med = медіана; оновлюються щодня/щомісяця):",
           "RAM (кіти, € за весь кіт): " + "; ".join(f"{v['name']}: p25 {v['p25']}, med {v['med']}" for v in ra.REAL.values()),
           "Консолі: " + "; ".join(f"{d['name']}: p25 {d['p25']}, med {d['med']}" for d in
                                   (ca.XBOX_SERIES_X, ca.PS5_DISC, ca.PS5_DIGITAL, ca.SWITCH2) if isinstance(d, dict) and "p25" in d),
           "ПК офісні цілим за процесором (€): " + "; ".join(f"{a} {b}-го: {v}" for (a, b), v in pc.PC_BY_CPU.items()),
           "ПК ігрові цілим за відеокартою (€): " + "; ".join(f"{k}: {v}" for k, v in pc.GAMING_PC_BY_GPU.items()),
           "Відеокарти окремо (€): " + "; ".join(f"{k}: {v}" for k, v in pc.GPU_PART.items()),
           "Процесори окремо (€): " + "; ".join(f"{a} {b}-го: {v}" for (a, b), v in pc.CPU_PART.items()),
           "SSD окремо (€, ГБ→ціна): " + "; ".join(f"{k}: {v}" for k, v in pc.SSD_PART.items()),
           f"Решта ПК (плата+корпус+БЖ) одним лотом на KA: ~{pc.REST} €; самовивіз ~{pc.PICKUP} € дорога."]
    return "\n".join(out)


def market(query: str, used: bool = True) -> str:
    """Оголошення eBay.de зараз (Browse API, 1 запит): скільки й почім — орієнтир попиту/пропозиції, не продажі."""
    import config
    from main import EbayClient, _request_with_backoff
    c = EbayClient()
    flt = "buyingOptions:{FIXED_PRICE},priceCurrency:EUR,itemLocationCountry:DE" + (",conditionIds:{3000}" if used else "")
    d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=c._headers(),
                              params={"q": query, "filter": flt, "limit": 50})
    items = d.get("itemSummaries") or []
    prices = sorted(float(i["price"]["value"]) + float(((i.get("shippingOptions") or [{}])[0].get("shippingCost") or {}).get("value") or 0)
                    for i in items if i.get("price"))
    if not prices:
        return f"eBay.de: за «{query}» оголошень не знайшов."
    q1 = prices[len(prices) // 4]
    return (f"eBay.de зараз за «{query}»{' (вживані)' if used else ''}: усього {d.get('total', len(items))} оголошень; "
            f"з перших {len(prices)}: від {prices[0]:.0f} €, p25 {q1:.0f} €, медіана {median(prices):.0f} € (з доставкою). "
            f"Приклади: " + "; ".join(i.get("title", "")[:60] for i in items[:5]) +
            "\nЦе ціни ОГОЛОШЕНЬ (не продажів): продають зазвичай на 10–25% дешевше за медіану оголошень.")


def main(argv: list[str]) -> str:
    if not argv:
        return __doc__
    cmd, rest = argv[0], argv[1:]
    if cmd == "eval" and len(rest) >= 2:
        return eval_text(rest[0], float(str(rest[1]).replace(",", ".").replace("€", "")), "--pickup" in rest)
    if cmd == "listing" and rest:
        return listing(rest[0])
    if cmd == "parts":
        return parts()
    if cmd == "market" and rest:
        return market(rest[0], "--new" not in rest)
    return __doc__


if __name__ == "__main__":
    print(main(sys.argv[1:]))
