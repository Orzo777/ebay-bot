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
# «Direkt kaufen» (продавець увімкнув купівлю одразу через «Sicher bezahlen»): лот стає твоїм без чекання відповіді
_BUY_NOW_RE = re.compile(r'isBuyNowEnabled:\s*true')   # у JS-конфігу сторінки; прихований input buyNowEnabled завжди false
_GONE_RE = re.compile(r"nicht mehr verfügbar|wurde gelöscht|Anzeige ist deaktiviert|existiert nicht mehr", re.I)

# «Жорсткі» ознаки — оголошення відкидається одразу, картки не буде: нормальний продавець лишається в чаті KA
# і погоджується на «Sicher bezahlen». Регулярні вирази навмисно вузькі: «E-Mail-Rechnung», «HDMI-Signal»,
# «Gutschein dabei», «Sicher bezahlen möglich, keine Rücknahme» — НЕ шахрайство.
_CONTACT = re.compile(r"whats\s?app|\bwa\b\s*:|telegram|threema|\b[\w.+-]+@[a-z0-9-]+\.[a-z]{2,}|\+\d{2}\s?\d{2,}|"
                      r"\b01[5-7]\d[\s/]?\d{3}|handynummer|meine nummer|telefonnummer|\btel\.?\s*:?\s*0\d|"
                      r"schreib(?:t)? mir (?:auf|per|über)|kontaktier\w* mich (?:auf|per|über)|"
                      r"ruf\w*\s+(?:sie\s+)?mich\s+an\b|\bsms\b|insta(?:gram)?\s*:", re.I)   # «Rechnung per E-Mail» — не контакт
# Оплата без захисту покупця. Шахраї маскують «PayPal»: «Pay Pal», «P@yPal», «PP», «F+F», «FnF» (28.09).
_PP = r"(?:pay\s*\.?\s*pal|p\s*@\s*y\s*pal|\bpp\b)"
# «PayPal FF», «Paypal per Freunde», «PP an Freunde» — між PayPal і «Freunde» буває 0–2 слова (28.09, живі описи)
_PAYMENT = re.compile(r"(?:freunde|familie)\s*(?:&|und|\+|/)\s*(?:familie|freunde)|" + _PP + r"\W{0,3}(?:\w+\W+){0,2}?"
                      r"(?:freunde|friends|family|familie|privat|ff|fnf)\b|\bf\s?[&+]\s?f\b|\bfnf\b|vorkasse|nur\s+(?:per\s+)?überweisung|"
                      r"western union|paysafe|\bwero\b|echtzeit-?überweisung|sofortüberweisung|überweisung\s+(?:vorab|vorher|im\s+voraus)|"
                      r"\banzahlung|\bkaution|treuhand|zahlungs-?link|link\s+(?:zur|für\s+die)\s+zahlung|als\s+geschenk\s+senden", re.I)
_TRANSFER = re.compile(r"überweisung", re.I)
_OK_NEG = r"(?!\s*(?:problem|thema|garantie|gewährleistung|rücknahme|umtausch|haftung))"
# «Sicher bezahlen» пишуть по-різному: «Sicher zahlen», «sicheres Bezahlen», «Sicherbezahlen» (26.09: «kein Sicher zahlen»)
_SB = r"(?:sicher(?:es)?\s*(?:be)?zahl\w*|bezahlfunktion|käuferschutz)"
_NO_PROTECTION = re.compile(_SB + r"[^.!,;]{0,80}\b(?:nicht|kein\w*)\b" + _OK_NEG + "|"
                            r"\b(?:nicht|kein\w*|ohne)\b[^.!,;]{0,40}" + _SB, re.I)
# Відмова від захисту іншими словами: «PayPal Waren & Dienstleistungen oder der Systemkauf werden nicht angeboten»
# (Systemkauf / Direkt kaufen = купівля через Kleinanzeigen із захистом). Потрібне саме «не пропоную / не можливо».
_NO_SYSTEM = re.compile(r"(?:systemkauf|waren\s*(?:&|und)\s*dienstleistung\w*|direkt\s*kaufen)[^.!]{0,80}\b(?:nicht|kein\w*)\s+"
                        r"(?:an)?(?:geboten|möglich|akzeptiert|in\s*frage|infrage)|\b(?:kein\w*|ohne)\s+(?:systemkauf|direkt\s*kaufen)",
                        re.I)
