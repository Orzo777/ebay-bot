"""«🏷 Виставлено» (16.10, прохання користувача): що зараз виставлено — коротко й з посиланнями, без гортання чату.

eBay (якщо підключено): GetMyeBaySelling → активні лоти: ціна, скільки днів, спостерігачі, питання; GetItem → перегляди
(HitCount, якщо eBay його віддає). Зіставлення з обліком — за номером eBay у нотатках («виставлено … (eBay 123…)») або назвою.
Kleinanzeigen API немає — рядки обліку «Виставлено» без номера eBay показуємо з посиланням на «Мої оголошення».
Підказка на лот: є спостерігачі й тиждень без продажу → пропозиція спостерігачам; мало переглядів → ціна / перевиставити.
"""
import html
import re
from datetime import datetime, timezone

KA_MINE = "https://www.kleinanzeigen.de/m-meine-anzeigen.html"


def _t(el, path: str, ns: str) -> str:
    x = el.find("/".join(ns + p for p in path.split("/"))) if el is not None else None
    return (x.text or "").strip() if x is not None and x.text else ""


def _num(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def active(token: str, post) -> list[dict]:
    """Активні лоти eBay (до 50)."""
    import ebay_list as el
    root = el.trading("GetMyeBaySelling", "<ActiveList><Include>true</Include><Pagination><EntriesPerPage>50</EntriesPerPage>"
                      "<PageNumber>1</PageNumber></Pagination></ActiveList><DetailLevel>ReturnAll</DetailLevel>", token, post)
    ns, out = el.NS, []
    lst = root.find(ns + "ActiveList")
    for it in (lst.iter(ns + "Item") if lst is not None else []):
        price = _num(_t(it, "SellingStatus/CurrentPrice", ns)) or _num(_t(it, "BuyItNowPrice", ns))
        free = _t(it, "ShippingDetails/ShippingServiceOptions/FreeShipping", ns).lower() == "true"
        ship = 0.0 if free else _num(_t(it, "ShippingDetails/ShippingServiceOptions/ShippingServiceCost", ns))
        out.append(dict(id=_t(it, "ItemID", ns), title=_t(it, "Title", ns), price=price, ship=ship, sku=_t(it, "SKU", ns),
                        watch=int(_num(_t(it, "WatchCount", ns)) or 0), questions=int(_num(_t(it, "QuestionCount", ns)) or 0),
                        start=_t(it, "ListingDetails/StartTime", ns), url=_t(it, "ListingDetails/ViewItemURL", ns)
                        or f"https://www.ebay.de/itm/{_t(it, 'ItemID', ns)}"))
    return out


def hits(token: str, post, item_id: str) -> int | None:
    """Перегляди лота (HitCount) — якщо eBay віддає; інакше None."""
    import ebay_list as el
    try:
        root = el.trading("GetItem", f"<ItemID>{html.escape(item_id)}</ItemID><IncludeWatchCount>true</IncludeWatchCount>", token, post)
    except Exception as e:
        print(f"GetItem: {e.__class__.__name__}")
        return None
    v = _num(_t(root.find(el.NS + "Item"), "HitCount", el.NS))
    return int(v) if v is not None else None


def short(t: str, n: int = 42) -> str:
    """Назва до n знаків — по межі слова, без «(» / «-» в кінці."""
    t = str(t).strip()
    if len(t) <= n:
        return t
    cut = t[:n]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" (-–,/")


def _days(start: str, now: datetime) -> int | None:
    try:
        return (now - datetime.fromisoformat(start.replace("Z", "+00:00"))).days
    except ValueError:
        return None


def _words(t: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]{3,}", str(t).lower()) if w not in {"ram", "ddr4", "ddr5", "für", "mit", "getestet"}}


def _row_for(lot: dict, rows: list[dict]) -> dict | None:
    hit = next((r for r in rows if lot["id"] and lot["id"] in str(r.get("note") or "")), None)
    if hit:
        return hit
    want = _words(lot["title"])
    best = max(rows, key=lambda r: len(want & _words(r.get("title", ""))), default=None)
    return best if best and len(want & _words(best.get("title", ""))) >= 3 else None


