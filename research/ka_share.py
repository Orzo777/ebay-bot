"""«Поділитися → бот»: користувач ділиться оголошенням Kleinanzeigen у Telegram-бот, а бот за ~30 с
відповідає тією самою карткою, що й з листів: вердикт, стеля, пропозиція ціни, ризик шахрайства, тексти.

Ланцюг: Telegram → вебхук Google Apps Script (tools/gmail_trigger.gs, doPost) → GitHub workflow
ka_share.yml → цей скрипт. Бот відкриває ЛИШЕ одну сторінку оголошення (/s-anzeige/<id>, дозволено
robots.txt Kleinanzeigen), як Telegram для попереднього перегляду посилання.

Запуск вручну: python research/ka_share.py "https://www.kleinanzeigen.de/s-anzeige/…/3523539635-279-3950"
"""
import html
import os
import re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "research")
from console_alert import SHIP_IN as SHIP_IN_CONSOLE
from console_alert import evaluate_console
from ka_listing_check import UA, parse_listing, risk_lines
from ram_mail_check import _NO_PICKUP
from ram_alert import (SEND_VERDICTS, SHIP_IN_RAM, apply_pickup, broken_reason, evaluate, format_html, item_for_cost, offer_template,
                       model_from_desc, refine_by_desc, seller_template)

_ID_RE = re.compile(r"kleinanzeigen\.de/s-anzeige/(?:[^/\s]+/)?(\d{8,})")
_TITLE_RE = re.compile(r'<h1[^>]*id="viewad-title"[^>]*>(.*?)</h1>', re.S)
_PRICE_RE = re.compile(r'id="viewad-price"[^>]*>\s*([^<]*)<', re.S)
_SHIP_RE = re.compile(r"Versand ab\s*([\d.,]+)\s*€")
_PICKUP_RE = re.compile(r"Nur Abholung")
_COMMERCIAL_RE = re.compile(r"Gewerblicher Nutzer")
_LOC_RE = re.compile(r'id="viewad-locality"[^>]*>\s*([^<]*)<')
# Гамбург і передмістя (самовивіз готівкою, 27.09): поштові індекси 20xxx–22xxx або «Hamburg» у місці
_HH_RE = re.compile(r"^\s*2[0-2]\d{3}\b|hamburg", re.I)


def listing_url(text: str) -> str | None:
    m = _ID_RE.search(text or "")
    return f"https://www.kleinanzeigen.de/s-anzeige/{m.group(1)}" if m else None


def _num(s: str) -> float | None:
    s = s.strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_page(page: str) -> dict | None:
    t = _TITLE_RE.search(page)
    if not t:
        return None
    title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t.group(1)))).strip()
    title = re.sub(r"^(Reserviert|Gelöscht|Verkauft)\s*•\s*", "", title)
    pm = _PRICE_RE.search(page)
    price_txt = html.unescape(pm.group(1)).strip() if pm else ""
    num = re.search(r"([\d.,]+)\s*€", price_txt)
    sm = _SHIP_RE.search(page)
    return dict(title=title, price=_num(num.group(1)) if num else None, vb="VB" in price_txt,
                ship_from=_num(sm.group(1)) if sm else None, pickup_only=bool(_PICKUP_RE.search(page)) and not sm,
                commercial=bool(_COMMERCIAL_RE.search(page)),
                reserved=bool(re.search(r"Reserviert\s*•", t.group(1))),
                hamburg=bool(_HH_RE.search(html.unescape(lm.group(1)))) if (lm := _LOC_RE.search(page)) else False)