# Текст-шаблон, що ходить під десятками оголошень (27.09, користувач бачив ~10 разів): лише F&F, «Preis fest», блокування.
# Беремо кілька характерних фраз разом — одна фраза сама по собі може трапитись і в чесного продавця.
_TEMPLATE_PHRASES = [r"vor dem anschreiben kurz alles durchlesen", r"unnötige zeit", r"ich bin ein ehrlicher verkäufer",
                     r"meine bewertungen sprechen für sich", r"systemkauf", r"gegebenenfalls direkt blockiert",
                     r"klare und faire bedingungen"]
_STORY = re.compile(r"im ausland|auf montage|bin beruflich|umzug ins ausland|nur\s+(?:per\s+)?versand|keine abholung|abholung nicht|"
                    r"wegen\s+(?:\w+\s+)?umzug|umgezogen|\bpendel|"
                    r"keine besichtigung|dringend|bundeswehr|soldat|krankenhaus|spedition|kurier|versand\s+nur\s+(?:gegen|per|bei|mit|über)|nur per post|"
                    r"wohne (?:jetzt |nun |derzeit )?(?:in|im)\s+(?:ausland|england|spanien|polen|frankreich|italien)", re.I)
_TITLE_RE = re.compile(r'<h1[^>]*id="viewad-title"[^>]*>(.*?)</h1>', re.S)


def _title_of(page: str) -> str:
    m = _TITLE_RE.search(page)
    return html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))) if m else ""


# «Neu / OVP / versiegelt» дешевше ринку — улюблена приманка (PS5 Slim «unausgepackt» за €280 від акаунта 15 дн., 27.09)
_NEW_BAIT = re.compile(r"(?<!nicht )\bneu\b(?!wertig)|originalverpackt|versiegelt|ungeöffnet|unausgepackt|sealed|"
                       r"(?:noch\s+)?in\s+(?:der\s+)?(?:original)?folie|nie\s+(?:benutzt|ausgepackt)", re.I)


