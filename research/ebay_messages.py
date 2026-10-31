"""Порядок у повідомленнях eBay (31.10, користувач: «у повідомленнях бардак: системні від eBay, покупці, продавці, ті, кому
вже продав… хочу, щоб в активних були лише ті, хто чекає на мою оплату, і мої покупці; решта — видна, але відфільтрована,
щоб я міг перемикатись; і щоб бот за цим ще й звірявся й не писав не в той чат»).

Trading API (класичні «Nachrichten» з папками): GetMyMessages — заголовки «Posteingang», ReviseMyMessagesFolders — свої
папки (до 10), ReviseMyMessages — перенести (до 10 за раз). У «Posteingang» лишається активне: покупці продажів, ще не
відправлених або відправлених ≤ ACTIVE_SALE_DAYS тому; продавці моїх покупок за ACTIVE_BUY_DAYS (і неоплачені); свіжі
(≤ FRESH_DAYS) питання й пропозиції. Решта — у папки FOLDERS. Нічого не видаляємо.
"""
import html
import re
from datetime import datetime, timedelta, timezone

import ebay_list as el

NS = el.NS
ACTIVE_SALE_DAYS, ACTIVE_BUY_DAYS, FRESH_DAYS = 10, 21, 3
FOLDERS = {"ebay": "eBay", "sold": "Verkauft – erledigt", "bought": "Gekauft – erledigt", "other": "Sonstiges"}
_KEEP_EBAY = re.compile(r"(?i)bitte bezahlen|zahlung (?:ist )?ausstehend|bezahlen sie|noch nicht bezahlt|zahlungserinnerung")


def _t(node, path: str) -> str:
    if node is None:
        return ""
    return (node.findtext("/".join(NS + p for p in path.split("/"))) or "").strip()


