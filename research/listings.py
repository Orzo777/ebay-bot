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
        out.append(dict(id=_t(it, "ItemID", ns), title=_t(it, "Title", ns), price=price,
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


def tip(days: int | None, views: int | None, watch: int, questions: int) -> str:
    if questions:
        return "💬 є питання — відповідь у Mein eBay → Nachrichten"
    if days is None or days < 5:
        return ""
    if watch and days >= 7:
        return "💡 є спостерігачі — надішли їм пропозицію на 5–10 € дешевше (Verkaufen → «Angebot an Beobachter»)"
    if views is not None and views < 5 * days:
        return "💡 мало переглядів — глянь ціну конкурентів; не допоможе — заверши й виставити заново (новий старт у пошуку)"
    if days >= 14:
        return "💡 два тижні без продажу — знизь ціну або перевистав"
    return ""


def report(d: dict, token: str | None, post, now: datetime | None = None) -> tuple[str, dict]:
    """→ (повідомлення HTML, клавіатура з посиланнями)."""
    now = now or datetime.now(timezone.utc)
    rows = [r for r in d.get("rows") or [] if r.get("status") == "Виставлено"]
    lots, err = [], ""
    if token:
        try:
            lots = active(token, post)
        except Exception as e:
            err = f"⚠️ eBay не відповів ({e.__class__.__name__}) — показую з обліку."
            print(f"GetMyeBaySelling: {e.__class__.__name__}")
    body, kb, used = [], [], set()
    if err:
        body.append(err)
    for lot in lots:
        r = _row_for(lot, rows)
        if r:
            used.add(r["n"])
        days = _days(lot["start"], now)
        views = hits(token, post, lot["id"]) if lot["id"] else None
        name = (f"№{r['n']} " if r else "") + html.escape(short((r or lot)["title"]))
        stats = " · ".join(x for x in [f"💶 {lot['price']:.0f} €" if lot["price"] else "",
                                        f"{days} дн." if days is not None else "",
                                        f"👁 {views}" if views is not None else "",
                                        f"⭐ {lot['watch']}", f"💬 {lot['questions']}" if lot["questions"] else ""] if x)
        t = tip(days, views, lot["watch"], lot["questions"])
        body.append(f"<b>{name}</b> · eBay\n<blockquote>{stats}" + (f"\n{t}" if t else "") + "</blockquote>")
        kb.append([{"text": f"🔗 {('№' + str(r['n']) + ' ') if r else ''}на eBay", "url": lot["url"]}])
    other = [r for r in rows if r["n"] not in used]
    for r in other:
        body.append(f"<b>№{r['n']} {html.escape(short(r['title']))}</b> · KA / вручну\n"
                    "<blockquote>статистики KA бот не бачить — перегляди й повідомлення в «Мої оголошення»</blockquote>")
    if other:
        kb.append([{"text": "🔗 Мої оголошення на KA", "url": KA_MINE}])
    n = len(lots) + len(other)
    out = [f"🏷 <b>Виставлено</b> · {n}" if n else "🏷 <b>Виставлено</b>"] + (body or ["Нічого не виставлено. Продати — «💶 Продати»."])
    return "\n\n".join(out), {"inline_keyboard": kb}