def parse_listing(page: str, price: float, quick_sale: float, today: date | None = None) -> dict:
    """Чистий розбір сторінки → ризик. Без мережі (тестується на збережених сторінках)."""
    today = today or date.today()
    if _GONE_RE.search(page) and not _DESC_RE.search(page):
        return dict(level="gone", score=0, reasons=["оголошення вже зняте"], seller="")
    if not _DESC_RE.search(page) and not _SINCE_RE.search(page):   # не сторінка оголошення (редирект, зміна верстки)
        return dict(level="unknown", score=0, reasons=["сторінку не вдалося прочитати — можливо, оголошення вже зняте"],
                    seller="")
    reasons, hard, score = [], [], 0
    m = _SINCE_RE.search(page)
    seller = "комерційний" if _COMMERCIAL_RE.search(page) else "приватний"
    if m:
        since = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        age_days = (today - since).days
        seller += f", на KA з {since.year}" if age_days >= 365 else f", на KA {age_days} дн."
        if age_days < 14:
            score += 3
            reasons.append(f"акаунт створено {age_days} дн. тому")
            hard.append(f"акаунт створено {age_days} дн. тому")
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
        hard.append("кличе писати поза Kleinanzeigen")
    if not _PAYMENT.search(desc) and _TRANSFER.search(desc):   # «PayPal oder Überweisung» — не блок, але попередження
        score += 1
        reasons.append("пропонує переказ (без захисту покупця) — плати лише через «Sicher bezahlen»")
    if _PAYMENT.search(desc):
        score += 2
        reasons.append("оплата переказом / PayPal Freunde")
        hard.append("хоче оплату переказом / PayPal Freunde (без захисту покупця)")
    if _NO_PROTECTION.search(desc) or _NO_SYSTEM.search(desc):
        score += 1
        reasons.append("відмовляється від «Sicher bezahlen» / захисту покупця")
        hard.append("відмовляється від «Sicher bezahlen»")
    if sum(1 for ph in _TEMPLATE_PHRASES if re.search(ph, desc, re.I)) >= 3:
        score += 2
        reasons.append("стандартний текст-шаблон, що ходить під багатьма оголошеннями")
        hard.append("відомий текст-шаблон (лише PayPal Freunde / без захисту покупця)")
    story = sorted({s.group(0).lower() for s in _STORY.finditer(desc)})
    if story:
        score += min(len(story), 2)
        reasons.append("типові фрази шахраїв: " + ", ".join(story))
    # Молодий акаунт + ціна помітно нижче ринку: так виглядає більшість «вигідних» шахрайських оголошень (27–28.09:
    # Xbox €250 від акаунта 0 дн., PS5 €230 — 0 дн., PS5 €280 — 15 дн.). Чесний новачок рідко продає настільки дешево.
    if m and quick_sale and age_days < 60 and price < 0.75 * quick_sale:
        score += 2
        reasons.append(f"акаунт {age_days} дн. і ціна {100 * price / quick_sale:.0f}% ринку")
        hard.append(f"молодий акаунт ({age_days} дн.) і ціна нижче 75% ринку")
    # Легенда «лише пересилка / річ в іншому місті» + ціна нижче ринку — шаблон шахраїв і зі «старих» (зламаних) акаунтів
    if story and quick_sale and price < 0.75 * quick_sale and (not m or age_days < 365):
        hard.append(f"легенда ({', '.join(story)}) і ціна {100 * price / quick_sale:.0f}% ринку")
    if quick_sale and price < 0.7 * quick_sale and _NEW_BAIT.search(desc + " " + _title_of(page)):
        score += 1
        reasons.append("«нове / в плівці» набагато дешевше ринку")
        if m and age_days < 180:
            hard.append("«нове / в плівці» за ціною нижче 70% ринку від молодого акаунта")
    if len(desc) < 30 and m and age_days < 90 and quick_sale and price < 0.8 * quick_sale:
        hard.append("порожній опис, молодий акаунт і ціна нижче ринку")
    if quick_sale and price < 0.4 * quick_sale:
        score += 2
        reasons.append(f"ціна {price:.0f} € — менше 40% ринку")
    elif quick_sale and price < 0.5 * quick_sale:
        score += 1
        reasons.append(f"ціна {price:.0f} € — менше половини ринку")
    level = "high" if score >= 4 else "medium" if score >= 2 else "low"
    if level == "high" and not hard:
        hard.append("забагато ознак шахрайства разом")
    # block: у підписках картки не буде зовсім; лишаються 🟢 і 🟡 лише з «м'яких» ознак (молодий акаунт, короткий опис)
    from photo_check import ka_images
    return dict(level=level, score=score, reasons=reasons, seller=seller, block=bool(hard), hard=hard, desc=desc,
                buy_now=bool(_BUY_NOW_RE.search(page)), images=ka_images(page), page_price=page_price(page))


_PAGE_PRICE = re.compile(r'ad_price"\s*:\s*"(\d+(?:\.\d+)?)"|adPrice:\s*(\d+(?:\.\d+)?)')


def page_price(page: str) -> float | None:
    """Поточна ціна на сторінці оголошення. 01.10: у листі KA було 80 €, а продавець за кілька хвилин підняв до 120 € VB —
    картка показала стару ціну. 0 / «Zu verschenken» / не знайдено → None."""
    m = _PAGE_PRICE.search(page or "")
    v = float(m.group(1) or m.group(2)) if m else 0.0
    return v or None


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


LABEL = {"unknown": "⚪ невідомо", "low": "🟢 низький", "medium": "🟡 середній", "high": "🔴 ВИСОКИЙ", "gone": "⚫ оголошення зняте"}


def risk_lines(risk: dict | None) -> list[str]:
    if risk is None:
        return ["🛡 Продавця перевірити не вдалося — глянь сам: дата реєстрації, опис."]
    head = f"🛡 Ризик шахрайства: <b>{LABEL[risk['level']]}</b>" + (f" · {risk['seller']}" if risk.get("seller") else "")
    return [head] + [f"   • {html.escape(r)}" for r in risk["reasons"]]


if __name__ == "__main__":
    import sys

    print(check_listing(sys.argv[1], float(sys.argv[2]), float(sys.argv[3])))