_ID_IN_NOTE = re.compile(r"eBay (\d{9,15})")
SYNC_FREE = ("Отримано", "Перевірено", "Виставлено")   # з цих статусів товар може з'явитись на eBay


def _last_id(note: str) -> str:
    ids = _ID_IN_NOTE.findall(str(note or ""))
    return ids[-1] if ids else ""


def _same_kit(title: str, rows: list[dict]) -> dict | None:
    """Рядок з тим самим кітом (обсяг, покоління, тип, кількість планок, частота) — лише якщо такий один."""
    from ram_parse import parse_title
    p, _ = parse_title(title)
    if not p:
        return None
    hits = []
    for r in rows:
        q, _ = parse_title(r.get("title") or "")
        same_brand = q and (q.get("brand") in (None, "other") or p.get("brand") in (None, "other") or q["brand"] == p["brand"])
        if same_brand and all(q.get(k) == p.get(k) for k in ("gen", "form", "total", "modules")) and \
                (not q.get("speed") or q["speed"] == p.get("speed")):
            hits.append(r)
    return hits[0] if len(hits) == 1 else None


def sync(lots: list[dict], rows: list[dict]) -> dict:
    """17.10 (користувач: «бот сам відслідковує зміни в оголошеннях і заносить в облік»): активні лоти eBay → облік.
    rows — відкриті рядки {n, title, status, note}. → {"lots": [{n, id, price}] — лот відомого рядка (номер у нотатках або
    однозначно за назвою: Apps Script записує «Виставлено» / нову ціну), "ask": [{id, title, price, cands}] — не однозначно
    (спитати кнопками), "ended": [{n, id}] — «Виставлено» з номером, якого вже немає серед активних}."""
    known, ask = [], []
    active_ids = {lot["id"] for lot in lots if lot.get("id")}
    linked = {_last_id(r.get("note")) for r in rows} - {""}
    for lot in lots:
        if not lot.get("id"):
            continue
        r = next((r for r in rows if _last_id(r.get("note")) == lot["id"]), None)
        if r:
            known.append(dict(n=r["n"], id=lot["id"], price=lot.get("price"), title=lot.get("title") or ""))
            continue
        free = [r for r in rows if r.get("status") in SYNC_FREE and not _last_id(r.get("note"))]
        # 31.10: лот на кілька штук (№5 і №6 — однакові кіти, SKU ledger-5): №5 продали — решта лота тепер №6. SKU рядка,
        # якого вже немає серед відкритих, → один відкритий рядок з тими самими обсягом, поколінням, типом і частотою
        sku = re.match(r"^ledger-(\d{1,4})$", str(lot.get("sku") or ""))
        if sku and not any(int(r["n"]) == int(sku.group(1)) for r in rows):
            twin = _same_kit(lot["title"], [r for r in rows if r.get("status") in SYNC_FREE and (not _last_id(r.get("note"))
                                                                                             or _last_id(r.get("note")) == lot["id"])])
            if twin:
                known.append(dict(n=twin["n"], id=lot["id"], price=lot.get("price"), title=lot.get("title") or "",
                                  twin_of=int(sku.group(1))))
                continue
        if lot["id"] in linked:
            continue
        want = _words(lot["title"])
        scored = sorted(((len(want & _words(r.get("title", ""))), r) for r in free), key=lambda x: -x[0])
        if not scored or scored[0][0] < 2:
            continue   # схоже, не з обліку (особисті речі тощо) — не питаємо
        best = scored[0][0]
        second = scored[1][0] if len(scored) > 1 else 0
        if best >= 4 and best - second >= 3:   # схожі кіти (2133 / 2400) — краще спитати одним натиском, ніж переплутати
            known.append(dict(n=scored[0][1]["n"], id=lot["id"], price=lot.get("price"), title=lot.get("title") or ""))
        else:
            ask.append(dict(id=lot["id"], title=short(lot["title"], 60), price=lot.get("price"),
                            cands=[r["n"] for sc, r in scored if sc >= max(2, best - 2)][:4]))
    ended = [dict(n=r["n"], id=_last_id(r.get("note"))) for r in rows
             if r.get("status") == "Виставлено" and _last_id(r.get("note")) and _last_id(r.get("note")) not in active_ids]
    return {"lots": known, "ask": ask, "ended": ended}


