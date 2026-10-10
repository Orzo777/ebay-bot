"""eBay.de як ДРУГЕ джерело купівлі (поруч із Kleinanzeigen): ті самі типи, ті самі оцінювачі, та сама картка в Telegram.

Що робить: кожні ~3 хв бере найновіші оголошення «Sofort-Kaufen» з Німеччини в категоріях RAM і Konsolen
(Browse API, sort=newlyListed) і пропускає кожне через ram_alert.evaluate / console_alert.evaluate_console —
ті самі стелі, що й для KA. Картка йде лише на вердикти BUY*/NEGOTIATE.

Відмінності від KA:
  • повна вартість = ціна + пересилка з оголошення (на eBay.de покупець не платить збору за захист покупця);
  • «торгуйся»: з «Preisvorschlag» (BEST_OFFER) — кнопкою eBay, без нього — питанням «знизите до X €?»;
  • продавцю нічого не пишемо — «Sofort-Kaufen»; оплата через eBay, гарантія повернення грошей eBay;
  • перевірка на шахраїв: опис (getItem, 1 виклик лише для кандидата) на WhatsApp / PayPal Freunde / переказ,
    і відгуки продавця;
  • здешевлення вже існуючого оголошення теж ловимо (пам'ятаємо ціну кожного побаченого оголошення 7 днів).

Квота Browse API — 5000/день спільна: 5 запитів × 20 разів/год ≈ 2 400/день.
Запуск: python research/ebay_watch.py --state ebay_watch_state.json --minutes 55 [--every 180] [--dry-run]
"""
import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from console_alert import evaluate_console
from ka_listing_check import _CONTACT, _PAYMENT
import health
from photo_check import STATS as PHOTO_STATS
from photo_check import ebay_images, photo_line
from ram_alert import (_DESC_UNTESTED, PICKUP_COST, SEND_VERDICTS, _questions, cheap_headline, too_cheap, _speed_label, broken_reason, desc_facts,
                       evaluate, refine_by_desc, tier)

RAM_CAT, CONSOLE_CAT = "170083", "139971"
# (запит, категорія, мін. ціна, макс. ціна). Верх — трохи вище найбільшої стелі «торгуйся» серед типів групи.
QUERIES = [
    ("ddr5", RAM_CAT, 15, 480),
    ("ddr4", RAM_CAT, 15, 240),
    ("xbox series x", CONSOLE_CAT, 150, 420),
    ("(ps5, playstation 5)", CONSOLE_CAT, 150, 360),
    ("switch 2", CONSOLE_CAT, 100, 320),
]
FRESH_HOURS = 6        # старіше оголошення без здешевлення — хтось уже бачив, не сповіщаємо
KEEP_DAYS = 7
BAD_CONDITIONS = {"7000"}   # «als Ersatzteil / defekt»
HAMBURG_ZIP = re.compile(r"^(?:20|21|22)")   # API маскує індекс: «22***»
# Самовивіз у Гамбурзі (27.09): оголошення з опцією «Abholung» у радіусі 30 км — ціна + дорога, без пересилки.
# Окремі 2 запити (консолі разом, RAM разом) — через раз, щоб тримати квоту (~2 900 викликів/добу).
PICKUP_QUERIES = [
    ("(xbox series x, ps5, playstation 5, switch 2)", CONSOLE_CAT, 100, 430),
    ("(ddr5, ddr4)", RAM_CAT, 15, 440),
]
PICKUP_FILTER = ("deliveryOptions:{SELLER_ARRANGED_LOCAL_PICKUP},pickupCountry:DE,pickupPostalCode:20095,"
                 "pickupRadius:30,pickupRadiusUnit:km")
_SB_NOTE = re.compile(r"\s*(?:·\s*)?[Лл]ише «?Sicher bezahlen»?[^.!·]*[.!]?")   # поради для KA; на eBay оплата і так через eBay
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/"
CALLS = {"n": 0}


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- чиста логіка (тестується)
def clean_url(url: str | None) -> str | None:
    """10.10: itemWebUrl з API має довгий хвіст (_skw, hash, amdata) — на iPhone у браузері Telegram сторінка eBay
    почала безкінечно перезавантажуватись при «Preis vorschlagen»; коротке /itm/<номер> ще й відкриває застосунок eBay."""
    m = re.search(r"ebay\.[a-z.]+/itm/(?:[^/?#]+/)?(\d{9,15})", url or "")
    return f"https://www.ebay.de/itm/{m.group(1)}" if m else url


def listing_of(it: dict) -> dict:
    so = it.get("shippingOptions") or []
    ship = _num(((so[0] if so else {}).get("shippingCost") or {}).get("value"))
    sl = it.get("seller") or {}
    loc = it.get("itemLocation") or {}
    return dict(id=it["itemId"], title=html.unescape(it.get("title", "")), price=_num((it.get("price") or {}).get("value")),
                ship=ship, url=clean_url(it.get("itemWebUrl")), created=it.get("itemCreationDate"),
                offer=("BEST_OFFER" in (it.get("buyingOptions") or [])), cond=str(it.get("conditionId") or ""),
                seller=sl.get("username") or "", fb=int(sl.get("feedbackScore") or 0),
                pct=_num(sl.get("feedbackPercentage")), zip=str(loc.get("postalCode") or ""),
                country=loc.get("country"),
                # «pickupOptions» є й в оголошеннях з Райне чи Мюнхена (29.09: 31 з 901, жодне не з Гамбурга) — самовивіз
                # лише з гамбурзьким індексом; запит самовивозу (радіус 30 км) ставить прапорець сам у poll_once
                pickup_ok=bool(it.get("pickupOptions")) and bool(HAMBURG_ZIP.match(str(loc.get("postalCode") or ""))),
                images=ebay_images(it),
                # оголошення з варіантами (колір/модель/ємність): ціна — найдешевшого варіанта, не того, що в назві
                group=bool(it.get("itemGroupHref") or it.get("itemGroupType") or it.get("itemGroupId")),
                dist=_num((it.get("distanceFromPickupLocation") or {}).get("value")))