def evaluate_listing(page: str, url: str) -> tuple[str, dict | None]:
    """→ (html-повідомлення, результат оцінки або None). Без мережі — тестується на збережених сторінках."""
    from html import escape

    info = parse_page(page)
    if not info:
        return "⚫ Не вдалося прочитати оголошення — можливо, його вже зняли.", None
    if info["price"] is None:
        return (f"⏭ <i>{escape(info['title'][:90])}</i>\nЦіни немає («VB» без суми) — напиши продавцю, "
                f"скільки хоче, і перешли мені ще раз з ціною в тексті."), None
    probe = evaluate_console(info["title"], info["price"])
    # своя пересилка для кожної консолі (Xbox/PS5 ~€11, Switch ~€7, Lite ~€5.5)
    default_ship = (probe.get("ship_in") or SHIP_IN_CONSOLE) if probe is not None else SHIP_IN_RAM
    # «Versand ab X €» — найдешевший варіант продавця; консоль/кіт рідко дешевше нашої оцінки
    ship = 0.0 if info["pickup_only"] else max(default_ship, info["ship_from"] or 0.0)
    res = (evaluate_console(info["title"], info["price"], shipping=ship, vb=info["vb"])
           or evaluate(info["title"], info["price"], shipping=ship, vb=info["vb"]))
    if res["verdict"] == "UNKNOWN":   # «Xbox zu verkaufen» — модель лише в описі (29.09)
        model = model_from_desc(info["title"], parse_listing(page, info["price"], 0).get("desc"))
        if model:
            info["title"] = f"{info['title']} {model}"
            res = evaluate_console(info["title"], info["price"], shipping=ship, vb=info["vb"])
    notes = []
    if info.get("hamburg"):   # поруч — забираємо самі, готівкою після огляду
        apply_pickup(res)
    if info["pickup_only"] and not info.get("hamburg"):
        notes.append("Лише самовивіз — пересилку не враховано, зважай на дорогу.")
    if info["commercial"]:
        notes.append("Комерційний продавець — у підписках ми таких не беремо; ціни зазвичай ринкові.")
    if info["reserved"]:
        notes.append("Оголошення позначене «Reserviert».")
    if res["verdict"] in SEND_VERDICTS:
        risk = parse_listing(page, info["price"], res.get("quick_sale", 0))
        if risk.get("block"):   # вигідна ціна не рятує: без захисту покупця це лотерея
            lines = [f"⛔ <b>Не бери — схоже на шахрая</b> · <i>{escape(info['title'][:90])}</i> — {info['price']:.0f} €"]
            return "\n".join(lines + ["   • " + escape(h) for h in risk["hard"]]), res
        broken = broken_reason(risk.get("desc"))
        if broken:   # несправне не купуємо (27.09)
            return (f"⛔ <b>Не бери — в описі дефект</b> · <i>{escape(info['title'][:90])}</i> — {info['price']:.0f} €\n"
                    f"   • «{escape(broken)}»"), res
        # опис уточнює назву: «Laptop RAM», «4x 8GB», «Preis pro Riegel», «ich suche» (28.09)
        new = refine_by_desc(res, info["title"], info["price"], info["vb"], risk.get("desc"),
                             lambda t, p, vb=False: (evaluate_console(t, p, shipping=ship, vb=vb)
                                                     or evaluate(t, p, shipping=ship, vb=vb)))
        if new is not res:
            if info.get("hamburg"):
                apply_pickup(new)
            res = new
            if res["verdict"] not in SEND_VERDICTS:
                return (f"⏭ <b>Не бери</b> · <i>{escape(info['title'][:90])}</i> — {info['price']:.0f} €\n"
                        f"За описом: {escape(res.get('refined', ''))} — {escape(res.get('reason') or 'не вигідно')}"), res
        if info.get("hamburg") and _NO_PICKUP.search(risk.get("desc") or ""):
            notes.append("Продавець пише «nur Versand» — самовивозу не буде, рахуй із пересилкою.")
        res["risk_lines"] = risk_lines(risk)
        res["desc"] = risk.get("desc")
        res["buy_now"] = bool(risk.get("buy_now"))
        if not res["buy_now"] and not info.get("hamburg"):   # у підписках таких не шлемо (02.10) — тут питав сам
            notes.insert(0, "Без «Direkt kaufen»: з досвіду такі продавці майже завжди шахраї. Купуй лише через «Sicher bezahlen» "
                            "(запит на оплату від KA) або самовивозом — жодних переказів і PayPal.")
        res["notes"] = res.get("notes", []) + notes
        return format_html(res), res
    head = f"⏭ <b>Не бери</b> · <i>{escape(info['title'][:90])}</i> — {info['price']:.0f} €" + (" VB" if info["vb"] else "")
    if res.get("cap"):
        # продавця перевіряємо й тут (29.09: PS5 за 310 € — «дорого», а насправді акаунт 0 дн. і 71% ринку = шахрай)
        risk = parse_listing(page, info["price"], res.get("quick_sale", 0))
        if risk.get("block"):
            lines = [f"⛔ <b>Не бери — схоже на шахрая</b> · <i>{escape(info['title'][:90])}</i> — {info['price']:.0f} €"]
            return "\n".join(lines + ["   • " + escape(h) for h in risk["hard"]]), res
        broken = broken_reason(risk.get("desc"))
        if broken:
            return (f"⛔ <b>Не бери — в описі дефект</b> · <i>{escape(info['title'][:90])}</i> — {info['price']:.0f} €\n"
                    f"   • «{escape(broken)}»"), res
        cap_item = int(item_for_cost(res['cap'], ship))
        head += (f"\nДля {escape(res['type'])} вигідно лише до {cap_item} € "
                 f"в оголошенні (продається за {res['quick_sale']}–{res['median_sale']} €).")
        head += bargain_line(res, info["price"], cap_item)
    elif res["verdict"] == "UNKNOWN" and not re.search(r"ddr|\bram\b|arbeitsspeicher|so-?dimm|speicher", info["title"], re.I):
        head += ("\nЦей товар бот не оцінює. Оцінює: оперативку, Xbox Series X, PS5, Nintendo Switch 2 "
                 "(і цілі ПК у ПК-боті). Інше ми досліджували — маржі на Kleinanzeigen немає.")
    elif res.get("reason"):
        head += f"\n{escape(res['reason'])}"
    return "\n".join([head] + [f"⚠️ {escape(n)}" for n in notes]), res