MARKET_MIN_DAYS, MARKET_GAP = 5, 3.0   # 19.10: користувач — порада з 5-го дня


def market_check(lots: list[dict], rows: list[dict], now: datetime | None = None, comp_fn=None) -> list[dict]:
    """19.10 (користувач: «на 2 оголошеннях мало переглядів — частіше заміряй ціни»): раз на добу — наші лоти проти
    живих конкурентів (повна ціна з доставкою). Дорожчі за третього найдешевшого більш ніж на MARKET_GAP € і виставлені
    ≥ MARKET_MIN_DAYS дн. → [{n, id, price, ship, comp, item, profit}] — ціна товару, з якою стаємо серед трьох найдешевших
    (не нижче мінімуму прибутку від ціни купівлі `spent`). Один виклик Browse API на лот."""
    import sell
    now, out = now or datetime.now(timezone.utc), []
    comp_fn = comp_fn or sell.competitors
    for lot in lots:
        r = _row_for(lot, rows)
        days = _days(lot.get("start") or "", now)
        if not r or not lot.get("price") or days is None or days < MARKET_MIN_DAYS:
            continue
        ident = sell.identify(r.get("title") or lot["title"])
        if not ident:
            continue
        try:
            comp = comp_fn(ident)
        except Exception as e:
            print(f"ринок №{r['n']}: {e.__class__.__name__}")
            continue
        ship = lot.get("ship")
        if ship is None:
            ship = sell.SHIP_CHARGE_RAM if ident["kind"] == "ram" else (ident.get("ship_out") or sell.console_alert.SHIP)
        mine = lot["price"] + ship
        if len(comp) < 3 or mine <= comp[2] + MARKET_GAP:
            continue
        spent = r.get("spent")
        pr = sell.plan_price(ident, float(spent) if spent else None, comp, undercut=True)
        item = max(1, round(pr["list"] - ship))
        if item >= lot["price"] - 1:   # мінімум прибутку не дає знизити
            continue
        out.append(dict(n=r["n"], id=lot["id"], price=lot["price"], ship=ship, comp=[round(c) for c in comp[:3]], item=item,
                        total=len(comp), place=1 + sum(c < mine for c in comp),   # «ти 6-й з 36»
                        floor=item + ship > comp[2],   # нижче не можна: прибуток < мінімуму — серед найдешевших не станемо
                        profit=round(pr["profit_list"]) if pr.get("profit_list") is not None else None))
    return out


def stale(days: int | None, views: int | None, watch: int) -> bool:
    """Лот застоявся: 14+ днів, або 7+ днів і мало переглядів (<5 на день), без спостерігачів."""
    if days is None or watch:
        return False
    return days >= 14 or (days >= 7 and views is not None and views < 5 * days)


def nice(p: float) -> int:
    """159, 164, 169… — не вище за p (як ціни бота)."""
    p = int(p)
    return p if p < 30 or p % 5 == 4 else p - (p % 5) - 1


def tip(days: int | None, views: int | None, watch: int, questions: int, price: float | None = None) -> str:
    if questions:
        return "💬 є питання — відповідь у Mein eBay → Nachrichten"
    if days is None or days < 5:
        return ""
    if watch and days >= 5:   # 18.10 (крок 6): з конкретною ціною
        offer = f" {nice(price * 0.95)} € — кнопка нижче" if price else " на 5–10 € дешевше"   # eBay: знижка ≥5%
        return f"💡 є спостерігачі — надішли їм пропозицію{offer}"
    if views is not None and views < 5 * days:
        return "💡 мало переглядів — глянь ціну конкурентів; не допоможе — заверши й виставити заново (новий старт у пошуку)"
    if days >= 14:
        return "💡 два тижні без продажу — знизь ціну або перевистав"
    return ""