def evaluate_ebay(lst: dict) -> dict:
    """Оцінка тими самими функціями, що й KA, але з eBay-вартістю купівлі: ціна + пересилка, без збору KA."""
    ship, pickup = lst["ship"], False
    can_pickup = lst.get("pickup_ok") or (ship is None and HAMBURG_ZIP.match(lst["zip"]))
    if ship is None and not can_pickup:
        return dict(verdict="SKIP", reason="лише самовивіз, не в Гамбурзі", title=lst["title"], price=lst["price"])
    if can_pickup and (ship is None or PICKUP_COST < ship):   # забрати самому дешевше за пересилку
        ship, pickup = PICKUP_COST, True
    if lst["cond"] in BAD_CONDITIONS:
        return dict(verdict="SKIP", reason="стан «на запчастини / дефект»", title=lst["title"], price=lst["price"])
    if lst.get("group"):
        return dict(verdict="SKIP", reason="оголошення з варіантами — ціна найдешевшого варіанта, не того, що в назві",
                    title=lst["title"], price=lst["price"])
    broken = broken_reason(lst["title"])   # «PS5 … (Laufwerk liest keine Disks mehr)» — дефект у самій назві
    if broken:
        return dict(verdict="SKIP", reason=f"дефект у назві: «{broken}»", title=lst["title"], price=lst["price"])
    vb = lst["offer"]
    r = evaluate_console(lst["title"], lst["price"], ship, vb) or evaluate(lst["title"], lst["price"], ship, vb)
    if "net_q" not in r:   # SKIP / UNKNOWN
        return r
    cost = lst["price"] + ship
    r.update(buy_cost=cost, ship_in=ship, profit_est=r["net_q"] - cost, pickup=pickup,
             verdict=tier(cost, r["cap"], r["good"], r["excellent"], vb))
    r["notes"] = [_SB_NOTE.sub("", n).strip() for n in r.get("notes", [])]
    r["notes"] = [n for n in r["notes"] if n]
    return r


def auction_eval(lst: dict) -> dict:
    """Аукціон: поточна ставка — не ціна. «Заглушка 1 €» / «дешевше €150» (захист від шахраїв у фіксованих цінах) не
    застосовуємо (30.09): межу беремо з оцінки за умовною ціною, а вартість — за реальною ставкою."""
    r = evaluate_ebay(lst)
    if "net_q" not in r and (r.get("wrong_type") is False or "заглушка" in (r.get("reason") or "")):
        probe = evaluate_ebay(dict(lst, price=999.0))
        if "net_q" in probe:
            cost = (lst["price"] or 0) + probe["ship_in"]
            r = dict(probe, price=lst["price"] or 0, buy_cost=cost, profit_est=probe["net_q"] - cost, verdict="BUY")
    return r


def refine_ebay(r: dict, lst: dict, desc: str | None) -> dict:
    """Уточнення за описом (ноутбучна пам'ять, «4x 8GB», ціна за планку, «ich suche») з eBay-вартістю купівлі."""
    new = refine_by_desc(r, lst["title"], lst["price"], lst["offer"], desc,
                         lambda t, p, vb=False: evaluate_ebay(dict(lst, title=t, price=p)))
    if new is not r:
        new["desc"] = desc
    return new


