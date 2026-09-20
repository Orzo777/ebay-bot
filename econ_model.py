"""Економіка однієї угоди: комісії, пересилка, канали збуту, макс. ціна закупівлі.

Чиста математика без мережі і файлів — щоб грошові формули можна було покрити
тестами (див. test_econ.py) і використовувати і в check.py, і в стрічці mydealz.

ДЖЕРЕЛА ЦИФР (станом на 20.09.2026, див. СТРАТЕГІЯ.md розд. 10):
- Комісії eBay.de для КОМЕРЦІЙНИХ продавців діють з 01.07.2026 і рахуються від
  суми, яку платить покупець (товар + доставка), плюс €0,45 за замовлення.
- Пересилка — публічні тарифи перевізників; бізнес-тарифи нижчі на 30–60%.
Обидва блоки треба перечитувати раз на квартал: eBay змінює таблицю комісій.
"""
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Комісії eBay.de
# --------------------------------------------------------------------------- #
# Ставка застосовується до суми з доставкою. Понад FEE_TIER_THRESHOLD частина суми
# рахується за зниженою ставкою FEE_TIER_RATE (для нашого діапазону €30–400 це
# майже ніколи не спрацьовує; лишено, щоб дорогі лоти не рахувались завищено).
FEE_TIER_THRESHOLD = 990.0
FEE_TIER_RATE = 0.03
ORDER_FEE = 0.45

FEE_RATES = {
    "toys": 0.14,       # іграшки/LEGO, beauty, спорт, дім/сад
    "games": 0.12,      # відеоігри, книги, фільми, музика
    "tcg": 0.11,        # збірні картки
    "models": 0.11,     # модельбау
    "tech_new": 0.07,   # комп'ютери, телефони, консолі, ТВ/аудіо, фото — нове
    "tech_used": 0.05,  # те саме, уживане/refurbished
    "coins": 0.065,
    "tires": 0.065,
    "sneakers": 0.07,   # кросівки від €100
}
# Коли категорію визначити не вдалося — беремо НАЙГІРШУ поширену ставку.
UNKNOWN_FEE_GROUP = "toys"


@dataclass
class FeeCategory:
    group: str
    rate: float
    known: bool          # False → ставку не визначили, взяли найгіршу
    source: str = ""     # що саме дало відповідь (для пояснення у виводі)


# Визначення групи за шляхом категорії eBay (categoryPath/categories).
# Перевірка йде зверху вниз: перше збіжне правило виграє.
# Кожне правило: (група, підрядок у шляху (нижній регістр), пояснення).
_PATH_RULES = [
    ("tcg", "sammelkartenspiele", "TCG"),
    ("tcg", "trading card", "TCG"),
    ("models", "modellbau", "модельбау"),
    ("models", "modelleisenbahn", "модельбау"),
    ("coins", "münzen", "монети"),
    ("coins", "muenzen", "монети"),
    ("tires", "reifen", "шини"),
    ("games", "pc- & videospiele", "відеоігри"),
    ("games", "videospiele", "відеоігри"),
    ("games", "bücher", "книги"),
    ("games", "buecher", "книги"),
    ("games", "filme & dvds", "фільми"),
    ("games", "musik-cds", "музика"),
    ("tech_new", "computer, tablets & netzwerk", "техніка"),
    ("tech_new", "handys & kommunikation", "техніка"),
    ("tech_new", "tv, video & audio", "техніка"),
    ("tech_new", "foto & camcorder", "техніка"),
    ("toys", "spielzeug", "іграшки"),
    ("toys", "beauty & gesundheit", "beauty"),
    ("toys", "sport", "спорт"),
    ("toys", "haus & garten", "дім"),
    ("toys", "möbel & wohnen", "дім"),
    ("toys", "moebel & wohnen", "дім"),
    ("toys", "baby", "дитяче"),
]


def fee_category(category_path: str | None, *, condition_id: str | None = "1000",
                 price: float | None = None) -> FeeCategory:
    """categoryPath від eBay ('Spielzeug|Bausets & Konstruktion|...') → ставка.

    Уживана техніка йде за зниженою ставкою (5%), але лише якщо лот справді в
    техніці; для іграшок стан на ставку не впливає.
    """
    path = (category_path or "").lower()
    if not path:
        g = UNKNOWN_FEE_GROUP
        return FeeCategory(g, FEE_RATES[g], False, "категорія невідома → найгірша ставка")
    for group, needle, why in _PATH_RULES:
        if needle in path:
            if group == "tech_new" and str(condition_id or "") != "1000":
                return FeeCategory("tech_used", FEE_RATES["tech_used"], True, why + " (уживане)")
            if group == "sneakers" and (price or 0) < 100:
                continue
            return FeeCategory(group, FEE_RATES[group], True, why)
    g = UNKNOWN_FEE_GROUP
    return FeeCategory(g, FEE_RATES[g], False, f"немає правила для «{path.split('|')[0]}» → найгірша ставка")


