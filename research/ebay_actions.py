"""Дії в eBay кнопками з бота «Облік і продаж» (10.10). Той самий ключ, що й для автопублікації (ebay_list.py).

  ship    — «відправив N трек» → трек у замовлення eBay (CompleteSale): «Versendet», покупець бачить відстеження;
  revise  — «⬇️ Знизити» з тижневого звіту → нова ціна й пороги Preisvorschlag у тих самих пропорціях (ReviseFixedPriceItem);
  answer  — «📨 Надіслати» під питанням покупця → чернетка німецькою як відповідь (AddMemberMessageRTQ);
  offer   — «Прийняти / Зустрічна / Відхилити» під пропозицією ціни (RespondToBestOffer).
Кожна дія — лише після натискання користувача. Потрібне замовлення / оголошення / питання / пропозицію знаходимо
за номером eBay, SKU «ledger-N» (автопублікація) або схожістю назви.
"""
import html
import re
from datetime import datetime, timedelta, timezone

import ebay_list as el

NS = el.NS
CARRIER = {"DHL": "DHL", "Hermes": "Hermes", "DPD": "DPD", "GLS": "GLS", "UPS": "UPS"}   # → ShippingCarrierCodeType eBay


def _t(node, path: str) -> str:
    return (node.findtext("/".join(NS + p for p in path.split("/"))) or "").strip()


def _iso(days: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9äöüß]+", (s or "").lower()) if len(w) >= 2}