def offer_ebay(r: dict) -> int | None:
    """Зустрічна сума: повна вартість ~10% нижче, не нижче «добре». З «Preisvorschlag» — через кнопку eBay; без нього —
    питанням продавцю (він може знизити ціну в оголошенні). Для «МОЖНА» без Preisvorschlag не торгуємось — купуй."""
    if r.get("verdict") not in ("BUY", "NEGOTIATE") or (r["verdict"] == "BUY" and not r.get("vb")):
        return None
    target = min(r["cap"], max(r["good"], r["buy_cost"] * 0.9))
    offer = int((target - r["ship_in"]) // 5 * 5)
    if r["verdict"] == "NEGOTIATE":
        offer = min(offer, int(r["price"] * 0.95 // 5 * 5))
    return offer if 0 < offer <= r["price"] * 0.95 else None


def ebay_message(r: dict, offer: int | None = None) -> str:
    """Текст продавцю через «Frage an den Verkäufer» / до Preisvorschlag (≤256 символів — ліміт кнопки копіювання).
    Як і на KA: спершу рішення, потім умови, в кінці лише питання, на які опис не відповів. Оплата — тільки через eBay."""
    what = r.get("item_acc", "den RAM")
    if offer is None:
        head = f"Hallo! Ich möchte {what} kaufen."
    elif r.get("vb"):
        head = f"Hallo! Ich nehme {what} für {offer} € – Preisvorschlag schicke ich gleich."
    else:   # Preisvorschlag вимкнений: продавець може знизити ціну в оголошенні
        head = f"Hallo! Würden Sie {what} für {offer} € verkaufen? Passen Sie den Preis an, dann kaufe ich sofort."
    terms = ("Ich hole heute oder morgen in Hamburg ab, bezahlt wird über eBay. Wann passt es Ihnen?" if r.get("pickup")
             else "Bezahlung über eBay, bitte gut verpackt und versichert versenden.")
    parts = [head, terms, *_questions(r), "Danke!"]
    while len(" ".join(parts)) > 256 and len(parts) > 3:
        parts.pop(-2)
    return " ".join(parts)


def risk_of(lst: dict, desc: str | None, quick_sale: float) -> dict:
    """Жорсткі ознаки (картки не буде) і м'які (попередження на картці)."""
    hard, soft = [], []
    if desc and _CONTACT.search(desc):
        hard.append("в описі кличе писати поза eBay (WhatsApp / телефон / e-mail)")
    if desc and _PAYMENT.search(desc):
        hard.append("в описі просить переказ / PayPal Freunde")
    if lst["fb"] == 0 and quick_sale and lst["price"] < 0.75 * quick_sale:
        hard.append("новий акаунт без відгуків і ціна нижче 75% ринку")   # 28.09: Switch 2 за €236 від 0 відгуків
    if lst["fb"] < 10:
        soft.append(f"мало відгуків ({lst['fb']})")
    if lst["pct"] is not None and lst["fb"] >= 10 and lst["pct"] < 97:
        soft.append(f"позитивних відгуків лише {lst['pct']:.0f}%")
    return dict(hard=hard, soft=soft)


def is_fresh(lst: dict, now: datetime, hours: float = FRESH_HOURS) -> bool:
    try:
        created = datetime.fromisoformat(lst["created"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return True
    return now - created <= timedelta(hours=hours)


def decide(lst: dict, state: dict, now: datetime) -> str | None:
    """Чи варто взагалі оцінювати: 'new' — нове свіже, 'drop' — здешевлення вже баченого, None — ні.
    'pickup' — уже бачене, але щойно з'ясувалось, що його можна забрати в Гамбурзі (запит самовивозу — через раз).
    Запам'ятовує ціну в state['items'] (id → [ціна, дата, самовивіз])."""
    items = state.setdefault("items", {})
    prev = items.get(lst["id"])
    total = lst["price"] + (lst["ship"] or 0)
    pickup = bool(lst.get("pickup_ok")) or bool(prev and len(prev) > 2 and prev[2])
    items[lst["id"]] = [total, now.isoformat(), pickup]
    if prev is None:
        return "new" if is_fresh(lst, now) else None
    if total < prev[0] - 0.5:
        return "drop"
    return "pickup" if lst.get("pickup_ok") and not (len(prev) > 2 and prev[2]) and is_fresh(lst, now) else None


def prune(state: dict, now: datetime, days: int = KEEP_DAYS):
    cut = (now - timedelta(days=days)).isoformat()
    state["items"] = {k: v for k, v in state.get("items", {}).items() if v[1] >= cut}


def _age_label(lst: dict, now: datetime) -> str:
    try:
        mins = (now - datetime.fromisoformat(lst["created"].replace("Z", "+00:00"))).total_seconds() / 60
    except (AttributeError, ValueError):
        return ""
    return (f"{mins:.0f} хв тому" if mins < 90 else f"{mins / 60:.0f} год тому" if mins < 48 * 60
            else f"{mins / 1440:.0f} дн. тому")


def format_card(r: dict, lst: dict, risk: dict, why: str, now: datetime) -> str:
    """10.10: коротка картка, як на KA; тексти продавцю — лише в кнопках «📋» (keyboard)."""
    from ram_alert import verdict_head
    esc = html.escape
    offer = offer_ebay(r)
    dist = f" ({lst['dist']:.0f} км)" if lst.get("dist") else ""
    ship_txt = (f"🚶 самовивіз{dist}" if r.get("pickup") else f"+ {r['ship_in']:.2f} €".replace(".", ","))
    seller = f"{esc(lst['seller'])} ({lst['fb']}" + (f", {lst['pct']:.0f}%" if lst["pct"] is not None else "") + ")"
    lines = ["🛒 <b>eBay</b> · " + verdict_head(r) + (" · 📉 ЗДЕШЕВШАЛО" if why == "drop" else "")]
    if too_cheap(r):
        lines.append(cheap_headline(r, ebay=True))
    lines += [f"<i>{esc(r['title'][:90])}</i>",
              f"{esc(r['type'])} · <b>{lst['price']:.0f} €</b> {ship_txt} → разом {r['buy_cost']:.0f} €"
              + (" · Preisvorschlag ✓" if lst["offer"] else ""),
              *([(f"🤝 Preisvorschlag <b>{offer} €</b>" if lst["offer"] else f"🤝 Напиши продавцю: <b>{offer} €</b>")
                 + f" → заробіток ≈ <b>{r['net_q'] - offer - r['ship_in']:.0f} €</b>"] if offer is not None else []),
              f"📏 Бери до {r['cap']:.0f} € разом · вигідно ≤ {r['good']:.0f} €",
              f"🏷 Продаси за {r['quick_sale']}–{r['median_sale']} € · {_speed_label(r['sell_through']).replace('продається ', '')}",
              f"👤 {seller}" + (f" · {_age_label(lst, now)}" if _age_label(lst, now) else "")]
    lines += [f"⚠️ {esc(s)}" for s in risk["soft"]]
    f = desc_facts(r.get("desc"), r)
    if f["works"] is False:
        lines.append("⚠️ В описі: НЕ тестоване або з дефектом")
    if r.get("single_module_warning") and not f["kit_ok"]:
        lines.append(f"⚠️ Перевір, що це <b>одна</b> планка на {r['total']} ГБ")
    elif r.get("kit_unknown") and not f["kit_ok"]:
        lines.append(f"⚠️ Скільки планок — не вказано: бери лише <b>2×{r['total'] // 2} ГБ</b>")
    lines += [f"⚠️ {esc(n)}" for n in r.get("notes", [])]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- мережа
def _client():
    from main import EbayClient
    return EbayClient()


def fetch_new(client, q: str, cat: str, lo: int, hi: int, pickup: bool = False) -> list[dict]:
    from main import _request_with_backoff
    flt = f"buyingOptions:{{FIXED_PRICE}},itemLocationCountry:DE,price:[{lo}..{hi}],priceCurrency:EUR"
    params = {"q": q, "category_ids": cat, "sort": "newlyListed", "limit": 100,
              "filter": flt + ("," + PICKUP_FILTER if pickup else "")}
    CALLS["n"] += 1
    d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=client._headers(), params=params)
    return d.get("itemSummaries") or []


def fetch_desc(client, item_id: str) -> str | None:
    from main import _request_with_backoff
    CALLS["n"] += 1
    try:
        d = _request_with_backoff("GET", ITEM_URL + item_id, headers=client._headers(), params={})
    except Exception:
        return None
    raw = d.get("description") or d.get("shortDescription") or ""
    raw = re.sub(r"(?is)<(style|script)\b.*?</\1\s*>", " ", raw)   # шаблони продавців: десятки КБ CSS перед текстом
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text).strip()


def keyboard(url: str, r: dict) -> dict:
    offer = offer_ebay(r)
    if offer is not None and r.get("verdict") == "NEGOTIATE":   # «торгуйся»: одна кнопка, одразу з пропозицією
        return {"inline_keyboard": [[{"text": "🔗 Відкрити на eBay", "url": url}],
                                    [{"text": f"📋 Текст із пропозицією {offer} €",
                                      "copy_text": {"text": ebay_message(r, offer)[:256]}}]]}
    rows = [[{"text": "🔗 Відкрити на eBay", "url": url}],
            [{"text": "📋 Скопіювати текст продавцю", "copy_text": {"text": ebay_message(r)[:256]}}]]
    if offer is not None:
        rows.append([{"text": f"📋 Текст із пропозицією {offer} €", "copy_text": {"text": ebay_message(r, offer)[:256]}}])
    return {"inline_keyboard": rows}


def send_card(text: str, url: str, r: dict, auction: bool = False):
    """→ (message_id, reply_markup) — щоб потім дописати рядок «📷 …». Аукціон — лише кнопка «відкрити» (ставку робиш сам)."""
    import requests
    kb = {"inline_keyboard": [[{"text": "🔗 Відкрити на eBay", "url": url}]]} if auction else keyboard(url, r)
    import quiet   # 09.10: уночі «можна» — без звуку; «бери» і аукціони — зі звуком
    data = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true",
            "reply_markup": json.dumps(kb, ensure_ascii=False),
            "disable_notification": "true" if not auction and quiet.silent(r.get("verdict")) else "false"}
    api = f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(api, data=data, timeout=15)
    if resp.status_code == 400:   # старий клієнт без copy_text — картка важливіша за кнопку
        data["reply_markup"] = json.dumps({"inline_keyboard": kb["inline_keyboard"][:1]}, ensure_ascii=False)
        resp = requests.post(api, data=data, timeout=15)
    resp.raise_for_status()
    return (resp.json().get("result") or {}).get("message_id"), data["reply_markup"]


# ----------------------------------------------------------------------------- «поділитися → бот» для eBay (28.09)
_EBAY_ID = re.compile(r"ebay\.[a-z.]+/itm/(?:[^/\s?]+/)?(\d{9,14})")
_EBAY_SHORT = re.compile(r"https?://(?:www\.)?ebay\.(?:us|to)/\S+")


def ebay_item_id(text: str) -> str | None:
    """Номер оголошення з посилання eBay; коротке посилання із застосунку (ebay.us/…) розкриваємо редиректом."""
    m = _EBAY_ID.search(text or "")
    if m:
        return m.group(1)
    s = _EBAY_SHORT.search(text or "")
    if s:
        import requests
        try:
            final = requests.get(s.group(0), timeout=10, allow_redirects=True).url
        except Exception:
            return None
        m = _EBAY_ID.search(final)
        return m.group(1) if m else None
    return None


try:
    from zoneinfo import ZoneInfo
    _BERLIN = ZoneInfo("Europe/Berlin")
except Exception:   # Windows без tzdata
    _BERLIN = timezone(timedelta(hours=2))


def auction_lines(r: dict, it: dict, now: datetime) -> list[str]:
    """Аукціон: показана ціна — поточна ставка, не кінцева. Радимо максимальну ставку (eBay сам підніматиме до неї)."""
    bids = it.get("bidCount") or 0
    try:
        end = datetime.fromisoformat(it["itemEndDate"].replace("Z", "+00:00"))
        left = end - now
        left_txt = (f"{left.days} дн. {left.seconds // 3600} год" if left.days else
                    f"{left.seconds // 3600} год {left.seconds % 3600 // 60} хв" if left.seconds >= 3600 else
                    f"<b>{left.seconds // 60} хв</b>")
        left_txt += f" (о {end.astimezone(_BERLIN):%H:%M})"
    except (KeyError, ValueError, TypeError):
        left, left_txt = None, "?"
    max_bid = int((r["cap"] - r["ship_in"]) // 1)
    good_bid = int((r["good"] - r["ship_in"]) // 1)
    lines = [f"🔨 <b>Аукціон</b>: зараз {r['price']:.0f} € ({bids} ставок), до кінця {left_txt}."]
    if r["price"] >= max_bid:
        lines.append(f"⛔ Уже дорожче вигідного: максимум для нас {max_bid} € (з пересилкою {r['cap']:.0f} €).")
    else:
        if left is not None and left <= timedelta(hours=1):
            lines += [f"👉 За 1–2 хв до кінця постав <b>максимальну ставку {max_bid} €</b> (краще {good_bid} €).",
                      f"Виграєш за {max_bid} € → заробіток ≈ {r['net_q'] - r['cap']:.0f} €."]
        else:
            lines += [f"👉 Постав <b>максимальну ставку {max_bid} €</b> (краще {good_bid} €) в останні хвилини — "
                      f"бот нагадає за ~20 хв до кінця.",
                      f"Виграєш за {max_bid} € → заробіток ≈ {r['net_q'] - r['cap']:.0f} €."]
    return lines


# 06.10 (прохання користувача): картка аукціону «жива». Раніше вона показувала ставку на момент надсилання, а коли
# користувач відкривав eBay, там уже було дорожче рекомендованого. Тепер щокола (~3 хв) бот оновлює ту саму картку:
# поточна ставка, скільки лишилось, чи ще вигідно; після кінця — «🏁 завершено, фінальна ставка». Один запит API
# на активний аукціон за коло (картка йде за 2–20 хв до кінця → ~7 запитів на аукціон).
LIVE_AFTER_END = timedelta(minutes=10)   # після кінця ще пробуємо забрати фінальну ставку, потім забуваємо


def auction_card(it: dict, r: dict, head: str, tail: str) -> dict:
    return {"head": head, "tail": tail, "end": it.get("itemEndDate"), "price": float(r["price"]), "type": r.get("type", ""),
            "bids": int(it.get("bidCount") or 0), "r": {k: r[k] for k in ("cap", "ship_in", "good", "net_q")}}


def live_text(c: dict, now: datetime) -> str:
    """Текст картки аукціону на зараз: «🟢 іде» + ставка/час/порада або «🏁 завершено» з фінальною ставкою."""
    r = dict(c["r"], price=c["price"])
    max_bid = int((r["cap"] - r["ship_in"]) // 1)
    try:
        end = datetime.fromisoformat(c["end"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        end = None
    if end is not None and now >= end:
        body = [f"🏁 <b>Аукціон завершено</b> о {end.astimezone(_BERLIN):%H:%M}: фінальна ставка {c['price']:.0f} € "
                f"({c['bids']} ставок).",
                "Ставка в межах нашого максимуму — якщо виграв, облік запише покупку з листа eBay." if c["price"] <= max_bid
                else f"Пішло дорожче за наш максимум {max_bid} € — пропускаємо."]
    else:
        body = [f"🟢 <b>Іде</b> · оновлено о {now.astimezone(_BERLIN):%H:%M}",
                *auction_lines(r, {"bidCount": c["bids"], "itemEndDate": c["end"]}, now)]
    return "\n".join([c["head"], *body, c["tail"]]).strip()


def edit_card(message_id: int, text: str, markup: str | None):
    import requests
    data = {"chat_id": config.TELEGRAM_CHAT_ID, "message_id": message_id, "text": text[:4096], "parse_mode": "HTML",
            "disable_web_page_preview": "true"}
    if markup:
        data["reply_markup"] = markup
    try:   # 400 «message is not modified» / картку видалили — не біда
        requests.post(f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/editMessageText", data=data, timeout=15)
    except Exception:
        pass


def update_auction_cards(client, state: dict, now: datetime) -> int:
    """Щокола: свіжа ставка й час для кожної надісланої картки аукціону; після кінця — фінал і забуваємо."""
    from main import _request_with_backoff
    cards = state.get("auction_cards") or {}
    n = 0
    for iid, c in list(cards.items()):
        try:
            end = datetime.fromisoformat(c["end"].replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            cards.pop(iid, None)
            continue
        if now > end + LIVE_AFTER_END:
            cards.pop(iid, None)
            continue
        try:
            CALLS["n"] += 1
            it = _request_with_backoff("GET", ITEM_URL + iid, headers=client._headers(), params={})
        except Exception:
            it = None   # після кінця Browse API часто віддає 404 — лишається остання відома ставка
        if it:
            bid = (it.get("currentBidPrice") or it.get("price") or {}).get("value")
            if bid:
                c["price"] = float(bid)
            c["bids"] = int(it.get("bidCount") or c["bids"])
            c["end"] = it.get("itemEndDate") or c["end"]
        if c.get("m"):
            edit_card(c["m"], live_text(c, now), c.get("kb"))
            n += 1
        if now >= datetime.fromisoformat(c["end"].replace("Z", "+00:00")):
            # 09.10: фінал аукціону — для тижневого звіту (чи реалістичні наші максимальні ставки)
            mx = int((c["r"]["cap"] - c["r"]["ship_in"]) // 1)
            res = state.setdefault("auction_results", [])
            res.append({"d": now.date().isoformat(), "final": round(c["price"]), "max": mx, "type": c.get("type", "")})
            del res[:-200]
            cards.pop(iid, None)   # фінал показали
    return n


def share_ebay(item_id: str, client=None, now: datetime | None = None) -> tuple[str, dict | None, dict | None]:
    """Оцінка одного оголошення eBay (посилання, яким поділились із ботом) → (html, результат, оголошення)."""
    from main import _request_with_backoff
    now = now or datetime.now(timezone.utc)
    client = client or _client()
    try:
        it = _request_with_backoff("GET", ITEM_URL + "get_item_by_legacy_id", headers=client._headers(),
                                   params={"legacy_item_id": item_id})
    except Exception as e:
        return f"⚫ Оголошення eBay не відкривається ({e.__class__.__name__}) — можливо, його вже зняли.", None, None
    auction = "AUCTION" in (it.get("buyingOptions") or []) and "FIXED_PRICE" not in (it.get("buyingOptions") or [])
    if auction:   # ціна аукціону — поточна ставка
        it = dict(it, price=it.get("currentBidPrice") or it.get("price") or {"value": "0"})
    lst = listing_of(it)
    r = auction_eval(lst) if auction else evaluate_ebay(lst)
    esc = html.escape
    if "net_q" not in r:
        return (f"⏭ <b>Не бери</b> · <i>{esc(lst['title'][:90])}</i> — {lst['price'] or 0:.0f} €\n"
                f"{esc(r.get('reason', ''))}"), r, lst
    desc = fetch_desc(client, lst["id"])
    r["desc"] = desc
    broken = broken_reason(desc)
    risk = risk_of(lst, desc, r.get("quick_sale", 0))
    # аукціон: уточнення за описом (4×8 замість 2×16 тощо) — за умовною ціною; відкидаємо лише інший тип товару
    new = refine_ebay(r, dict(lst, price=999.0), desc) if auction else refine_ebay(r, lst, desc)
    if auction and new is not r and not new.get("wrong_type"):
        new = r
    if new is not r and (auction or new["verdict"] not in SEND_VERDICTS):
        return (f"⏭ <b>Не бери</b> · <i>{esc(lst['title'][:90])}</i> — {lst['price'] or 0:.0f} €\n"
                f"За описом: {esc(new.get('refined', ''))} — {esc(new.get('reason') or 'не вигідно')}"), new, lst
    r = new
    if broken:
        return f"⛔ <b>Не бери — в описі дефект</b> · <i>{esc(lst['title'][:90])}</i>\n   • «{esc(broken)}»", r, lst
    if risk["hard"]:
        return ("⛔ <b>Не бери — схоже на шахрая</b> · <i>" + esc(lst["title"][:90]) + "</i>\n"
                + "\n".join("   • " + esc(h) for h in risk["hard"])), r, lst
    if auction:
        head = [f"🛒 <b>eBay</b> · <b>{esc(r['type'])}</b>", f"<i>{esc(lst['title'][:90])}</i>", "",
                *auction_lines(r, it, now), f"🏷 Продати: {r['quick_sale']}–{r['median_sale']} €",
                f"👤 Продавець {esc(lst['seller'])} ({lst['fb']})", *[f"⚠️ {esc(s)}" for s in risk["soft"]],
                *(["⚠️ Продавець пише, що НЕ тестовано — ставку роби з поправкою на ризик"]
                  if desc and _DESC_UNTESTED.search(desc) else [])]
        return "\n".join(head), dict(r, verdict="AUCTION"), lst
    if r["verdict"] not in SEND_VERDICTS:
        return (f"⏭ <b>Не бери</b> · <i>{esc(lst['title'][:90])}</i> — {lst['price']:.0f} € + пересилка "
                f"{lst['ship'] or 0:.2f} €\nВигідно лише до {r['cap']:.0f} € разом з пересилкою "
                f"(продається за {r['quick_sale']}–{r['median_sale']} €)."), r, lst
    return format_card(r, lst, risk, "share", now), r, lst


# Аукціони (28.09): поточна ставка — не ціна. Сповіщаємо ОДИН раз, коли до кінця ≤ 3 год, а ставка ще нижча за нашу
# межу: користувач ставить максимальну ставку й забуває (eBay сам торгується за нього до цієї суми).
# 29.09 (прохання користувача): сповіщення в ОСТАННІ хвилини — ставка вже майже кінцева, а пізня ставка не провокує
# торг (конкуренти не встигають перебити). Вікно 20 хв > інтервал аукціонних запитів (~6 хв, через коло) + пауза між запусками Actions (1–3 хв):
# гарантовано встигаємо хоча б раз. Раніше 2 хв — уже пізно (Telegram + відкрити eBay + поставити ставку).
# Усі наші категорії: RAM DDR4/DDR5, Xbox Series X, PS5, Switch 2 (першу Switch не купуємо з 04.10).
AUCTION_QUERIES = [("(ddr5, ddr4)", RAM_CAT, 1, 480),
                   ("(xbox series x, ps5, playstation 5, switch 2)", CONSOLE_CAT, 1, 420)]
AUCTION_MINUTES = (2, 20)


def fetch_auctions(client, q: str, cat: str, lo: int, hi: int) -> list[dict]:
    from main import _request_with_backoff
    params = {"q": q, "category_ids": cat, "sort": "endingSoonest", "limit": 100,
              "filter": f"buyingOptions:{{AUCTION}},itemLocationCountry:DE,price:[{lo}..{hi}],priceCurrency:EUR"}
    CALLS["n"] += 1
    d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=client._headers(), params=params)
    return d.get("itemSummaries") or []


def auction_candidate(it: dict, state: dict, now: datetime) -> tuple[dict, dict] | None:
    """Аукціон, що закінчується за AUCTION_MINUTES і досі дешевший за нашу максимальну ставку (раз на оголошення)."""
    done = state.setdefault("auctions", {})
    if it.get("itemId") in done:
        return None
    try:
        end = datetime.fromisoformat(it["itemEndDate"].replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError):
        return None
    if not (timedelta(minutes=AUCTION_MINUTES[0]) <= end - now <= timedelta(minutes=AUCTION_MINUTES[1])):
        return None
    it = dict(it, price=it.get("currentBidPrice") or it.get("price"))
    lst = listing_of(it)
    if lst["price"] is None or lst["country"] not in (None, "DE"):
        return None
    r = auction_eval(lst)
    # На останніх хвилинах запас не потрібен: максимальна ставка = межа; переб'ють — нічого не втрачаєш (30.09; було 0.9×)
    if "net_q" not in r or lst["price"] + r["ship_in"] >= r["cap"]:
        return None
    done[it["itemId"]] = now.isoformat()
    return lst, r


def poll_once(client, state: dict, dry_run: bool, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    first_run = not state.get("items")
    sent = auctions_sent = api_errors = 0
    calls0, photo0 = CALLS["n"], dict(PHOTO_STATS)
    state["round"] = state.get("round", 0) + 1
    jobs = [(q, False) for q in QUERIES] + ([(q, True) for q in PICKUP_QUERIES] if state["round"] % 2 else [])
    merged = {}   # одне оголошення може прийти з обох запитів — самовивіз «прилипає»
    for (q, cat, lo, hi), pickup in jobs:
        try:
            raw = fetch_new(client, q, cat, lo, hi, pickup)
        except Exception as e:
            print(f"   [{q}] помилка API: {e}")
            api_errors += 1
            quota_error(state, e, now)
            continue
        for it in raw:
            lst = listing_of(it)
            lst["pickup_ok"] = lst["pickup_ok"] or pickup
            if lst["id"] in merged:
                merged[lst["id"]]["pickup_ok"] |= lst["pickup_ok"]
            else:
                merged[lst["id"]] = lst
    for lst in merged.values():
        if lst["price"] is None or lst["country"] not in (None, "DE"):
            continue
        why = decide(lst, state, now)
        if first_run or why is None:   # перший запуск лише запам'ятовує, що вже висить
            continue
        r = evaluate_ebay(lst)
        print(f" - [{why}] {lst['title'][:70]} | {lst['price']:.0f}+{lst['ship'] or 0:.0f}€ -> {r['verdict']}"
              + (f" ({r['reason']})" if r.get("reason") else ""))
        if r["verdict"] not in SEND_VERDICTS:
            continue
        desc = fetch_desc(client, lst["id"])
        r["desc"] = desc
        r = refine_ebay(r, lst, desc)
        if r["verdict"] not in SEND_VERDICTS:
            print(f"   за описом: {r['verdict']} ({r.get('refined', '')}; {r.get('reason') or ''})")
            continue
        broken = broken_reason(desc)
        if broken:   # несправне не купуємо (27.09)
            print(f"   ДЕФЕКТ в описі — картку не надсилаю: «{broken}»")
            continue
        risk = risk_of(lst, desc, r.get("quick_sale", 0))
        if risk["hard"]:
            print("   ШАХРАЙ — картку не надсилаю: " + "; ".join(risk["hard"]))
            continue
        if dry_run:
            print("   [DRY RUN] надіслав би картку")
        else:
            card = format_card(r, lst, risk, why, now)
            ph = photo_line(r, lst["title"], lst.get("images") or [])
            if ph and ph[1]:
                print("   ФОТО НЕ ЗБІГАЄТЬСЯ — картку не надсилаю: " + re.sub(r"<[^>]+>", "", ph[0]).replace("\n", " ")[:200])
                continue
            send_card(card + ("\n\n" + ph[0] if ph else ""), lst["url"], r)
        sent += 1
    if state["round"] % 2 == 0:   # аукціони — через коло (~6 хв; квота API): у вікно 20 хв потрапляємо щонайменше двічі
        for q, cat, lo, hi in AUCTION_QUERIES:
            try:
                raw = fetch_auctions(client, q, cat, lo, hi)
            except Exception as e:
                print(f"   [аукціони {q}] помилка API: {e}")
                api_errors += 1
                quota_error(state, e, now)
                continue
            for it in raw:
                cand = auction_candidate(it, state, now)
                if not cand:
                    continue
                lst, r = cand
                desc = fetch_desc(client, lst["id"])
                r["desc"] = desc
                r2 = refine_ebay(r, lst, desc)
                if r2 is not r and r2["verdict"] not in SEND_VERDICTS:
                    print(f" - [аукціон] {lst['title'][:70]} — за описом не те: {r2.get('refined', '')}")
                    continue
                r = r2
                risk = risk_of(lst, desc, r.get("quick_sale", 0))
                print(f" - [аукціон] {lst['title'][:70]} | ставка {lst['price']:.0f}€ -> макс. {r['cap'] - r['ship_in']:.0f}€")
                if broken_reason(desc) or risk["hard"] or (desc and _DESC_UNTESTED.search(desc)):
                    print("   дефект / шахрай / не тестовано — пропускаю")
                    continue
                esc = html.escape
                head = "\n".join([f"🛒 <b>eBay</b> · <b>{esc(r['type'])}</b>", f"<i>{esc(lst['title'][:90])}</i>", ""])
                tail = "\n".join([f"🏷 Продати: {r['quick_sale']}–{r['median_sale']} €",
                                  f"👤 Продавець {esc(lst['seller'])} ({lst['fb']})", *[f"⚠️ {esc(s)}" for s in risk["soft"]]])
                card = auction_card(it, r, head, tail)
                text = live_text(card, now)
                if dry_run:
                    print("   [DRY RUN] надіслав би картку аукціону")
                else:
                    # фото — ДО надсилання, як для «Sofort-Kaufen» (01.10: гра «EA Sports FC 26 - Switch 2» пішла з ⛔ у картці)
                    ph = photo_line(r, lst["title"], lst.get("images") or [])
                    if ph and ph[1]:
                        print("   ФОТО НЕ ЗБІГАЄТЬСЯ — картку аукціону не надсилаю")
                        continue
                    card["tail"] += "\n\n" + ph[0] if ph else ""
                    mid, kb = send_card(live_text(card, now), lst["url"], dict(r, verdict="BUY"), auction=True)
                    state.setdefault("auction_cards", {})[it["itemId"]] = dict(card, m=mid, kb=kb)   # далі — живі оновлення
                auctions_sent += 1
        cut = (now - timedelta(days=2)).isoformat()
        state["auctions"] = {k: v for k, v in state.get("auctions", {}).items() if v >= cut}
    if state.get("auction_cards") and not dry_run:
        try:
            update_auction_cards(client, state, now)
        except Exception as e:
            print(f"   [аукціони] оновлення карток: {e.__class__.__name__}")
    if first_run:
        print(f"   перший запуск: запам'ятав {len(state.get('items', {}))} оголошень, сповіщення — з наступного кругу")
    prune(state, now)
    track_round(state, len(jobs), api_errors, sent, auctions_sent, CALLS["n"] - calls0, photo0, now)
    if state["round"] % 20 == 0 and not dry_run:   # ~раз на годину
        ka_dispatch_check(state, now)
        price_refresh_check(state, now)
    return sent + auctions_sent


def _last_ka_dispatch() -> datetime | None:
    """Коли Apps Script востаннє запускав KA-пошту (workflow_dispatch ram_mail_alert.yml)."""
    import requests
    tok = os.getenv("GH_API_TOKEN")
    # БЕЗ фільтра event=…: відфільтровані запити GitHub бере з пошукового індексу, що відстає на години
    # (02.10: о 12:22 повернув запуск 5-годинної давнини, хоча останній був хвилину тому → хибна тривога)
    r = requests.get(f"https://api.github.com/repos/{os.getenv('GITHUB_REPOSITORY', 'Orzo777/ebay-bot')}"
                     "/actions/workflows/ram_mail_alert.yml/runs", params={"per_page": 50},
                     headers={"Authorization": f"Bearer {tok}"} if tok else {}, timeout=15)
    if r.status_code != 200:
        return None
    runs = [x for x in r.json().get("workflow_runs") or [] if x.get("event") == "workflow_dispatch"]
    if not runs:
        return None   # 50 запусків поспіль за розкладом — не знаємо; краще промовчати, ніж збрехати
    return max(datetime.fromisoformat(x["created_at"].replace("Z", "+00:00")) for x in runs)


def _prices_updated() -> str:
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ram_prices.json"), encoding="utf-8") as fh:
            return json.load(fh).get("updated", "")
    except (OSError, ValueError):
        return ""


def _dispatch(workflow: str) -> int:
    import requests
    tok = os.getenv("GH_API_TOKEN")
    if not tok:
        return 0
    r = requests.post(f"https://api.github.com/repos/{os.getenv('GITHUB_REPOSITORY', 'Orzo777/ebay-bot')}/actions/workflows/"
                      f"{workflow}/dispatches", json={"ref": "main"}, timeout=15,
                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
    return r.status_code


def price_refresh_check(state: dict, now: datetime, updated_fn=_prices_updated, dispatch=_dispatch):
    """Сторож цін — раз на добу (cron GitHub запізнюється на години): після 5:00 UTC, якщо ціни сьогодні ще не оновлено."""
    today = now.strftime("%Y-%m-%d")
    if now.hour < 5 or updated_fn() >= today or state.get("refresh_dispatched") == today:
        return False
    code = dispatch("price_refresh.yml")
    print(f"   [сторож цін] запуск: {code}")
    if code == 204:
        state["refresh_dispatched"] = today
    return code == 204


def ka_dispatch_check(state: dict, now: datetime, send=health.office_send, last_fn=_last_ka_dispatch):
    """Незалежно від Apps Script: вдень >3 год без запуску KA-пошти від Apps Script — сигнал (01.10: Gmail-ліміт
    Google вимкнув check() з 5:00 до вечора, сторож в Apps Script теж не зміг попередити)."""
    try:
        last = last_fn()
    except Exception as e:
        print(f"   [сторож] GitHub API: {e.__class__.__name__}")
        return
    if last is None or not 9 <= now.astimezone(_BERLIN).hour < 23:
        return
    hours = (now - last).total_seconds() / 3600
    seen = state.get("ka_gap_seen", 0) + 1 if hours > 3 else 0
    state["ka_gap_seen"] = seen
    if seen >= 2:   # підтверджено дві перевірки поспіль (~2 год), а не один дивний відповідь API
        health.alert(state, "ka_dispatch", f"⛔ Apps Script уже {hours:.0f} год не запускав обробку листів Kleinanzeigen. "
                     "Картки з KA приходитимуть пачками із запізненням (запасний запуск GitHub — раз на кілька годин). "
                     "Apps Script → «Виконання»: чи працює check і чи немає помилок.", 6, now, send)


def quota_error(state: dict, e: Exception, now: datetime, send=health.office_send):
    """Денна квота Browse API (5 000) закінчилась — жоден пошук не пройде до півночі за Тихоокеанським часом."""
    if re.search(r"429|quota|exceeded|too many", str(e), re.I):
        health.alert(state, "ebay_quota", "🛑 eBay API: закінчилась денна квота запитів (5 000). eBay-сторож і оцінка "
                     "eBay-посилань не працюють до ~9:00 за Берліном; Kleinanzeigen працює як звичайно. "
                     "Якщо повторюється щодня — скажи, зменшимо частоту перевірок.", 12, now, send)


def track_round(state: dict, n_jobs: int, api_errors: int, cards: int, auctions: int, calls: int, photo0: dict,
                now: datetime, send=health.office_send):
    """Лічильники для щоденного звіту + сповіщення, якщо eBay API не відповідає 3 кола поспіль (~10 хв)."""
    health.bump(state, "ebay_rounds", 1, now)
    health.bump(state, "ebay_calls", calls, now)
    health.bump(state, "ebay_api_errors", api_errors, now)
    health.bump(state, "ebay_cards", cards, now)
    health.bump(state, "ebay_auctions", auctions, now)
    health.track_photo(state, photo0, PHOTO_STATS, now, send)
    state["api_down_run"] = state.get("api_down_run", 0) + 1 if n_jobs and api_errors >= n_jobs else 0
    if state["api_down_run"] >= 3:
        health.alert(state, "ebay_api", "⚠️ eBay API не відповідає вже " + str(state["api_down_run"]) + " кола поспіль "
                     "(~10 хв). Нові оголошення eBay зараз не перевіряються. Якщо це збій eBay — мине саме; "
                     "якщо триває годинами — перевір секрети EBAY_APP_ID / EBAY_CERT_ID.", 6, now, send)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="ebay_watch_state.json")
    ap.add_argument("--minutes", type=float, default=0, help="крутитись стільки хвилин (0 — один круг)")
    ap.add_argument("--every", type=float, default=float(os.getenv("EBAY_WATCH_EVERY", "180")))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        state = json.load(open(a.state, encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    client = _client()
    end = time.time() + a.minutes * 60
    rounds = sent = 0
    while True:
        t0 = time.time()
        sent += poll_once(client, state, a.dry_run)
        rounds += 1
        with open(a.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        if time.time() + a.every > end:
            break
        time.sleep(max(5.0, a.every - (time.time() - t0)))
    print(f"кругів {rounds}, карток {sent}, викликів API {CALLS['n']}, у пам'яті {len(state.get('items', {}))} оголошень")


if __name__ == "__main__":
    main()
