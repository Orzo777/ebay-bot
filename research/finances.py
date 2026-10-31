"""Фактичні витрати eBay по наших продажах (31.10, користувач: «фактичні мінуси в таблиці робимо»).

Finances API (scope sell.finances): етикетка (SHIPPING_LABEL) і комісії — реклама Basis (NON_SALE_CHARGE, з ПДВ) та
комісія з продажу (marketplaceFees у SALE; у приватного продавця 0) — по кожному замовленню. research/sell.py (режим sync)
додає їх до звірки, tools/ledger.gs пише в колонки «Моя пересилка покупцю» і «Комісія eBay». Без доступу (ключ eBay
старий, без sell.finances) — None: бот один раз попросить «ebay вхід».
"""
from datetime import datetime, timedelta, timezone

FIN_URL = "https://apiz.ebay.com/sell/finances/v1/transaction"


def _order_of(t: dict) -> str:
    if t.get("orderId"):
        return str(t["orderId"])
    for ref in t.get("references") or []:
        if ref.get("referenceType") == "ORDER_ID" and ref.get("referenceId"):
            return str(ref["referenceId"])
    return ""


def _amount(x) -> float:
    try:
        return float((x or {}).get("value") or 0)
    except (TypeError, ValueError):
        return 0.0


def order_costs(transactions: list[dict]) -> dict:
    """Транзакції Finances API → {order_id: {"label": €, "fees": €}} (витрати — додатні; повернення витрат — мінус)."""
    out = {}
    for t in transactions:
        oid = _order_of(t)
        if not oid:
            continue
        typ = t.get("transactionType") or ""
        sign = -1.0 if t.get("bookingEntry") == "CREDIT" else 1.0   # витрата — DEBIT; повернення етикетки / комісії — CREDIT
        c = out.setdefault(oid, {"label": 0.0, "fees": 0.0})
        if typ == "SHIPPING_LABEL":
            c["label"] += sign * _amount(t.get("amount"))
        elif typ == "NON_SALE_CHARGE":   # реклама Basis («Gebühr für Basis-Anzeigen») та інші платні опції замовлення
            c["fees"] += sign * _amount(t.get("amount"))
        elif typ == "SALE":   # комісія з продажу (приватний продавець — 0)
            c["fees"] += sum(_amount(f.get("amount")) for li in t.get("orderLineItems") or []
                             for f in li.get("marketplaceFees") or [])
    return {k: {"label": round(v["label"], 2), "fees": round(v["fees"], 2)} for k, v in out.items()
            if v["label"] or v["fees"]}


def fetch(token: str, get, days: int = 45, sign: dict | None = None) -> list[dict]:
    """Усі транзакції за days днів (сторінками по 200). sign — ключ підпису (research/ebay_sign.py): для продавців з ЄС
    eBay без нього відповідає 403 «Missing x-ebay-signature…»."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    h = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_DE", "Accept": "application/json"}
    out, off = [], 0
    while off < 2000:
        if sign:
            import ebay_sign
            h = dict(h, **ebay_sign.headers(sign, "GET", FIN_URL))
        r = get(FIN_URL, headers=h, params={"filter": f"transactionDate:[{since}..{now.strftime('%Y-%m-%dT%H:%M:%S.000Z')}]",
                                             "limit": 200, "offset": off}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Finances API HTTP {r.status_code}: {r.text[:160]}")
        page = r.json().get("transactions") or []
        out += page
        if len(page) < 200:
            break
        off += 200
    return out
