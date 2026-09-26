"""Перевірка продавця на ознаки шахрайства — лише для кандидатів «БЕРИ» (кілька оголошень на день).

Бот відкриває ОДНУ сторінку оголошення (/s-anzeige/<id>, дозволено robots.txt Kleinanzeigen) —
так само, як Telegram будує попередній перегляд посилання. Списки пошуку/підписок бот НЕ відкриває:
robots.txt їх забороняє (m-suche-verwenden, preis:, anbieter:, versand...).

Ознаки (з типових схем на KA): свіжий акаунт; опис, що веде в WhatsApp/Telegram/e-mail;
оплата «PayPal Freunde»/переказом наперед; «я за кордоном / лише пересилка»; ціна «надто гарна»;
порожній опис. Старий зламаний акаунт це не ловить повністю — тому Kleinanzeigen-попередження
після першого повідомлення продавцю лишається останнім фільтром.
"""
import html
import re
from datetime import date, datetime

UA = "Mozilla/5.0 (compatible; ka-alert-bot/1.0; personal use, one listing page per alert)"

_DESC_RE = re.compile(r'id="viewad-description-text"[^>]*>(.*?)</p>', re.S)
_SINCE_RE = re.compile(r"Aktiv seit\s*(\d{2})\.(\d{2})\.(\d{4})")
_COMMERCIAL_RE = re.compile(r"Gewerblicher Nutzer")
_GONE_RE = re.compile(r"nicht mehr verfügbar|wurde gelöscht|Anzeige ist deaktiviert|existiert nicht mehr", re.I)

_CONTACT = re.compile(r"whats\s?app|telegram|signal\b|e-?mail|@[a-z0-9-]+\.[a-z]{2,}|\+\d{2}\s?\d|\b01[5-7]\d[\s/]?\d{3}|"
                      r"handynummer|meine nummer|schreib(?:t)? mir (?:auf|per)", re.I)
_PAYMENT = re.compile(r"freunde\s*(?:&|und)\s*familie|paypal\s*(?:an\s*)?freunde|\bf\s?&\s?f\b|vorkasse|"
                      r"nur\s+(?:per\s+)?überweisung|western union|gutschein|paysafe", re.I)
_NO_PROTECTION = re.compile(r"(?:sicher bezahlen|bezahlfunktion|käuferschutz)[^.!]{0,80}(?:nicht|kein)|"
                            r"(?:nicht|kein)[^.!]{0,40}(?:sicher bezahlen|käuferschutz)", re.I)
_STORY = re.compile(r"im ausland|auf montage|bin beruflich|umzug ins ausland|nur versand|keine abholung|"
                    r"keine besichtigung|dringend", re.I)


def parse_listing(page: str, price: float, quick_sale: float, today: date | None = None) -> dict:
    """Чистий розбір сторінки → ризик. Без мережі (тестується на збережених сторінках)."""
    today = today or date.today()
    if _GONE_RE.search(page) and not _DESC_RE.search(page):
        return dict(level="gone", score=0, reasons=["оголошення вже зняте"], seller="")
    reasons, score = [], 0
    m = _SINCE_RE.search(page)
    seller = "комерційний" if _COMMERCIAL_RE.search(page) else "приватний"
    if m:
        since = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        age_days = (today - since).days
        seller += f", на KA з {since.year}" if age_days >= 365 else f", на KA {age_days} дн."
        if age_days < 14:
            score += 3
            reasons.append(f"акаунт створено {age_days} дн. тому")
        elif age_days < 90:
            score += 1
            reasons.append(f"молодий акаунт ({age_days} дн.)")
    else:
        score += 1
        reasons.append("не видно дати реєстрації")
    dm = _DESC_RE.search(page)
    desc = html.unescape(re.sub(r"<[^>]+>", " ", dm.group(1))) if dm else ""
    desc = re.sub(r"\s+", " ", desc).strip()
    if len(desc) < 30:
        score += 1
        reasons.append("майже порожній опис")
    if _CONTACT.search(desc):
        score += 2
        reasons.append("у описі кличе писати поза Kleinanzeigen")
    if _PAYMENT.search(desc):
        score += 2
        reasons.append("оплата переказом / PayPal Freunde")
    if _NO_PROTECTION.search(desc):
        score += 1
        reasons.append("відмовляється від «Sicher bezahlen» / захисту покупця")
    story = sorted({s.group(0).lower() for s in _STORY.finditer(desc)})
    if story:
        score += min(len(story), 2)
        reasons.append("типові фрази шахраїв: " + ", ".join(story))
    if quick_sale and price < 0.4 * quick_sale:
        score += 2
        reasons.append(f"ціна {price:.0f} € — менше 40% ринку")
    elif quick_sale and price < 0.5 * quick_sale:
        score += 1
        reasons.append(f"ціна {price:.0f} € — менше половини ринку")
    level = "high" if score >= 4 else "medium" if score >= 2 else "low"
    return dict(level=level, score=score, reasons=reasons, seller=seller)


def check_listing(link: str | None, price: float, quick_sale: float) -> dict | None:
    """Мережа: одна сторінка оголошення. Будь-яка помилка → None (картка йде як раніше, з приміткою)."""
    if not link or "/s-anzeige/" not in link:
        return None
    try:
        import requests

        r = requests.get(link, headers={"User-Agent": UA, "Accept-Language": "de-DE"}, timeout=10)
        if r.status_code == 404:
            return dict(level="gone", score=0, reasons=["оголошення вже зняте"], seller="")
        if r.status_code != 200:
            return None
        return parse_listing(r.text, price, quick_sale)
    except Exception:
        return None


LABEL = {"low": "🟢 низький", "medium": "🟡 середній", "high": "🔴 ВИСОКИЙ", "gone": "⚫ оголошення зняте"}


def risk_lines(risk: dict | None) -> list[str]:
    if risk is None:
        return ["🛡 Продавця перевірити не вдалося — глянь сам: дата реєстрації, опис."]
    head = f"🛡 Ризик шахрайства: <b>{LABEL[risk['level']]}</b>" + (f" · {risk['seller']}" if risk.get("seller") else "")
    return [head] + [f"   • {html.escape(r)}" for r in risk["reasons"]]


if __name__ == "__main__":
    import sys

    print(check_listing(sys.argv[1], float(sys.argv[2]), float(sys.argv[3])))
