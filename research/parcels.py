"""«📦 Де посилки» (12.10, прохання користувача): одним повідомленням — куди йде кожна посилка, до нас і від нас.

Дані: рядки обліку з треками (з Apps Script, зашифровано), eBay GetOrders (покупки й продажі: відправлено, трек, очікувана
й фактична доставка) і живий статус DHL з публічної сторінки відстеження dhl.de (той самий JSON, що бачить людина; ключ не
потрібен). Hermes / DPD / GLS — останнє, що знає облік, і посилання на сторінку перевізника.
"""
import html
import os
import re
from datetime import datetime, timezone

DHL_URL = "https://www.dhl.de/int-verfolgen/data/search"
# 12.10: з серверів GitHub публічна сторінка DHL віддає захист від ботів (не JSON) — там потрібен офіційний безкоштовний ключ
# (developer.dhl.com → «Shipment Tracking - Unified», секрет DHL_API_KEY). Без ключа — публічна сторінка (працює з дому).
DHL_API = "https://api-eu.dhl.com/track/shipments"
TRACK_LINK = {"DHL": "https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html?piececode={}",
              "Hermes": "https://www.myhermes.de/empfangen/sendungsverfolgung/sendungsinformation#{}",
              "DPD": "https://tracking.dpd.de/status/de_DE/parcel/{}",
              "GLS": "https://gls-group.com/DE/de/paketverfolgung?match={}",
              "UPS": "https://www.ups.com/track?tracknum={}&loc=de_DE"}


def _d(s: str) -> str:
    """ISO → «12.10»."""
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).strftime("%d.%m")
    except ValueError:
        return ""


def _find(o, pat: re.Pattern) -> list:
    out = []
    if isinstance(o, dict):
        for k, v in o.items():
            if pat.search(k) and isinstance(v, (str, int, float)) and v not in ("", None):
                out.append((k, v))
            out += _find(v, pat)
    elif isinstance(o, list):
        for v in o:
            out += _find(v, pat)
    return out


def dhl_api_status(num: str, get, key: str) -> dict | None:
    """Офіційний DHL Shipment Tracking - Unified API (ключ DHL_API_KEY)."""
    try:
        r = get(DHL_API, params={"trackingNumber": num, "language": "de"}, headers={"DHL-API-Key": key}, timeout=20)
        if r.status_code != 200:
            print(f"DHL API {r.status_code}")
            return None
        sh = (r.json().get("shipments") or [{}])[0]
    except Exception as e:
        print(f"DHL API: {e.__class__.__name__}")
        return None
    st = sh.get("status") or {}
    if not st:
        return None
    text = str(st.get("description") or st.get("status") or "")
    code = str(st.get("statusCode") or "")
    tf = sh.get("estimatedDeliveryTimeFrame") or {}
    eta = [x for x in (tf.get("estimatedFrom"), tf.get("estimatedThrough")) if x] or \
          ([sh["estimatedTimeOfDelivery"]] if sh.get("estimatedTimeOfDelivery") else [])
    delivered = code == "delivered"
    return dict(status=text, date=_d(st.get("timestamp", "")), delivered=delivered,
                ready=not delivered and bool(re.search(r"filiale|packstation|paketshop|abhol", text, re.I))
                and not re.search(r"abholung .{0,20}erfolgt|abgeholt", text, re.I),
                eta=" – ".join(_d(x) for x in eta[:2]))


