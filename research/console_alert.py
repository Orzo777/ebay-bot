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

from ram_alert import buy_cost, tier

# Terapeak eBay.de, SOLD, conditionId=3000 (вживані), 30 днів до 24.09.2026.
XBOX_SERIES_X = dict(p25=509, med=561, st=30, name="Xbox Series X (вживана)")
# Konsolen: 6,5% (eBay.de з 12.02.2026), €0.45 за замовлення; пересилка DHL Paket до 10 кг зі страховкою.
FEE, ORDER_FEE, SHIP = 0.065, 0.45, 10.49
SHIP_IN = 11.0   # пересилка, яку покупець платить продавцю (DHL Paket до 10 кг через «Sicher bezahlen»)
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


PACK = 3.0            # коробка + наповнювач для консолі
INSURE_OVER_500 = 6.99  # DHL базово страхує до €500; Xbox продається дорожче → страховка до €2 500


def costs(sale_price: float) -> float:
    return (FEE * sale_price + ORDER_FEE + SHIP + PACK + (INSURE_OVER_500 if sale_price > 500 else 0)
            + 0.03 * (2 * SHIP + ORDER_FEE))


def _skip(title: str, price: float, reason: str, wrong_type: bool = True) -> dict:
    return dict(verdict="SKIP", reason=reason, title=title, price=price, wrong_type=wrong_type)


def evaluate_console(title: str, price: float, shipping: float | None = None, vb: bool = False) -> dict | None:
    if not _SERIES_X.search(title):
        return evaluate_ps5(title, price, shipping, vb)
    ship_in = SHIP_IN if shipping is None else shipping
    total = price
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
    cost = buy_cost(price, ship_in)
    verdict = tier(cost, cap, good, excellent, vb)
    notes = ["Перевір: працює привід і контролер, консоль не заблокована. Лише «Sicher bezahlen» або самовивіз."]
    if total < SUSPICIOUS_BELOW:
        notes.insert(0, "Підозріло дешево: частина таких оголошень — шахраї. Жодних переказів наперед.")
    return dict(verdict=verdict, type=real["name"], price=total, cap=cap, good=good, excellent=excellent,
                quick_sale=real["p25"], median_sale=real["med"], sell_through=real["st"], profit_est=net_q - cost,
                brand="Microsoft", title=title, notes=notes, net_q=net_q,
                item_acc="die Xbox Series X", check_q="Laufen Laufwerk und Controller einwandfrei, keine Sperre?",
                buy_cost=cost, ship_in=ship_in, vb=vb)


# ---------------- PS5 (додано 26.09.2026) ----------------
# Terapeak eBay.de, SOLD, вживані, 30 днів до 26.09.2026 (див. PS5_* нижче). Slim і перша («товста») модель
# продаються майже однаково, тому Disc — один тип; Digital — окремий (дешевший); Pro не купуємо (забирає бюджет,
# на KA вже по ринку).
PS5_DISC = dict(p25=459, med=489, st=30, name="PS5 з дисководом (вживана)")
PS5_DIGITAL = dict(p25=418, med=443, st=25, name="PS5 Digital (вживана)")
PS5_SUSPICIOUS_BELOW = 280

_PS5 = re.compile(r"\bps\s?5\b|playstation\s*5", re.I)
_PS5_PRO = re.compile(r"\bpro\b", re.I)
_PS5_OTHER = re.compile(r"portal|\bvr\s?2?\b|psvr|xbox|switch|\bps4\b|playstation\s*4", re.I)
_PS5_ACCESSORY = re.compile(r"controller|dualsense|headset|laufwerk|disc\s*drive|\bssd\b|festplatte|ständer|halterung|"
                            r"lüfter|kühler|skin|folie|hülle|tasche|\bcase\b|cover|faceplate|netzteil|kabel|\bspiele?\b|"
                            r"\bgame\b|fernbedienung|ladestation|dock|kamera|lenkrad", re.I)
_PS5_CONSOLE = re.compile(r"konsole|console|\d\s?tb\b|825\s?gb|\bmit\b|\binkl|\+|\bbundle\b|\bslim\b|\bedition\b", re.I)


def evaluate_ps5(title: str, price: float, shipping: float | None = None, vb: bool = False) -> dict | None:
    """None — не PS5 (далі оцінює RAM-логіка); інакше словник у форматі ram_alert.evaluate()."""
    if not _PS5.search(title):
        return None
    ship_in = SHIP_IN if shipping is None else shipping
    if _REJECT.search(title):
        return _skip(title, price, "дефект / пошук / обмін / лише коробка")
    if _PS5_PRO.search(title):
        return _skip(title, price, "PS5 Pro — не купуємо: забирає бюджет, на KA вже по ринку")
    if _PS5_OTHER.search(_PS5.sub("", title)):
        return _skip(title, price, "PS Portal / VR / разом з іншою консоллю")
    digital = bool(_DIGITAL.search(title))
    # «Digital + Laufwerk» / «Laufwerk für PS5» — дисковод окремо: без слова «Konsole» це аксесуар
    if _PS5_ACCESSORY.search(title) and not _PS5_CONSOLE.search(title):
        return _skip(title, price, "схоже на аксесуар, а не на консоль")
    if price < MIN_PRICE:
        return _skip(title, price, f"дешевше €{MIN_PRICE} — аксесуар, шахрайство або помилка в ціні", False)
    real = PS5_DIGITAL if digital else PS5_DISC
    net_q = real["p25"] - costs(real["p25"])
    cap, good, excellent = net_q / 1.3, net_q / 1.6, net_q / 2.0
    cost = buy_cost(price, ship_in)
    verdict = tier(cost, cap, good, excellent, vb)
    notes = ["Перевір: " + ("" if digital else "працює дисковод, ") + "контролер, немає PSN-блокування. "
             "Лише «Sicher bezahlen» або самовивіз."]
    if price < PS5_SUSPICIOUS_BELOW:
        notes.insert(0, "Підозріло дешево: частина таких оголошень — шахраї. Жодних переказів наперед.")
    return dict(verdict=verdict, type=real["name"], price=price, cap=cap, good=good, excellent=excellent,
                quick_sale=real["p25"], median_sale=real["med"], sell_through=real["st"], profit_est=net_q - cost,
                brand="Sony", title=title, notes=notes, net_q=net_q, item_acc="die PS5",
                check_q=("Läuft alles einwandfrei (Controller), keine PSN-Sperre?" if digital
                         else "Laufen Laufwerk und Controller einwandfrei, keine PSN-Sperre?"),
                buy_cost=cost, ship_in=ship_in, vb=vb)


if __name__ == "__main__":
    import sys

    r = evaluate_console(sys.argv[1], float(sys.argv[2]))
    print(r if r else "не Xbox Series X / PS5 — оцінює ram_alert")
