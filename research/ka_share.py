"""«Поділитися → бот»: користувач ділиться оголошенням Kleinanzeigen у Telegram-бот, а бот за ~30 с
відповідає тією самою карткою, що й з листів: вердикт, стеля, пропозиція ціни, ризик шахрайства, тексти.

Ланцюг: Telegram → вебхук Google Apps Script (tools/gmail_trigger.gs, doPost) → GitHub workflow
ka_share.yml → цей скрипт. Бот відкриває ЛИШЕ одну сторінку оголошення (/s-anzeige/<id>, дозволено
robots.txt Kleinanzeigen), як Telegram для попереднього перегляду посилання.

Запуск вручну: python research/ka_share.py "https://www.kleinanzeigen.de/s-anzeige/…/3523539635-279-3950"
"""
import html
import re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "research")
from console_alert import SHIP_IN as SHIP_IN_CONSOLE
from console_alert import evaluate_console
from ka_listing_check import UA, parse_listing, risk_lines
from ram_alert import SEND_VERDICTS, SHIP_IN_RAM, evaluate, format_html, item_for_cost, offer_template, seller_template

_ID_RE = re.compile(r"kleinanzeigen\.de/s-anzeige/(?:[^/\s]+/)?(\d{8,})")
_TITLE_RE = re.compile(r'<h1[^>]*id="viewad-title"[^>]*>(.*?)</h1>', re.S)
_PRICE_RE = re.compile(r'id="viewad-price"[^>]*>\s*([^<]*)<', re.S)
_SHIP_RE = re.compile(r"Versand ab\s*([\d.,]+)\s*€")
_PICKUP_RE = re.compile(r"Nur Abholung")
_COMMERCIAL_RE = re.compile(r"Gewerblicher Nutzer")


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
                reserved=bool(re.search(r"Reserviert\s*•", t.group(1))))


def evaluate_listing(page: str, url: str) -> tuple[str, dict | None]:
    """→ (html-повідомлення, результат оцінки або None). Без мережі — тестується на збережених сторінках."""
    from html import escape

    info = parse_page(page)
    if not info:
        return "⚫ Не вдалося прочитати оголошення — можливо, його вже зняли.", None
    if info["price"] is None:
        return (f"⏭ <i>{escape(info['title'][:90])}</i>\nЦіни немає («VB» без суми) — напиши продавцю, "
                f"скільки хоче, і перешли мені ще раз з ціною в тексті."), None
    is_console = evaluate_console(info["title"], info["price"]) is not None
    default_ship = SHIP_IN_CONSOLE if is_console else SHIP_IN_RAM
    # «Versand ab X €» — найдешевший варіант продавця; консоль/кіт рідко дешевше нашої оцінки
    ship = 0.0 if info["pickup_only"] else max(default_ship, info["ship_from"] or 0.0)
    res = (evaluate_console(info["title"], info["price"], shipping=ship, vb=info["vb"])
           or evaluate(info["title"], info["price"], shipping=ship, vb=info["vb"]))
    notes = []
    if info["pickup_only"]:
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
        res["risk_lines"] = risk_lines(risk)
        res["desc"] = risk.get("desc")
        res["notes"] = res.get("notes", []) + notes
        return format_html(res), res
    head = f"⏭ <b>Не бери</b> · <i>{escape(info['title'][:90])}</i> — {info['price']:.0f} €" + (" VB" if info["vb"] else "")
    if res.get("cap"):
        head += (f"\nДля {escape(res['type'])} вигідно лише до {int(item_for_cost(res['cap'], ship))} € "
                 f"в оголошенні (продається за {res['quick_sale']}–{res['median_sale']} €).")
    elif res.get("reason"):
        head += f"\n{escape(res['reason'])}"
    return "\n".join([head] + [f"⚠️ {escape(n)}" for n in notes]), res


def main(text: str):
    import requests

    from ram_mail_check import send_telegram_card, send_telegram_text

    url = listing_url(text)
    if not url:
        send_telegram_text("Не бачу посилання на оголошення Kleinanzeigen. Поділись оголошенням (кнопка «Teilen») "
                           "або встав посилання виду kleinanzeigen.de/s-anzeige/…")
        return
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "de-DE"}, timeout=15)
    if r.status_code != 200:
        send_telegram_text(f"⚫ Оголошення не відкривається (код {r.status_code}) — можливо, його вже зняли.")
        return
    msg, res = evaluate_listing(r.text, url)
    if res and res.get("verdict") in SEND_VERDICTS:
        send_telegram_card(msg, url, seller_template(res), None, False, offer_template(res))
    else:
        send_telegram_text(msg, url)
    print(msg)


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]))