def dhl_status(num: str, get) -> dict | None:
    """→ {status, date, delivered, ready, eta} або None (DHL не відповів / номер невідомий)."""
    key = re.sub(r"\s+", "", os.getenv("DHL_API_KEY") or "")   # 12.10: ключ вставили з переносом рядка — InvalidHeader
    if key:
        return dhl_api_status(num, get, key)
    try:
        r = get(DHL_URL, params={"piececode": num, "language": "de"}, timeout=20,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"})
        if r.status_code != 200:
            print(f"DHL {r.status_code}")
            return None
        s = (r.json().get("sendungen") or [{}])[0]
    except Exception as e:
        print(f"DHL: {e.__class__.__name__}")
        return None
    det = s.get("sendungsdetails") or {}
    ver = det.get("sendungsverlauf") or {}
    if not ver.get("status"):
        return None
    st = str(ver["status"])
    eta = [str(v) for k, v in _find(det, re.compile(r"zeitfenster|voraussichtlich|zustelltermin|zustelldatum", re.I))]
    return dict(status=st, date=_d(ver.get("datumAktuellerStatus", "")), delivered=bool(det.get("istZugestellt")),
                ready=not det.get("istZugestellt") and bool(re.search(r"filiale|packstation|paketshop|abhol", st, re.I))
                and not re.search(r"abholung .{0,20}erfolgt|abgeholt", st, re.I),
                eta=" – ".join(_d(x) or x[:16] for x in eta[:2]))


def _t(el, path: str, ns: str) -> str:
    x = el.find("/".join(ns + p for p in path.split("/")))
    return (x.text or "").strip() if x is not None and x.text else ""


def ebay_orders(token: str, post, role: str) -> list[dict]:
    """Покупки (Buyer) або продажі (Seller) за 45 днів: трек, відправлено, очікувана / фактична доставка."""
    import ebay_actions as ea
    import ebay_list as el
    root = el.trading("GetOrders", f"<CreateTimeFrom>{ea._iso(45)}</CreateTimeFrom><CreateTimeTo>{ea._iso()}</CreateTimeTo>"
                      f"<OrderRole>{role}</OrderRole><OrderStatus>All</OrderStatus>", token, post)
    ns, out = ea.NS, []
    for o in root.iter(ns + "Order"):
        for tr in o.iter(ns + "Transaction"):
            tracks = [(_t(t, "ShipmentTrackingNumber", ns), _t(t, "ShippingCarrierUsed", ns))
                      for t in tr.iter(ns + "ShipmentTrackingDetails")]
            pk = next(iter(list(tr.iter(ns + "ShippingPackageInfo")) + list(o.iter(ns + "ShippingPackageInfo"))), None)
            out.append(dict(item_id=_t(tr, "Item/ItemID", ns), title=_t(tr, "Item/Title", ns), status=_t(o, "OrderStatus", ns),
                            shipped=_t(o, "ShippedTime", ns), track=(tracks[0] if tracks else ("", "")),
                            eta_min=_t(pk, "EstimatedDeliveryTimeMin", ns) if pk is not None else "",
                            eta_max=_t(pk, "EstimatedDeliveryTimeMax", ns) if pk is not None else "",
                            delivered=_t(pk, "ActualDeliveryTime", ns) if pk is not None else ""))
    return out


def _words(t: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]{3,}", str(t).lower()) if w not in {"ram", "ddr4", "ddr5", "für", "mit", "gebraucht"}}


def _match(row: dict, orders: list[dict]) -> dict | None:
    if row.get("id"):
        hit = next((o for o in orders if o["item_id"] == str(row["id"])), None)
        if hit:
            return hit
    want = _words(row.get("title", ""))
    best = max(orders, key=lambda o: len(want & _words(o["title"])), default=None)
    return best if best and len(want & _words(best["title"])) >= 3 else None


def line_for(row: dict, order: dict | None, live: dict | None, outgoing: bool) -> str:
    who = f"№{row['n']} {html.escape(str(row['title'])[:34])}"
    src = "" if outgoing else f" ({html.escape(str(row.get('src') or ''))})"
    num, carrier = row.get("track") or (order or {}).get("track", ("", ""))[0], row.get("carrier") or ""
    if order and not num and order["track"][0]:
        num, carrier = order["track"][0], order["track"][1] or carrier
    link = (TRACK_LINK.get(carrier) or TRACK_LINK["DHL"]).format(num) if num else ""
    tail = f' · <a href="{html.escape(link)}">{html.escape(carrier or "трек")}</a>' if link else ""
    if live:
        if live["delivered"]:
            state = f"✅ доставлено {live['date']}" + (" (забрано у відділенні)" if re.search(r"filiale|abgeholt|abholung", live["status"], re.I) else "")
        elif live["ready"]:
            state = f"📬 чекає у відділенні / Packstation з {live['date']}" + ("" if outgoing else " — забери")
        else:
            state = f"🚚 {html.escape(live['status'][:90])} ({live['date']})" + (f" · очікується {live['eta']}" if live["eta"] else "")
    elif (order and order.get("delivered")) or row.get("delivered"):   # облік знає доставку раніше за eBay (11.10, №2)
        state = f"✅ доставлено {_d((order or {}).get('delivered') or row['delivered'])}"
    elif order and (order.get("shipped") or num):
        eta = f" · очікується {_d(order['eta_min'])}–{_d(order['eta_max'])}" if order.get("eta_max") else ""
        state = f"🚚 відправлено {_d(order['shipped'])}" + eta if order.get("shipped") else "🚚 в дорозі" + eta
    elif order:
        eta = f" · eBay обіцяє {_d(order['eta_min'])}–{_d(order['eta_max'])}" if order.get("eta_max") else ""
        state = ("⏳ ще не відправлено" if not outgoing else "⏳ ще не відправив") + eta
    elif row.get("last"):
        state = f"🚚 {html.escape(row['last'])}"
    elif num:
        state = "🚚 в дорозі"
    elif "klein" in str(row.get("src", "")).lower():   # KA шле трек лише в чат, не на пошту
        state = f"⏳ трек є лише в KA — скопіюй («Details» → «Sendungsverfolgung») і напиши «трек {row['n']} номер»"
    else:
        state = "⏳ трек-номера ще немає"
    return f"• {who}{src} — {state}{tail}"


def report(d: dict, token: str | None, post, get) -> str:
    rows_in, rows_out = d.get("incoming") or [], d.get("outgoing") or []
    if not rows_in and not rows_out:
        return "📦 Посилок у дорозі немає: усе куплене вже в тебе, а продане — доставлено."
    buy, sell = [], []
    if token:
        try:
            buy = ebay_orders(token, post, "Buyer")
            sell = ebay_orders(token, post, "Seller")
        except Exception as e:   # eBay недоступний — лишаються DHL і облік
            print(f"eBay GetOrders: {e.__class__.__name__}")
    out = ["📦 <b>Посилки</b>"]
    for title, rows, orders, outgoing in (("⬇️ До тебе", rows_in, buy, False), ("⬆️ Від тебе покупцям", rows_out, sell, True)):
        if not rows:
            continue
        out.append(title)
        for r in rows:
            order = _match(r, orders) if (outgoing or "ebay" in str(r.get("src", "")).lower()) else None
            num = r.get("track") or (order or {}).get("track", ("", ""))[0]
            carrier = r.get("carrier") or (order or {}).get("track", ("", ""))[1]
            live = dhl_status(num, get) if num and (not carrier or "dhl" in carrier.lower() or "deutsche post" in carrier.lower()) else None
            out.append(line_for(r, order, live, outgoing))
    return "\n".join(out)