def _date(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def inbox(token: str, post, days: int = 120) -> list[dict]:
    """Заголовки повідомлень у «Posteingang» за days днів."""
    now = datetime.now(timezone.utc)
    out, page = [], 1
    while page <= 5:
        root = el.trading("GetMyMessages", "<DetailLevel>ReturnHeaders</DetailLevel><FolderID>0</FolderID>"
                          f"<StartTime>{(now - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S.000Z')}</StartTime>"
                          f"<EndTime>{now.strftime('%Y-%m-%dT%H:%M:%S.000Z')}</EndTime>"
                          f"<Pagination><EntriesPerPage>200</EntriesPerPage><PageNumber>{page}</PageNumber></Pagination>",
                          token, post)
        msgs = list(root.iter(NS + "Message"))
        for m in msgs:
            out.append(dict(id=_t(m, "MessageID"), sender=_t(m, "Sender"), to=_t(m, "SendToName") or _t(m, "RecipientUserID"),
                            subject=_t(m, "Subject"), item_id=_t(m, "ItemID"), date=_t(m, "ReceiveDate")))
        if len(msgs) < 200:
            break
        page += 1
    return out


def folder_ids(token: str, post) -> dict:
    """Назва папки → FolderID (свої папки й стандартні)."""
    root = el.trading("GetMyMessages", "<DetailLevel>ReturnSummary</DetailLevel>", token, post)
    return {_t(f, "FolderName"): _t(f, "FolderID") for f in root.iter(NS + "FolderSummary") if _t(f, "FolderName")}


def ensure_folders(token: str, post) -> dict:
    """Створює наші папки, яких ще немає. → ключ (ebay/sold/bought/other) → FolderID."""
    have = folder_ids(token, post)
    for name in FOLDERS.values():
        if name not in have:
            el.trading("ReviseMyMessagesFolders", f"<Operation>Display</Operation><FolderName>{html.escape(name)}</FolderName>",
                       token, post)
    have = folder_ids(token, post)
    return {k: have[v] for k, v in FOLDERS.items() if v in have}


def move(ids: list[str], folder_id: str, token: str, post) -> int:
    n = 0
    for i in range(0, len(ids), 10):
        part = ids[i:i + 10]
        body = "<MessageIDs>" + "".join(f"<MessageID>{html.escape(x)}</MessageID>" for x in part) + "</MessageIDs>" + \
               f"<FolderID>{html.escape(str(folder_id))}</FolderID>"
        errs, _ = el.problems(el.trading("ReviseMyMessages", body, token, post))
        if not errs:
            n += len(part)
    return n


def purchases(token: str, post, days: int = 45) -> list[dict]:
    """Мої покупки (роль покупця): товар, продавець, дата, чи оплачено."""
    now = datetime.now(timezone.utc)
    root = el.trading("GetOrders", f"<CreateTimeFrom>{(now - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S.000Z')}</CreateTimeFrom>"
                      f"<CreateTimeTo>{now.strftime('%Y-%m-%dT%H:%M:%S.000Z')}</CreateTimeTo><OrderRole>Buyer</OrderRole>"
                      "<OrderStatus>All</OrderStatus>", token, post)
    out = []
    for o in root.iter(NS + "Order"):
        for tr in o.iter(NS + "Transaction"):
            out.append(dict(item_id=_t(tr, "Item/ItemID"), seller=_t(o, "SellerUserID") or _t(tr, "Item/Seller/UserID"),
                            created=_t(o, "CreatedTime"), paid=bool(_t(o, "PaidTime")) or _t(o, "OrderStatus") == "Completed"))
    return out


def classify(msgs: list[dict], sales: list[dict], buys: list[dict], now: datetime | None = None) -> dict:
    """→ {"keep": [id…], "ebay": [...], "sold": [...], "bought": [...], "other": [...]} — що куди."""
    now = now or datetime.now(timezone.utc)
    age = lambda s: (now - _date(s)).days if _date(s) else 0   # noqa: E731
    active_sale = {o["item_id"] for o in sales if not o.get("shipped") or (o.get("shipped_at") and
                   age(o["shipped_at"] + "T00:00:00Z") <= ACTIVE_SALE_DAYS)}
    sold = {o["item_id"] for o in sales}
    active_buy = {b["item_id"] for b in buys if not b.get("paid") or age(b.get("created") or "") <= ACTIVE_BUY_DAYS}
    bought = {b["item_id"] for b in buys}
    out = {"keep": [], "ebay": [], "sold": [], "bought": [], "other": []}
    for m in msgs:
        if m["sender"].lower() == "ebay":
            key = "keep" if _KEEP_EBAY.search(m["subject"]) and age(m["date"]) <= ACTIVE_BUY_DAYS else "ebay"
        elif m["item_id"] and m["item_id"] in active_sale | active_buy:
            key = "keep"
        elif m["item_id"] in sold:
            key = "sold"
        elif m["item_id"] in bought:
            key = "bought"
        else:
            key = "keep" if age(m["date"]) <= FRESH_DAYS else "other"   # свіже питання / пропозиція — ще треба відповісти
        out[key].append(m["id"])
    return out


def tidy(token: str, post, sales: list[dict]) -> dict:
    """Розкласти «Posteingang». → скільки куди перенесено."""
    msgs = inbox(token, post)
    plan = classify(msgs, sales, purchases(token, post))
    if not any(plan[k] for k in FOLDERS):
        return {}
    ids = ensure_folders(token, post)
    return {k: move(plan[k], ids[k], token, post) for k in FOLDERS if plan[k] and k in ids}


def active_sale(order: dict, now: datetime | None = None) -> bool:
    """Продаж ще «живий» — покупцю можна писати (не відправлено або відправлено ≤ ACTIVE_SALE_DAYS тому)."""
    now = now or datetime.now(timezone.utc)
    if not order.get("shipped"):
        return True
    d = _date((order.get("shipped_at") or "") + "T00:00:00Z")
    return bool(d) and (now - d).days <= ACTIVE_SALE_DAYS