def ebay_fee(gross: float, rate: float) -> float:
    """Комісія eBay від суми, яку платить покупець (товар + доставка), + €0,45."""
    if gross <= 0:
        return 0.0
    base = min(gross, FEE_TIER_THRESHOLD)
    over = max(0.0, gross - FEE_TIER_THRESHOLD)
    return round(base * rate + over * FEE_TIER_RATE + ORDER_FEE, 2)


# --------------------------------------------------------------------------- #
# Пересилка
# --------------------------------------------------------------------------- #
# Публічні тарифи (2026). Для бізнес-договору реальні ціни на 30–60% нижчі —
# тоді передавай свою цифру через --ship-cost.
SHIPPING = {
    "hermes_s2s": 3.70,    # Päckchen Shop-to-Shop
    "dhl_paeckchen": 4.19,
    "gls": 4.35,
    "dpd": 4.39,
    "hermes": 4.50,
    "dhl_paket_2kg": 6.19,
    "abholung": 0.0,       # самовивіз / особиста передача
}
DEFAULT_SHIPPING = "dhl_paket_2kg"


def shipping_cost(carrier: str | None = None, override: float | None = None) -> float:
    if override is not None:
        return round(float(override), 2)
    return SHIPPING.get(carrier or DEFAULT_SHIPPING, SHIPPING[DEFAULT_SHIPPING])


# --------------------------------------------------------------------------- #
# Канали збуту
# --------------------------------------------------------------------------- #
@dataclass
class Channel:
    id: str
    name: str
    fee_rate: float | None       # None → береться ставка категорії eBay
    order_fee: float             # фіксована частина з замовлення
    listing_cost: float          # вартість оголошення
    seller_pays_shipping: bool   # чи пересилка є витратою продавця
    price_factor: float          # множник до ринкової ціни eBay
    price_measured: bool         # чи цей множник виміряний (False → припущення!)
    note: str = ""


def channels(*, ka_factor: float = 0.90, ka_listing: float = 2.00) -> dict:
    """ka_factor — у скільки разів ціна на Kleinanzeigen нижча за eBay.

    УВАГА: 0,90 — ПРИПУЩЕННЯ, не вимір. Ціни Kleinanzeigen ми не міряли (їх
    автоматичний парсинг порушує умови сервісу). Тому канали поза eBay завжди
    позначені price_measured=False і не мають самі визначати вердикт BUY.
    """
    return {
        "ebay": Channel("ebay", "eBay.de", None, ORDER_FEE, 0.0, True, 1.0, True,
                        "комісія від суми з доставкою; ціна виміряна по живих лотах"),
        "kleinanzeigen": Channel(
            "kleinanzeigen", "Kleinanzeigen", 0.0, 0.0, ka_listing, False, ka_factor, False,
            "«Sicher bezahlen» платить покупець; 5 оголошень/30 днів безкоштовно, далі ~€2"),
        "vinted": Channel(
            "vinted", "Vinted Pro", 0.0, 0.0, 0.0, False, ka_factor, False,
            "0% з продавця; 14 днів повернення, рахунки, WEEE для електроніки"),
    }


# --------------------------------------------------------------------------- #
# Розрахунок угоди
# --------------------------------------------------------------------------- #
@dataclass
class Deal:
    channel: str
    sale_price: float        # скільки платить покупець (з доставкою, якщо вона в ціні)
    buy_price: float | None
    fee: float
    shipping: float
    listing_cost: float
    net: float | None        # чистими за одиницю
    roi: float | None        # net / buy_price
    max_buy: float           # макс. ціна закупівлі для target_roi
    breakeven_buy: float     # ціна закупівлі, за якої net = 0
    price_measured: bool
    warnings: list = field(default_factory=list)


def deal(sale_price: float, *, fee_rate: float, buy_price: float | None = None,
         channel: Channel, ship: float, target_roi: float = 0.20,
         return_rate: float = 0.0) -> Deal:
    """Економіка однієї одиниці товару в одному каналі.

    sale_price — ринкова ціна eBay (те, що платить покупець). Для інших каналів
    вона множиться на channel.price_factor.
    return_rate — очікувана частка повернень; витрата = r * (2 * пересилка + збір).
    """
    sale = round(sale_price * channel.price_factor, 2)
    rate = channel.fee_rate if channel.fee_rate is not None else fee_rate
    fee = ebay_fee(sale, rate) if channel.id == "ebay" else round(sale * rate, 2)
    ship_cost = ship if channel.seller_pays_shipping else 0.0
    costs = fee + ship_cost + channel.listing_cost
    if return_rate:
        costs += round(return_rate * (2 * max(ship, ship_cost) + ORDER_FEE), 2)
    net = None if buy_price is None else round(sale - costs - buy_price, 2)
    roi = None if (buy_price in (None, 0) or net is None) else round(net / buy_price, 4)
    breakeven = round(sale - costs, 2)
    max_buy = round(breakeven / (1 + target_roi), 2)
    warnings = []
    if not channel.price_measured:
        warnings.append(f"ціна каналу — припущення ×{channel.price_factor} від eBay, не вимір")
    return Deal(channel.id, sale, buy_price, fee, ship_cost, channel.listing_cost,
                net, roi, max(max_buy, 0.0), breakeven, channel.price_measured, warnings)