def similar(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def pick(cands: list[dict], item_id: str = "", row=None, title: str = "", min_sim: float = 0.3) -> dict | None:
    """Номер eBay → SKU «ledger-N» → найсхожіша назва (≥ min_sim); один кандидат без назви — він."""
    if item_id:
        for c in cands:
            if c.get("item_id") == str(item_id):
                return c
    if row:
        for c in cands:
            if c.get("sku") == f"ledger-{row}":
                return c
    if title:
        best = max(cands, key=lambda c: similar(title, c.get("title", "")), default=None)
        if best and similar(title, best.get("title", "")) >= min_sim:
            return best
        return None
    return cands[0] if len(cands) == 1 else None


def _ok(root) -> tuple[bool, list[str]]:
    errs, _ = el.problems(root)
    return not errs, errs


# ----------------------------------------------------------------------------- замовлення: трек, подяка, відгук
THANKS = ("Hallo, vielen Dank für Ihren Kauf! Ihr Paket ist heute an {carrier} übergeben worden, Sendungsnummer {track}. "
          "Ich wünsche Ihnen viel Freude damit. Viele Grüße")
FEEDBACK = "Schnelle Zahlung, unkomplizierter Kauf - jederzeit gerne wieder!"


def recent_orders(token: str, post) -> list[dict]:
    """Продажі за 45 днів (і вже відправлені: етикетка eBay сама ставить «Versendet» і трек)."""
    root = el.trading("GetOrders", f"<CreateTimeFrom>{_iso(45)}</CreateTimeFrom><CreateTimeTo>{_iso()}</CreateTimeTo>"
                      "<OrderRole>Seller</OrderRole><OrderStatus>Completed</OrderStatus>", token, post)
    out = []
    for o in root.iter(NS + "Order"):
        for tr in o.iter(NS + "Transaction"):
            out.append(dict(order_id=_t(o, "OrderID"), line_id=_t(tr, "OrderLineItemID"), item_id=_t(tr, "Item/ItemID"),
                            txn_id=_t(tr, "TransactionID"), title=_t(tr, "Item/Title"), sku=_t(tr, "Item/SKU"),
                            buyer=_t(o, "BuyerUserID") or _t(tr, "Buyer/UserID"), shipped=bool(_t(o, "ShippedTime")),
                            feedback_left=tr.find(NS + "FeedbackLeft") is not None))
    return out


def orders_to_ship(token: str, post) -> list[dict]:
    return [o for o in recent_orders(token, post) if not o["shipped"]]


def pick_order(orders: list[dict], item_id: str = "", row=None, title: str = "") -> dict | None:
    """Спершу серед невідправлених, потім — серед усіх (етикетка eBay вже позначила відправленим)."""
    return pick([o for o in orders if not o["shipped"]], item_id, row, title) or pick(orders, item_id, row, title)


def thank_buyer(line: dict, track: str, carrier: str, token: str, post) -> tuple[bool, list[str]]:
    body = (f"<ItemID>{el._x(line['item_id'])}</ItemID><MemberMessage><Subject>Ihr Paket ist unterwegs</Subject>"
            f"<Body>{el._x(THANKS.format(carrier=carrier or 'DHL', track=track))}</Body><QuestionType>Shipping</QuestionType>"
            f"<RecipientID>{el._x(line['buyer'])}</RecipientID></MemberMessage>")
    return _ok(el.trading("AddMemberMessageAAQToPartner", body, token, post))


def leave_feedback(line: dict, token: str, post) -> tuple[bool, list[str]]:
    body = (f"<ItemID>{el._x(line['item_id'])}</ItemID><TransactionID>{el._x(line['txn_id'])}</TransactionID>"
            f"<TargetUser>{el._x(line['buyer'])}</TargetUser><CommentType>Positive</CommentType>"
            f"<CommentText>{el._x(FEEDBACK)}</CommentText>")
    return _ok(el.trading("LeaveFeedback", body, token, post))


def complete_sale(line: dict, track: str, carrier: str, token: str, post) -> tuple[bool, list[str]]:
    body = (f"<ItemID>{el._x(line['item_id'])}</ItemID><TransactionID>{el._x(line['txn_id'])}</TransactionID>"
            "<Shipped>true</Shipped><Shipment><ShipmentTrackingDetails>"
            f"<ShipmentTrackingNumber>{el._x(track)}</ShipmentTrackingNumber>"
            f"<ShippingCarrierUsed>{el._x(CARRIER.get(carrier or '', carrier or 'DHL'))}</ShippingCarrierUsed>"
            "</ShipmentTrackingDetails></Shipment>")
    return _ok(el.trading("CompleteSale", body, token, post))


# ----------------------------------------------------------------------------- ціна
def active_listings(token: str, post) -> list[dict]:
    root = el.trading("GetMyeBaySelling", "<ActiveList><Include>true</Include><Pagination><EntriesPerPage>100</EntriesPerPage>"
                      "<PageNumber>1</PageNumber></Pagination></ActiveList>", token, post)
    out = []
    for it in root.iter(NS + "Item"):
        if _t(it, "ItemID"):
            out.append(dict(item_id=_t(it, "ItemID"), title=_t(it, "Title"), sku=_t(it, "SKU")))
    return out


def listing_prices(item_id: str, token: str, post) -> dict:
    root = el.trading("GetItem", f"<ItemID>{el._x(item_id)}</ItemID>", token, post)
    num = lambda p: float(_t(root, p) or 0) or None   # noqa: E731
    return dict(price=num("Item/StartPrice"), accept=num("Item/ListingDetails/BestOfferAutoAcceptPrice"),
                decline=num("Item/ListingDetails/MinimumBestOfferPrice"), title=_t(root, "Item/Title"))


def new_thresholds(old: dict, price: float) -> dict:
    """Пороги Preisvorschlag — у тих самих пропорціях до ціни, що й були (і завжди нижче ціни)."""
    out = {"item": round(price)}
    if old.get("price") and old.get("accept") and old.get("decline"):
        a = min(round(price * old["accept"] / old["price"]), out["item"] - 1)
        d = min(round(price * old["decline"] / old["price"]), a - 1)
        if d > 0:
            out.update(accept=a, decline=d)
    return out


def revise_price(item_id: str, p: dict, token: str, post) -> tuple[bool, list[str]]:
    eur = lambda tag, v: f'<{tag} currencyID="EUR">{float(v):.2f}</{tag}>'   # noqa: E731
    best = (f"<ListingDetails>{eur('BestOfferAutoAcceptPrice', p['accept'])}{eur('MinimumBestOfferPrice', p['decline'])}</ListingDetails>"
            if p.get("accept") else "")
    return _ok(el.trading("ReviseFixedPriceItem", f"<Item><ItemID>{el._x(item_id)}</ItemID>{eur('StartPrice', p['item'])}{best}</Item>",
                          token, post))


# ----------------------------------------------------------------------------- питання покупця
def unanswered_questions(token: str, post) -> list[dict]:
    root = el.trading("GetMemberMessages", "<MailMessageType>AskSellerQuestion</MailMessageType><MessageStatus>Unanswered</MessageStatus>"
                      f"<StartCreationTime>{_iso(14)}</StartCreationTime><EndCreationTime>{_iso()}</EndCreationTime>", token, post)
    out = []
    for ex in root.iter(NS + "MemberMessageExchange"):
        out.append(dict(item_id=_t(ex, "Item/ItemID"), title=_t(ex, "Item/Title"), msg_id=_t(ex, "Question/MessageID"),
                        sender=_t(ex, "Question/SenderID"), body=_t(ex, "Question/Body")))
    return out


def pick_question(qs: list[dict], question: str, title: str) -> dict | None:
    if question:
        best = max(qs, key=lambda q: similar(question, q["body"]), default=None)
        if best and similar(question, best["body"]) >= 0.4:
            return best
    return pick(qs, title=title) if title else (qs[0] if len(qs) == 1 else None)


def answer(q: dict, text: str, token: str, post) -> tuple[bool, list[str]]:
    body = (f"<ItemID>{el._x(q['item_id'])}</ItemID><MemberMessage><Body>{el._x(text[:2000])}</Body>"
            "<DisplayToPublic>false</DisplayToPublic><EmailCopyToSender>true</EmailCopyToSender>"
            f"<ParentMessageID>{el._x(q['msg_id'])}</ParentMessageID><RecipientID>{el._x(q['sender'])}</RecipientID></MemberMessage>")
    return _ok(el.trading("AddMemberMessageRTQ", body, token, post))


# ----------------------------------------------------------------------------- пропозиції ціни
def pending_offers(token: str, post) -> list[dict]:
    root = el.trading("GetBestOffers", "<BestOfferStatus>Active</BestOfferStatus><DetailLevel>ReturnAll</DetailLevel>", token, post)
    out = []
    for ib in root.iter(NS + "ItemBestOffers"):
        iid, title = _t(ib, "Item/ItemID"), _t(ib, "Item/Title")
        for bo in ib.iter(NS + "BestOffer"):
            out.append(dict(item_id=iid, title=title, offer_id=_t(bo, "BestOfferID"), price=float(_t(bo, "Price") or 0),
                            buyer=_t(bo, "Buyer/UserID")))
    return out


def pick_offer(offers: list[dict], amount: float | None, title: str) -> dict | None:
    # сума відома — лише пропозиції саме на неї (інша сума = інша пропозиція, її не чіпаємо)
    c = [o for o in offers if abs(o["price"] - float(amount)) < 0.51] if amount else offers
    if len(c) == 1 and (amount or not title):
        return c[0]
    return pick(c, title=title, min_sim=0.2) if title else None


def counter_price(offer: float, lp: dict) -> int:
    """Середина між пропозицією і ціною, не нижче порогу автоприйняття, нижче ціни."""
    price = lp.get("price") or offer * 1.15
    c = max(round((offer + price) / 2), round(lp.get("accept") or 0))
    return int(min(c, round(price) - 1))


def respond_offer(o: dict, action: str, token: str, post, counter: int | None = None) -> tuple[bool, list[str]]:
    body = (f"<ItemID>{el._x(o['item_id'])}</ItemID><BestOfferID>{el._x(o['offer_id'])}</BestOfferID><Action>{action}</Action>"
            + (f'<CounterOfferPrice currencyID="EUR">{counter:.2f}</CounterOfferPrice><CounterOfferQuantity>1</CounterOfferQuantity>'
               if action == "Counter" else ""))
    return _ok(el.trading("RespondToBestOffer", body, token, post))


# ----------------------------------------------------------------------------- з бота
def _errs(errs: list[str]) -> str:
    return "\n".join(f"• {html.escape(e[:250])}" for e in errs[:3])


def run(d: dict, token: str, post, send, back, key: str) -> bool:
    """d: action + дані з Apps Script. → результат повідомленням у бот (і зворотний запис в облік, де треба)."""
    a, row, title = d.get("action"), d.get("row"), d.get("title") or ""
    what = f"№{row}" if row else f"«{html.escape(title[:50])}»"
    if a == "ship":
        orders = recent_orders(token, post)
        line = pick_order(orders, d.get("item_id") or "", row, title)
        carrier, track = d.get("carrier") or "DHL", d["track"]
        if not line:
            return send(f"📮 Трек {what} в eBay не передав: не знайшов такого продажу за 45 днів (продажів: {len(orders)}). "
                        "Введи трек в eBay вручну: Mein eBay → Verkauft → «Sendungsnummer hinzufügen».", None)
        if line["shipped"]:   # етикетка eBay: трек уже в замовленні — не дублюємо
            out = [f"✅ {what}: в eBay вже «Versendet» з треком (етикетка eBay) — нічого не дублюю."]
        else:
            ok, errs = complete_sale(line, track, carrier, token, post)
            out = [f"✅ Трек {what} передано в eBay ({html.escape(carrier)} {html.escape(track)}): «Versendet», покупець бачить відстеження."
                   if ok else f"⚠️ eBay не прийняв трек {what}:\n{_errs(errs)}\nВведи вручну: Mein eBay → Verkauft → «Sendungsnummer hinzufügen»."]
        if d.get("thanks") and line.get("buyer"):   # 10.10: коротка подяка покупцю з треком
            ok, errs = thank_buyer(line, track, carrier, token, post)
            out.append(f"💌 Покупцю {html.escape(line['buyer'])} надіслав подяку з треком." if ok
                       else f"⚠️ Подяку покупцю eBay не прийняв: {_errs(errs)}")
        return send("\n".join(out), None)
    if a == "feedback":   # 10.10: позитивний відгук покупцю, щойно продаж оплачено
        line = pick_order(recent_orders(token, post), d.get("item_id") or "", row, title)
        if not line or not line.get("buyer"):
            return send(f"⭐ Відгук покупцю {what} не залишив: не знайшов продажу — залиш в eBay сам (Mein eBay → Verkauft).", None)
        if line["feedback_left"]:
            print("відгук уже є")
            return True
        ok, errs = leave_feedback(line, token, post)
        return send(f"⭐ Відгук покупцю {html.escape(line['buyer'])} за {what} залишено." if ok
                    else f"⚠️ eBay не прийняв відгук {what}:\n{_errs(errs)}", None)
    if a == "revise":
        lst = active_listings(token, post)
        it = pick(lst, d.get("item_id") or "", row, title)
        if not it:
            return send(f"⬇️ Не знайшов на eBay активного оголошення {what} (активних: {len(lst)}). Може, вже продано або знято?", None)
        lp = listing_prices(it["item_id"], token, post)
        p = new_thresholds(lp, float(d["price"]))
        if lp.get("price") and p["item"] >= lp["price"]:
            return send(f"⬇️ {what}: на eBay вже {lp['price']:.0f} € — не вище за {p['item']} €, нічого не міняю.", None)
        ok, errs = revise_price(it["item_id"], p, token, post)
        if not ok:
            return send(f"⚠️ eBay не змінив ціну {what}:\n{_errs(errs)}", None)
        back({"kind": "repriced", "row": row, "price": p["item"], "item_id": it["item_id"]}, key)
        return send(f"⬇️ {what}: ціна на eBay {lp.get('price') or 0:.0f} → <b>{p['item']} €</b>"
                    + (f"; пропозиції: автоприйняти від {p['accept']} €, відхиляти нижче {p['decline']} €" if p.get("accept") else ""),
                    {"inline_keyboard": [[{"text": "🔗 Оголошення", "url": f"https://www.ebay.de/itm/{it['item_id']}"}]]})
    if a == "answer":
        qs = unanswered_questions(token, post)
        q = pick_question(qs, d.get("question") or "", title)
        if not q:
            return send(f"📨 Не знайшов питання без відповіді {('до ' + what) if title else ''} (таких: {len(qs)}). "
                        "Можливо, ти вже відповів в eBay.", None)
        ok, errs = answer(q, d.get("text") or "", token, post)
        return send(f"📨 Відповідь {html.escape(q['sender'])} надіслано ✅" if ok else f"⚠️ eBay не прийняв відповідь:\n{_errs(errs)}", None)
    if a == "offer":
        offers = pending_offers(token, post)
        o = pick_offer(offers, d.get("amount"), title)
        if not o:
            return send(f"🤝 Не знайшов активної пропозиції {d.get('amount') or ''} € (активних: {len(offers)}) — "
                        "мабуть, вона вже прийнята, відхилена або прострочена.", None)
        op = d.get("op")
        if op == "cnt":
            c = counter_price(o["price"], listing_prices(o["item_id"], token, post))
            ok, errs = respond_offer(o, "Counter", token, post, c)
            return send(f"🔁 Зустрічна пропозиція <b>{c} €</b> надіслана {html.escape(o['buyer'])} (він пропонував {o['price']:.0f} €)." if ok
                        else f"⚠️ eBay не прийняв зустрічну:\n{_errs(errs)}", None)
        action = {"acc": "Accept", "dec": "Decline"}.get(op)
        if not action:
            return send("⚠️ Невідома дія з пропозицією — напиши Claude.", None)
        ok, errs = respond_offer(o, action, token, post)
        return send((f"✅ Пропозицію {o['price']:.0f} € прийнято — eBay чекає оплату від {html.escape(o['buyer'])}." if action == "Accept"
                     else f"✖️ Пропозицію {o['price']:.0f} € відхилено.") if ok else f"⚠️ eBay не прийняв відповідь:\n{_errs(errs)}", None)
    return send(f"⚠️ Невідома дія eBay «{html.escape(str(a))}» — напиши Claude.", None)