def bargain_line(res: dict, price: float, cap_item: int) -> str:
    """«Якщо зторгуєшся»: заробіток при реальній знижці (−5…10%) і на межі вигідності — рішення за тобою."""
    from ram_alert import total_for
    if not res.get("net_q") or cap_item <= 0 or cap_item < 0.8 * price:
        return ""   # до вигідної ціни задалеко — торг не допоможе
    pts = sorted({int(price * 0.95 // 5 * 5), int(price * 0.9 // 5 * 5), cap_item // 5 * 5}, reverse=True)
    bits = []
    for p in pts:
        if p >= price:
            continue
        total = total_for(res, p)
        profit = res["net_q"] - total
        bits.append(f"за {p} € → заробіток ≈ {profit:.0f} € ({100 * profit / total:.0f}%)")
    return ("\n🤝 Якщо зторгуєшся: " + "; ".join(bits) + ". Наш поріг — 30% маржі (запас на ризик і повільний продаж)."
            if bits else "")


def _send(html_text: str, kb: dict):
    import json

    import requests

    import config
    api = f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": config.TELEGRAM_CHAT_ID, "text": html_text, "parse_mode": "HTML",
            "disable_web_page_preview": "true", "reply_markup": json.dumps(kb, ensure_ascii=False)}
    r = requests.post(api, data=data, timeout=15)
    if r.status_code == 400:
        data["reply_markup"] = json.dumps({"inline_keyboard": kb["inline_keyboard"][:1]}, ensure_ascii=False)
        r = requests.post(api, data=data, timeout=15)
    r.raise_for_status()
    return (r.json().get("result") or {}).get("message_id"), data["reply_markup"]


def _is_no(msg: str) -> bool:
    """Відповідь «не бери»: шахрай / дефект (⛔), невигідно чи не той товар (⏭), не читається (⚫)."""
    return msg.lstrip().startswith(("⛔", "⏭", "⚫"))


def _remember(url: str, mid, title: str):
    """Картка «поділитися» → карта карток (для реплаю відповіді продавця). Стан — KA_SHARE_STATE (кеш GitHub)."""
    import json

    import cardmap
    path = os.getenv("KA_SHARE_STATE")
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    cardmap.remember(st.setdefault("cards", {}), url, mid, title)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False)


def main(text: str):
    import requests

    from ram_mail_check import send_telegram_card, send_telegram_text

    from ebay_watch import ebay_item_id, keyboard, share_ebay
    from photo_check import add_to_card, ka_images
    eid = ebay_item_id(text)
    if eid:   # посилання eBay (28.09): та сама оцінка, що й у сканері eBay; аукціон — з максимальною ставкою
        msg, res, lst = share_ebay(eid)
        if res and res.get("verdict") in SEND_VERDICTS and lst and not _is_no(msg):
            mid, kb_json = _send(msg, keyboard(lst["url"], res)) or (None, None)
            add_to_card(mid, msg, kb_json, res, lst["title"], lst.get("images") or [])
        elif res and res.get("verdict") == "AUCTION" and lst:   # аукціон: лише «відкрити», ставку робиш сам
            mid, kb_json = _send(msg, {"inline_keyboard": [[{"text": "🔗 Відкрити на eBay", "url": lst["url"]}]]}) or (None, None)
            add_to_card(mid, msg, kb_json, res, lst["title"], lst.get("images") or [])
        else:   # «не бери» — лише причина, без кнопок (29.09: посилання в тебе вже є, текст продавцю не потрібен)
            send_telegram_text(msg)
        print(msg)
        return
    url = listing_url(text)
    if not url:
        send_telegram_text("Не бачу посилання на оголошення Kleinanzeigen чи eBay. Поділись оголошенням (кнопка «Teilen») "
                           "або встав посилання виду kleinanzeigen.de/s-anzeige/… чи ebay.de/itm/…")
        return
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "de-DE"}, timeout=15)
    if r.status_code != 200:
        send_telegram_text(f"⚫ Оголошення не відкривається (код {r.status_code}) — можливо, його вже зняли.")
        return
    msg, res = evaluate_listing(r.text, url)
    if res and res.get("verdict") in SEND_VERDICTS and not _is_no(msg):
        mid, kb = send_telegram_card(msg, url, seller_template(res), None, False, offer_template(res)) or (None, None)
        _remember(url, mid, res.get("title") or "")
        add_to_card(mid, msg, kb, res, res.get("title") or "", ka_images(r.text))
    else:   # «не бери» (шахрай, дефект, дорого, опис) — лише причина, без кнопок
        send_telegram_text(msg)
    print(msg)


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]))