def report(d: dict, token: str | None, post, now: datetime | None = None, lots: list | None = None) -> tuple[str, dict]:
    """→ (повідомлення HTML, клавіатура з посиланнями). lots — уже отримані активні лоти (інакше запит тут)."""
    now = now or datetime.now(timezone.utc)
    rows = [r for r in d.get("rows") or [] if r.get("status") == "Виставлено"]
    err = ""
    if lots is None and token:
        try:
            lots = active(token, post)
        except Exception as e:
            err = f"⚠️ eBay не відповів ({e.__class__.__name__}) — показую з обліку."
            print(f"GetMyeBaySelling: {e.__class__.__name__}")
    lots = lots or []
    body, kb, used = [], [], set()
    if err:
        body.append(err)
    for lot in lots:
        r = _row_for(lot, rows)
        if r:
            used.add(r["n"])
        # 18.10: оголошення з кількістю — кілька номерів з тим самим номером eBay (№5 + №6)
        twins = [x["n"] for x in rows if r and x["n"] != r["n"] and lot["id"] and _last_id(x.get("note")) == lot["id"]]
        used.update(twins)
        days = _days(lot["start"], now)
        views = hits(token, post, lot["id"]) if lot["id"] else None
        name = ("№" + " + №".join(str(n) for n in [r["n"]] + twins) + " " if r else "") + html.escape(short((r or lot)["title"]))             + (f" · {1 + len(twins)} шт" if twins else "")
        stats = " · ".join(x for x in [f"💶 {lot['price']:.0f} €" if lot["price"] else "",
                                        f"{days} дн." if days is not None else "",
                                        f"👁 {views}" if views is not None else "",
                                        f"⭐ {lot['watch']}", f"💬 {lot['questions']}" if lot["questions"] else ""] if x)
        t = tip(days, views, lot["watch"], lot["questions"], lot["price"])
        body.append(f"<b>{name}</b> · eBay\n<blockquote>{stats}" + (f"\n{t}" if t else "") + "</blockquote>")
        kb.append([{"text": f"🔗 {('№' + str(r['n']) + ' ') if r else ''}на eBay", "url": lot["url"]}])
        if r and lot["price"] and lot["watch"] and days is not None and days >= 5:   # 18.10: пропозиція — кнопкою
            kb.append([{"text": f"💌 №{r['n']}: спостерігачам {nice(lot['price'] * 0.95)} €",
                        "callback_data": f"w|{r['n']}|{nice(lot['price'] * 0.95)}"}])
        if r and lot["price"] and stale(days, views, lot["watch"]):   # 18.10 (крок 6): дія — однією кнопкою
            kb.append([{"text": f"⬇️ №{r['n']} → {nice(lot['price'] * 0.95)} €", "callback_data": f"p|{r['n']}|{nice(lot['price'] * 0.95)}"},
                       {"text": f"♻️ Перевиставити №{r['n']}", "callback_data": f"c|перевиставити|{r['n']}"}])
    other = [r for r in rows if r["n"] not in used]
    for r in other:
        body.append(f"<b>№{r['n']} {html.escape(short(r['title']))}</b> · KA / вручну\n"
                    "<blockquote>статистики KA бот не бачить — перегляди й повідомлення в «Мої оголошення»</blockquote>")
    if other:
        kb.append([{"text": "🔗 Мої оголошення на KA", "url": KA_MINE}])
    n = len(lots) + len(other)
    out = [f"🏷 <b>Виставлено</b> · {n}" if n else "🏷 <b>Виставлено</b>"] + (body or ["Нічого не виставлено. Продати — «💶 Продати»."])
    return "\n\n".join(out), {"inline_keyboard": kb}
