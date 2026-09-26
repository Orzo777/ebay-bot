"""Оцінка оголошень Xbox Series X з листів Kleinanzeigen — поруч із RAM (ram_alert.py).

Чому Xbox: Microsoft підняла ціну 1.08.2026, вживані на eBay.de подорожчали на +29% за 2–3 міс.
(Terapeak, вживані, 30 днів до 24.09.2026: швидка ціна €509, медіана €561, ≥15 продажів/тиж),
а частина приватних продавців на KA ще продає за старою ціною (5–6 з ~50 оголошень за 7 год нижче стелі).

evaluate_console() повертає None, якщо назва — не Xbox Series X (тоді лист оцінює RAM-логіка),
інакше словник у форматі ram_alert.evaluate(), тож format_html/seller_template працюють без змін.

Ручна перевірка:
    python research/console_alert.py "Xbox Series X 1TB mit Controller" 290
"""
import re

from ram_alert import tier

# Terapeak eBay.de, SOLD, conditionId=3000 (вживані), 30 днів до 24.09.2026.
XBOX_SERIES_X = dict(p25=509, med=561, st=30, name="Xbox Series X (вживана)")
# Konsolen: 6,5% (eBay.de з 12.02.2026), €0.45 за замовлення; пересилка DHL Paket до 10 кг зі страховкою.
FEE, ORDER_FEE, SHIP = 0.065, 0.45, 10.49
MIN_PRICE = 150  # дешевше — аксесуар, «Suche», шахрайство або помилка в ціні
SUSPICIOUS_BELOW = 250  # навіть за старими цінами (до 1.08) вживана Series X коштувала €330+

_SERIES_X = re.compile(r"series\s*x\b", re.I)
_X_AND_S = re.compile(r"series\s*x\s*(?:/|\||&|und|oder|,)\s*s\b|series\s*s\s*(?:/|\||&|und|oder|,)\s*x\b", re.I)
_OTHER_CONSOLE = re.compile(r"series\s*s\b|playstation|\bps[45]\b|switch|xbox\s*one", re.I)
_REJECT = re.compile(r"defekt|bastler|ersatzteil|kaputt|\bsuche\b|\bsuch\b|tausch|gesperrt|gebannt|banned|"
                     r"ohne\s+(?:laufwerk|netzteil|konsole)|\bnur\s+(?:die\s+)?(?:ovp|karton|verpackung|controller)", re.I)
_DIGITAL = re.compile(r"digital", re.I)
_ACCESSORY = re.compile(r"controller|kontroller|headset|speichererweiterung|festplatte|\bssd\b|lenkrad|wheel|ständer|"
                        r"halterung|kühler|lüfter|skin|folie|hülle|tasche|\bcase\b|netzteil|kabel|\bspiele?\b|\bgame\b|"
                        r"fernbedienung|akku|ladestation|dock", re.I)
_CONSOLE_WORD = re.compile(r"konsole|console|\d\s?tb\b|\bmit\b|\binkl|\+|\bbundle\b", re.I)


def costs(sale_price: float) -> float:
    return FEE * sale_price + ORDER_FEE + SHIP + 0.03 * (2 * SHIP + ORDER_FEE)


def _skip(title: str, price: float, reason: str, wrong_type: bool = True) -> dict:
    return dict(verdict="SKIP", reason=reason, title=title, price=price, wrong_type=wrong_type)


def evaluate_console(title: str, price: float, shipping: float = 0.0) -> dict | None:
    if not _SERIES_X.search(title):
        return None
    total = price + shipping
    if _X_AND_S.search(title):
        return _skip(title, total, "«Series X/S» — аксесуар для обох консолей, не сама консоль")
    if _REJECT.search(title):
        return _skip(title, total, "дефект / пошук / обмін / лише коробка")
    if _OTHER_CONSOLE.search(_SERIES_X.sub("", title)):
        return _skip(title, total, "разом з іншою консоллю або не Series X")
    if _DIGITAL.search(title):
        return _skip(title, total, "Series X Digital — інша ціна продажу, не виміряна")
    if _ACCESSORY.search(title) and not _CONSOLE_WORD.search(title):
        return _skip(title, total, "схоже на аксесуар, а не на консоль")
    if total < MIN_PRICE:
        return _skip(title, total, f"дешевше €{MIN_PRICE} — аксесуар, шахрайство або помилка в ціні", False)
    real = XBOX_SERIES_X
    net_q = real["p25"] - costs(real["p25"])
    cap, good, excellent = net_q / 1.3, net_q / 1.6, net_q / 2.0
    verdict = tier(total, cap, good, excellent)
    notes = ["Перевір: працює привід і контролер, консоль не заблокована. Лише «Sicher bezahlen» або самовивіз."]
    if total < SUSPICIOUS_BELOW:
        notes.insert(0, "Підозріло дешево: частина таких оголошень — шахраї. Жодних переказів наперед.")
    return dict(verdict=verdict, type=real["name"], price=total, cap=cap, good=good, excellent=excellent,
                quick_sale=real["p25"], median_sale=real["med"], sell_through=real["st"], profit_est=net_q - total,
                brand="Microsoft", title=title, notes=notes, seller_text=seller_text(), net_q=net_q,
                offer_item="die Xbox Series X", offer_check="Laufen Laufwerk und Controller einwandfrei?")


def seller_text() -> str:
    """Німецькою, ≤256 символів (ліміт кнопки «копіювати» в Telegram)."""
    return ('Hallo! Ist die Xbox Series X noch da? Laufen Laufwerk und Controller einwandfrei, keine Sperre? '
            'Ich kaufe sofort per „Sicher bezahlen" mit Versand. Bitte ein aktuelles Foto mit Zettel (Datum). Danke!')


if __name__ == "__main__":
    import sys

    r = evaluate_console(sys.argv[1], float(sys.argv[2]))
    print(r if r else "не Xbox Series X — оцінює ram_alert")
