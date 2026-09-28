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
from ram_alert import PICKUP_COST, SEND_VERDICTS, _questions, cheap_headline, too_cheap, _speed_label, broken_reason, desc_facts, evaluate, tier

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
_SB_NOTE = re.compile(r"\s*Лише «?Sicher bezahlen»?[^.!]*[.!]")   # поради для KA; на eBay оплата і так через eBay
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/"
CALLS = {"n": 0}


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- чиста логіка (тестується)
def listing_of(it: dict) -> dict:
    so = it.get("shippingOptions") or []
    ship = _num(((so[0] if so else {}).get("shippingCost") or {}).get("value"))
    sl = it.get("seller") or {}
    loc = it.get("itemLocation") or {}
    return dict(id=it["itemId"], title=html.unescape(it.get("title", "")), price=_num((it.get("price") or {}).get("value")),
                ship=ship, url=it.get("itemWebUrl"), created=it.get("itemCreationDate"),
                offer=("BEST_OFFER" in (it.get("buyingOptions") or [])), cond=str(it.get("conditionId") or ""),
                seller=sl.get("username") or "", fb=int(sl.get("feedbackScore") or 0),
                pct=_num(sl.get("feedbackPercentage")), zip=str(loc.get("postalCode") or ""),
                country=loc.get("country"), pickup_ok=bool(it.get("pickupOptions")),
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
    return f"{mins:.0f} хв тому" if mins < 90 else f"{mins / 60:.0f} год тому"


def format_card(r: dict, lst: dict, risk: dict, why: str, now: datetime) -> str:
    esc = html.escape
    tag = {"BUY-EXCELLENT": "🟢🟢 <b>ВІДМІННО — КУПУЙ</b>", "BUY-GOOD": "🟢 <b>ДОБРЕ — КУПУЙ</b>",
           "BUY": "🟡 <b>МОЖНА, але маржа тонка</b>",
           "NEGOTIATE": "💬 <b>ЗАПРОПОНУЙ ЦІНУ — трохи дорожче стелі</b>"}[r["verdict"]]
    if too_cheap(r):
        tag = cheap_headline(r, ebay=True)
    offer = offer_ebay(r)
    dist = f" ({lst['dist']:.0f} км)" if lst.get("dist") else ""
    ship_txt = (f"🚶 самовивіз у Гамбурзі{dist}, дорога ≈ {PICKUP_COST:.0f} €" if r.get("pickup") else f"пересилка {r['ship_in']:.2f} €".replace(".", ","))
    seller = f"{esc(lst['seller'])} ({lst['fb']}" + (f", {lst['pct']:.0f}%" if lst["pct"] is not None else "") + ")"
    lines = [
        "🛒 <b>eBay</b> · " + tag + (" · 📉 ЗДЕШЕВШАЛО" if why == "drop" else ""),
        f"<b>{esc(r['type'])}</b>",
        f"<b>{lst['price']:.0f} €</b> + {ship_txt} = <b>{r['buy_cost']:.0f} €</b>"
        + (" · Preisvorschlag можна" if lst["offer"] else " · фіксована ціна"),
        "",
        (f"💶 Заробіток ≈ <b>{r['profit_est']:.0f} €</b>" if r["verdict"] != "NEGOTIATE"
         else f"💶 За поточною ціною ≈ {r['profit_est']:.0f} € (маржа нижча за 30%)"),
        *([(f"🤝 Preisvorschlag <b>{offer} €</b>" if lst["offer"] else f"🤝 Напиши продавцю: <b>{offer} €</b> "
            "(Preisvorschlag вимкнений — хай знизить ціну)") + f" (разом ≈ {offer + r['ship_in']:.0f} €) → заробіток ≈ "
           f"<b>{r['net_q'] - offer - r['ship_in']:.0f} €</b>"] if offer is not None else []),
        f"🛒 Разом з пересилкою до {r['cap']:.0f} € (добре ≤ {r['good']:.0f}, супер ≤ {r['excellent']:.0f})",
        f"🏷 Продати: {r['quick_sale']}–{r['median_sale']} €",
        f"⏱ {_speed_label(r['sell_through'])}",
        f"👤 Продавець {seller}" + (f" · виставлено {_age_label(lst, now)}" if _age_label(lst, now) else ""),
    ]
    for s in risk["soft"]:
        lines.append(f"⚠️ {esc(s)}")
    f = desc_facts(r.get("desc"), r)
    if f["works"] is False:
        lines.append("⚠️ В описі: НЕ тестоване або з дефектом")
    if r.get("single_module_warning") and not f["kit_ok"]:
        lines.append(f"⚠️ Перевір на фото/в описі, що це <b>одна</b> планка на {r['total']} ГБ.")
    elif r.get("kit_unknown") and not f["kit_ok"]:
        lines.append(f"⚠️ Кількість планок не вказана — бери лише якщо це <b>2×{r['total'] // 2} ГБ</b>.")
    for n in r.get("notes", []):
        lines.append(f"⚠️ {esc(n)}")
    lines += ["", f"<i>{esc(r['title'][:90])}</i>",
              "Оплата лише через eBay (гарантія повернення грошей). Не пиши продавцю поза eBay.",
              "", "✉️ Текст продавцю («Frage an den Verkäufer», натисни — скопіюється):",
              f"<code>{esc(ebay_message(r))}</code>"]
    if offer is not None:
        lines += ["", f"✉️ З пропозицією {offer} €" + (" (потім «Preisvorschlag senden»):" if lst["offer"]
                                                       else " (питання продавцю, потім купуй за новою ціною):"),
                  f"<code>{esc(ebay_message(r, offer))}</code>"]
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
    text = html.unescape(re.sub(r"<[^>]+>", " ", d.get("description") or d.get("shortDescription") or ""))
    return re.sub(r"\s+", " ", text).strip()


def keyboard(url: str, r: dict) -> dict:
    rows = [[{"text": "🔗 Відкрити на eBay", "url": url}],
            [{"text": "📋 Скопіювати текст продавцю", "copy_text": {"text": ebay_message(r)[:256]}}]]
    offer = offer_ebay(r)
    if offer is not None:
        rows.append([{"text": f"📋 Текст із пропозицією {offer} €", "copy_text": {"text": ebay_message(r, offer)[:256]}}])
    return {"inline_keyboard": rows}


def send_card(text: str, url: str, r: dict):
    import requests
    kb = keyboard(url, r)
    data = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true",
            "reply_markup": json.dumps(kb, ensure_ascii=False)}
    api = f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(api, data=data, timeout=15)
    if resp.status_code == 400:   # старий клієнт без copy_text — картка важливіша за кнопку
        data["reply_markup"] = json.dumps({"inline_keyboard": kb["inline_keyboard"][:1]}, ensure_ascii=False)
        resp = requests.post(api, data=data, timeout=15)
    resp.raise_for_status()


def poll_once(client, state: dict, dry_run: bool, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    first_run = not state.get("items")
    sent = 0
    state["round"] = state.get("round", 0) + 1
    jobs = [(q, False) for q in QUERIES] + ([(q, True) for q in PICKUP_QUERIES] if state["round"] % 2 else [])
    merged = {}   # одне оголошення може прийти з обох запитів — самовивіз «прилипає»
    for (q, cat, lo, hi), pickup in jobs:
        try:
            raw = fetch_new(client, q, cat, lo, hi, pickup)
        except Exception as e:
            print(f"   [{q}] помилка API: {e}")
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
            send_card(format_card(r, lst, risk, why, now), lst["url"], r)
        sent += 1
    if first_run:
        print(f"   перший запуск: запам'ятав {len(state.get('items', {}))} оголошень, сповіщення — з наступного кругу")
    prune(state, now)
    return sent


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
