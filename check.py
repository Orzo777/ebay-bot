"""«Перевір» — ринок eBay DE + економіка по каналах для конкретного товару.

Вхід: EAN/GTIN, номер набору LEGO або назва (+ опційно ціна закупівлі).
Вихід за секунди, БЕЗ накопичення історії.

Навіщо окремий модуль, а не розширення бота: бот стежить за 9 категоріями за
розкладом і накопичує історію; тут потрібне протилежне — разовий запит про
довільний товар просто зараз. Спільне (EbayClient, quality.listing_flags,
identity.norm, словники відсіву) перевикористано, стан бота не чіпається.

ВИМІРЯНО 20.09.2026 (зонди у scratch, підсумок у СТРАТЕГІЯ.md):
- Пошук за `gtin` має ВИСОКУ точність, але НИЗЬКЕ покриття: для LEGO 10354
  gtin-пошук дав 0 лотів, хоча лот саме з цим GTIN існує (перевірено getItem).
  Тому GTIN — це підтвердження ідентичності, а НЕ джерело ринкових цін.
- `item_summary/search` уже повертає `categories`/`leafCategoryIds`/`epid`,
  тож категорію комісії видно без додаткового виклику getItem.
- `epid` групує лоти надто широко: під epid DualSense опинились і звичайний
  білий (€88), і Elden Ring Edition (€116). Тому epid теж не еталон ціни.

Виклики API на одну перевірку: 1 (ринок) + 1 (gtin, якщо заданий) + N (швидкість,
типово 10) ≈ 11. Квота 5000/добу СПІЛЬНА з ботом — див. --no-velocity для економії.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

import config
import econ_model as E
import identity
import quality

# --------------------------------------------------------------------------- #
# Пороги вердикту
# --------------------------------------------------------------------------- #
MIN_LOTS = 5           # менше — ринок надто тонкий, щоб рахувати медіану
MIN_SELLERS = 3        # щоб один продавець не «створював ринок»
MAX_DISPERSION = 0.45  # (q3-q1)/median; вище — у вибірці, ймовірно, різні товари
MIN_PROFIT = 15.0      # «€10–20 прибутку — смішно» (вимога користувача)
MIN_ROI = 0.15
MAX_WEEKS_TO_SELL = 6.0   # довше — капітал стоїть у товарі надто довго
LOW5_N = 5             # «швидка» ціна = медіана 5 найдешевших (по продавцях)
VELOCITY_SAMPLE = 10   # скільки лотів опитати через getItem; на реальних даних
                       # вибірка <8 давала розкид «тижнів до продажу» у рази
PRICE_BAND_LO = 0.50   # придатні ціни: від 50% медіани...
PRICE_BAND_HI = 2.00   # ...до 200% (решта — майже напевно інший товар)
MIN_BAND_LOTS = 6      # діапазон вмикається лише коли лотів достатньо
MAX_BAND_DROP = 0.40   # якщо діапазон викинув більше — вибірка неоднорідна

ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/"


# --------------------------------------------------------------------------- #
# Ідентифікація товару
# --------------------------------------------------------------------------- #
@dataclass
class Product:
    query: str                       # що шукаємо на eBay
    must: list = field(default_factory=list)   # групи токенів: кожна має бути в назві
    exclude: list = field(default_factory=list)  # додаткові слова-виключення
    gtin: str | None = None
    label: str = ""                  # людська назва
    source: str = ""                 # як ідентифікували
    ambiguous: list = field(default_factory=list)  # кандидати, якщо неоднозначно


# Слова, які самі по собі не ідентифікують товар (не йдуть у must).
_Q_STOP = {
    "neu", "new", "ovp", "original", "originalverpackt", "versiegelt", "sealed",
    "und", "and", "der", "die", "das", "mit", "von", "fuer", "for", "the", "in",
    "set", "edition", "box", "pack", "packung", "stueck", "stk", "top",
}
# Товар = сам предмет, а не запчастина/аксесуар/друк/порожня коробка до нього.
_CHECK_DENY_RE = [
    (re.compile(r"\b(ersatzteil|ersatzteile|einzelteil|einzelteile|steine einzeln)\b"), "запчастина"),
    (re.compile(r"\b(anleitung|bauanleitung|instructions?|manual|handbuch)\b"), "лише інструкція"),
    (re.compile(r"\b(aufkleber|sticker|stickerbogen|decals?)\b"), "лише наліпки"),
    (re.compile(r"\b(minifigur|minifiguren|minifig|minifigs|figur einzeln)\b"), "лише фігурка"),
    (re.compile(r"\b(polybag|poly bag|beutel)\b"), "polybag, не набір"),
    (re.compile(r"\b(karton|umkarton|versandkarton)\b"), "лише коробка"),
    (re.compile(r"\b(poster|plakat|kunstdruck|leinwand|bild|print)\b"), "постер/друк"),
    (re.compile(r"\b(lichtset|licht set|beleuchtungsset|beleuchtung|led ?set|lightailing)\b"),
     "підсвітка (сторонній аксесуар)"),
    (re.compile(r"\b(schluesselanhaenger|magnet|tasse|t ?shirt|kissen|puzzle)\b"), "сувенір"),
    # «Tasche mit tiptoi Stift Starterset» — продається сумка, а не товар
    (re.compile(r"^(tasche|huelle|case|etui|koffer|aufbewahrung|aufbewahrungsbox"
                r"|transportbox|rucksack|organizer|tragetasche)\b"), "тара/сумка, не товар"),
    # Витратні матеріали: у нашому бізнесі це ніколи не головний товар
    # (користувач прямо відмовився від дешевих витратних), а на запиті
    # «Oral-B iO 6» їх було більше, ніж самих щіток, і вони ставали «ринком».
    (re.compile(r"\b(akku|akkus|tauschakku|batterie|batterien|battery|netzteil"
                r"|ladekabel|ladestation|ladegeraet|charger)\b"), "запчастина/зарядка"),
    (re.compile(r"\b(aufsteckbuersten?|buerstenkopf|buerstenkoepfe|brush ?heads?)\b"),
     "насадки"),
    (re.compile(r"\b(gebraucht|used|bespielt|vollstaendig geprueft)\b"), "уживане"),
    (re.compile(r"\b(digital|code|key|download)\b"), "цифрова версія"),
]
# Витратні матеріали й запчастини. ПОЗИЦІЙНЕ правило: відсіваємо лише коли слово
# стоїть ПЕРЕД назвою товару («Ersatz Akku für Oral-B iO 6» — це запчастина), але
# не коли після («Oral-B iO 6 … Reiseetui» — це сама щітка з футляром).
# Абсолютне правило давало хибні виключення: реальний лот за €90,01 випадав
# із ринку лише через слово «Reiseetui» у хвості назви.
_POSITIONAL_DENY_RE = [
    (re.compile(r"\b(reiseetui|travel ?case|etui|dongle|zubehoer|halterung|staender"
                r"|schutzhuelle|schutzfolie)\b"), "аксесуар"),
]
# Кілька штук в одному лоті: ціна не порівнянна з ціною однієї одиниці.
_MULTI_RE = re.compile(
    r"(?:^|\s)(\d{1,2})\s?x(?=\s|$)"
    r"|\bx\s?(\d{1,2})(?=\s|$)"
    r"|\b(\d{1,2})er[ -]?(?:set|pack|packung|bundle)\b"
    r"|\b(?:doppelpack|dreierpack|viererpack|multipack|sparpack|bundle|buendel)\b"
    r"|\bset of (\d{1,2})\b|\blot of (\d{1,2})\b"
)
_LEGO_SET_RE = re.compile(r"^\d{4,7}$")
_EAN_RE = re.compile(r"^\d{8}$|^\d{12,14}$")


def product_from_args(ean=None, lego=None, query=None, label=None,
                      must=None, exclude=None) -> Product:
    """Побудувати Product з того, що дав користувач (без мережі).

    must/exclude — ручне уточнення: слова, які ОБОВ'ЯЗКОВО мають бути в назві
    лота, і слова, що відсівають лот. Потрібні там, де назва неоднозначна
    («Oral-B iO 6» проти «Oral-B iO Kids 6+»).
    """
    extra_must = [{identity.norm(w)} for w in (must or [])]
    exclude = [identity.norm(w) for w in (exclude or [])]
    if ean:
        ean = str(ean).strip()
        if not _EAN_RE.match(ean):
            raise ValueError(f"«{ean}» не схоже на EAN/GTIN (8, 12–14 цифр)")
        return Product(query=query or "", must=extra_must, exclude=exclude, gtin=ean,
                       label=label or f"EAN {ean}", source="gtin")
    if lego:
        num = str(lego).strip()
        if not _LEGO_SET_RE.match(num):
            raise ValueError(f"«{num}» не схоже на номер набору LEGO (4–7 цифр)")
        return Product(query=f"LEGO {num}", must=[{num}] + extra_must, exclude=exclude,
                       label=label or f"LEGO {num}", source="номер набору")
    if query:
        return Product(query=query, must=must_from_query(query) + extra_must,
                       exclude=exclude, label=label or query, source="назва")
    raise ValueError("Потрібен один із: --ean, --lego, --query")


def must_from_query(query: str) -> list:
    """Назва → групи обов'язкових токенів.

    Числа/артикули важливіші за слова: якщо в запиті є число з 4+ цифр, воно
    стає єдиною обов'язковою умовою (це майже завжди номер моделі/набору).
    """
    toks = [t for t in re.split(r"[^a-z0-9]+", identity.norm(query)) if t]
    long_nums = [t for t in toks if t.isdigit() and len(t) >= 4]
    if long_nums:                       # артикул/номер набору — його достатньо
        return [{n} for n in long_nums]
    # Короткі номери моделей («iO 6», «Evolve2 65», «NT1») теж обов'язкові:
    # без них «Oral-B iO 6» зіставлявся з «Oral-B iO Kids 6+».
    nums = [t for t in toks if any(c.isdigit() for c in t)]
    words = [t for t in toks if t not in _Q_STOP and len(t) >= 3 and not t.isdigit()]
    must, seen = [], set()
    for grp in [{w} for w in words[:3]] + [{n} for n in nums[:2]]:
        key = frozenset(grp)
        if key not in seen:
            seen.add(key)
            must.append(grp)
    return must or [{w} for w in words[:4]]


# --------------------------------------------------------------------------- #
# Відбір лотів
# --------------------------------------------------------------------------- #
def match_reasons(title: str, product: Product) -> list:
    """Порожній список = лот справді про цей товар. Інакше — причини відсіву."""
    n = identity.norm(title)
    toks = [t for t in re.split(r"[^a-z0-9]+", n) if t]
    tset = set(toks)
    reasons = []

    for i, grp in enumerate(product.must):
        # артикул може бути злитий з іншим текстом («lego10354», «nr.10354»),
        # тому шукаємо і як токен, і як підрядок
        hit_at = min((n.find(w) for w in grp if w in n), default=-1)
        if not (tset & grp) and hit_at < 0:
            reasons.append("немає:" + "/".join(sorted(grp))[:24])
            continue
        # «für 10354», «passend für Lego10354» → аксесуар до товару, а не товар
        if i == 0 and hit_at > 0:
            head = n[:hit_at]
            if any(re.search(r"\b" + w + r"\b", head) for w in identity._FOR_WORDS):
                reasons.append("аксесуар-für")
            for rx, why in _POSITIONAL_DENY_RE:
                if rx.search(head):
                    reasons.append(why)

    for t in toks:
        r = identity.GLOBAL_EXCLUDE_TOK.get(t)
        if r:
            reasons.append(r)
    for rx, r in identity.GLOBAL_EXCLUDE_RE:
        if rx.search(n):
            reasons.append(r)
    for rx, r in _CHECK_DENY_RE:
        if rx.search(n):
            reasons.append(r)
    for w in product.exclude:
        if w and w in n:
            reasons.append("виключено:" + w)
    for w in config.CONDITION_BLOCKLIST:
        if identity.norm(w) in n:
            reasons.append("пошкоджене")
    if _MULTI_RE.search(n):
        reasons.append("кілька штук у лоті")
    for t in toks:
        if t in identity.GRADED_TOK:
            reasons.append("graded")
    return sorted(set(reasons))


# --------------------------------------------------------------------------- #
# Ринок
# --------------------------------------------------------------------------- #
@dataclass
class Market:
    n_seen: int = 0
    n_kept: int = 0
    n_sellers: int = 0
    total_reported: int = 0
    band_dropped: int = 0        # відсіяно за ціновим діапазоном
    dropped: dict = field(default_factory=dict)
    prices: list = field(default_factory=list)       # по одній ціні на продавця
    minimum: float | None = None
    low5: float | None = None
    median: float | None = None
    dispersion: float | None = None
    category_path: str | None = None
    condition_id: str | None = None
    kept_items: list = field(default_factory=list)
    velocity_items: list = field(default_factory=list)   # лоти біля «швидкої» ціни
    samples: list = field(default_factory=list)      # (назва, ціна, продавець)


def _drop(d: dict, reason: str) -> None:
    d[reason] = d.get(reason, 0) + 1


def build_market(items: list, product: Product, total_reported: int = 0) -> Market:
    """Чиста функція: список item_summary → ринкова картина. Без мережі."""
    m = Market(n_seen=len(items), total_reported=total_reported)
    kept = []
    for it in items:
        flags = quality.listing_flags(it)
        if flags:
            _drop(m.dropped, flags[0])
            continue
        reasons = match_reasons(it.get("title") or "", product)
        if reasons:
            _drop(m.dropped, reasons[0])
            continue
        price = _total_price(it)
        if price is None:
            _drop(m.dropped, "немає ціни")
            continue
        kept.append((price, _seller(it), it))

    if not kept:
        return m

    # --- ціновий діапазон -------------------------------------------------
    # Словникові фільтри ніколи не відловлять увесь сміттєвий хвіст (на LEGO
    # 10354 крізь них пройшли постери за €18 і чужий світловий набір за €102 —
    # і зіпсували «швидку ціну» з €215 до €30). Тому після словників ще раз
    # відсікаємо за ціною: усе, що далеко від медіани, — це майже напевно ІНШИЙ
    # товар, а не знахідка. Орієнтир ринку має бути стійким; вигідні пропозиції
    # ми шукаємо не тут, а порівнянням із роздрібною ціною.
    if len(kept) >= MIN_BAND_LOTS:
        rough = statistics.median([p for p, _s, _i in kept])
        lo, hi = rough * PRICE_BAND_LO, rough * PRICE_BAND_HI
        inside = [r for r in kept if lo <= r[0] <= hi]
        m.band_dropped = len(kept) - len(inside)
        if m.band_dropped:
            m.dropped["ціна поза діапазоном ринку"] = m.band_dropped
        kept = inside
    if not kept:
        return m

    m.n_kept = len(kept)
    m.kept_items = [it for _p, _s, it in kept]
    kept.sort(key=lambda r: r[0])
    m.samples = [(it.get("title", "")[:58], p, s) for p, s, it in kept[:8]]
    per_seller = quality.per_seller_prices([(p, s, it.get("itemId")) for p, s, it in kept])
    vals = sorted(per_seller.values())
    m.n_sellers = len(vals)
    m.prices = vals
    m.minimum = vals[0]
    m.low5 = statistics.median(vals[:LOW5_N])
    m.median = statistics.median(vals)
    if len(vals) >= 2:
        q = statistics.quantiles(vals, n=4, method="inclusive")
        m.dispersion = round((q[2] - q[0]) / m.median, 3) if m.median else None
    # Категорія — НАЙЧАСТІША серед придатних лотів, а не категорія найдешевшого
    # (найдешевшим виявлявся постер у «Figuren|Fantasy» → неправильна комісія).
    paths = Counter(_category_path(it) for it in m.kept_items if _category_path(it))
    m.category_path = paths.most_common(1)[0][0] if paths else None
    conds = Counter(str(it.get("conditionId") or "") for it in m.kept_items)
    m.condition_id = conds.most_common(1)[0][0]
    # Лоти для заміру швидкості: РІВНОМІРНО по всьому ціновому діапазону.
    # Спершу бралися найдешевші — і замір виходив зміщеним: унизу стоять дрібні
    # продавці без обороту, а дилери із запасом сидять вище по ціні, тож
    # «тижні до продажу» стрибали з ∞ до 1,7 залежно від розміру вибірки.
    m.velocity_items = _spread(kept)
    return m


def _spread(kept: list) -> list:
    """Лоти, відсортовані за ціною → порядок обходу, що рівномірно покриває
    діапазон: спочатку найдешевший, найдорожчий і середина, далі — решта."""
    n = len(kept)
    if n <= 2:
        return [it for _p, _s, it in kept]
    order, used = [], set()
    # позиції 0, n-1, n//2, потім поділ навпіл щоразу дрібніше
    step = n - 1
    picks = [0, n - 1]
    while step > 1:
        step = max(1, step // 2)
        picks += list(range(0, n, step))
    for i in picks:
        if i not in used:
            used.add(i)
            order.append(kept[i][2])
    for i in range(n):
        if i not in used:
            order.append(kept[i][2])
    return order


def _total_price(item: dict):
    from main import total_price
    return total_price(item)


def _seller(item: dict) -> str:
    return (item.get("seller") or {}).get("username") or "?"


def _category_path(item: dict) -> str | None:
    """categoryPath із getItem або зібраний зі списку `categories` у summary."""
    if item.get("categoryPath"):
        return item["categoryPath"]
    cats = item.get("categories") or []
    if cats:
        return "|".join(c.get("categoryName", "") for c in cats)
    return None


# --------------------------------------------------------------------------- #
# Швидкість продажів
# --------------------------------------------------------------------------- #
@dataclass
class Velocity:
    per_listing_week: float | None = None   # шт/тиж НА ОДИН лот (інваріант до вибірки)
    weeks_to_sell: float | None = None      # очікувані тижні до продажу одиниці
    market_week: float | None = None        # оцінка обороту всього ринку, шт/тиж
    per_week: float | None = None           # сумарно по вибірці (сире, для довідки)
    n_lots: int = 0          # опитано лотів
    n_with_data: int = 0     # з них мали запас/продажі >1
    n_market_lots: int = 0   # скільки придатних лотів на ринку всього
    sold_total: int = 0
    youngest_days: float | None = None
    confidence: str = "немає даних"
    note: str = ""


def build_velocity(details: list, now: datetime | None = None,
                   n_market_lots: int = 0) -> Velocity:
    """details — відповіді getItem. Чиста функція, без мережі.

    ЧОМУ НЕ СУМА. Спершу метрикою була сума продажів по вибірці — і вона
    механічно росла з розміром вибірки (Oral-B iO 6: 4 лоти → 0,0 шт/тиж;
    10 лотів → 2,4; 14 лотів → 7,5), а вердикт через це перевертався.
    Тому рахуємо СЕРЕДНЄ НА ОДИН ЛОТ: воно не залежить від того, скільки лотів
    ми опитали. З нього випливає головне число — скільки тижнів чекати продажу
    одиниці (1 / шт-на-лот-за-тиждень).

    ВАЖЛИВО: eBay показує продажі лише для лотів із кількістю >1, тобто для
    дилерів із запасом. Поштучний лот (наш випадок) продається повільніше, ніж
    така оцінка, — це верхня межа швидкості, а не прогноз.
    """
    now = now or datetime.now(timezone.utc)
    v = Velocity(n_lots=len(details), n_market_lots=n_market_lots)
    rates, ages = [], []
    for det in details:
        ea = (det.get("estimatedAvailabilities") or [{}])[0]
        sold = ea.get("estimatedSoldQuantity") or 0
        avail = ea.get("estimatedAvailableQuantity") or 0
        age = _age_days(det.get("itemCreationDate"), now)
        if (avail > 1 or sold > 0) and age:
            v.n_with_data += 1
            v.sold_total += sold
            rates.append(sold / age * 7)
            ages.append(age)
    if rates:
        v.per_week = round(sum(rates), 1)
        v.per_listing_week = round(sum(rates) / len(rates), 3)
        v.youngest_days = round(min(ages), 1)
        if v.per_listing_week > 0:
            v.weeks_to_sell = round(1 / v.per_listing_week, 1)
        if n_market_lots:
            v.market_week = round(v.per_listing_week * n_market_lots, 1)

    if v.n_with_data == 0:
        v.confidence = "не виміряно"
        v.note = "усі опитані лоти — поштучні (кількість 1), eBay не показує проданих"
    elif v.per_listing_week == 0:
        v.confidence = "низька"
        v.note = f"{v.n_with_data} лот(и) із запасом, але жодного продажу"
    elif v.n_with_data < 4 or v.sold_total < 10:
        v.confidence = "низька"
        v.note = f"лише {v.n_with_data} лот(и) з даними, продано {v.sold_total}"
    elif v.youngest_days and v.youngest_days < 7:
        v.confidence = "середня"
        v.note = f"наймолодший лот {v.youngest_days} дн — екстраполяція шумна"
    else:
        v.confidence = "висока"
    if v.n_with_data:
        v.note = (v.note + "; " if v.note else "") + \
            "міряно по лотах дилерів із запасом — поштучний лот буде повільнішим"
    return v


def _age_days(created: str | None, now: datetime):
    if not created:
        return None
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(1.0, (now - dt).total_seconds() / 86400)


# --------------------------------------------------------------------------- #
# Вердикт
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    product: Product
    market: Market
    velocity: Velocity
    fee: E.FeeCategory
    deals: dict = field(default_factory=dict)     # channel_id -> E.Deal
    patient: "E.Deal | None" = None               # eBay за медіаною (продаж не поспіхом)
    verdict: str = "UNKNOWN"
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def evaluate(product: Product, market: Market, velocity: Velocity, *,
             buy_price: float | None = None, ship: float | None = None,
             carrier: str | None = None, target_roi: float = 0.20,
             ka_factor: float = 0.90, return_rate: float = 0.0,
             min_profit: float = MIN_PROFIT, min_roi: float = MIN_ROI) -> Result:
    """Ринок + швидкість + ціна закупівлі → BUY / SKIP / UNKNOWN. Без мережі."""
    fee = E.fee_category(market.category_path, condition_id=market.condition_id,
                         price=market.low5)
    res = Result(product=product, market=market, velocity=velocity, fee=fee)
    ship_cost = E.shipping_cost(carrier, ship)

    if market.low5 is None:
        res.verdict = "UNKNOWN"
        res.reasons.append("жодного придатного лота — товар не знайдено або все відсіяно")
        return res

    chans = E.channels(ka_factor=ka_factor)
    for cid, ch in chans.items():
        res.deals[cid] = E.deal(market.low5, fee_rate=fee.rate, buy_price=buy_price,
                                channel=ch, ship=ship_cost, target_roi=target_roi,
                                return_rate=return_rate)

    if market.median is not None:
        res.patient = E.deal(market.median, fee_rate=fee.rate, buy_price=buy_price,
                             channel=chans["ebay"], ship=ship_cost,
                             target_roi=target_roi, return_rate=return_rate)
    # Неспецифічний запит — головна причина хибних зіставлень («Oral-B iO 6»
    # зловив «Oral-B iO Kids 6+»). Краще сказати про це прямо, ніж дати числа.
    distinctive = sum(1 for grp in product.must
                      if any(len(w) >= 5 or any(c.isdigit() for c in w) for w in grp))
    if product.source == "назва" and distinctive < 2:
        res.warnings.append("запит неспецифічний (немає артикула/моделі) — "
                            "додай --must <слово> або перевіряй за EAN/номером набору")
    # Ми бачимо лише першу сторінку (limit=200, порядок best_match). Якщо лотів
    # більше — вибірка не є ринком цього товару, а зрізом дуже широкого запиту.
    if market.total_reported > market.n_seen:
        res.warnings.append(
            f"eBay повідомляє {market.total_reported} лотів за запитом, ми бачили "
            f"{market.n_seen} — запит завеликий, звузь його (--must / точніша назва)")
    if not fee.known:
        res.warnings.append(f"комісія: {fee.source} ({fee.rate:.0%}) — перевір вручну")
    if market.n_kept < MIN_LOTS:
        res.reasons.append(f"мало лотів: {market.n_kept} < {MIN_LOTS}")
    if market.n_sellers < MIN_SELLERS:
        res.reasons.append(f"мало продавців: {market.n_sellers} < {MIN_SELLERS}")
    if market.dispersion is not None and market.dispersion > MAX_DISPERSION:
        res.reasons.append(f"ціни розкидані (IQR/медіана {market.dispersion}) — "
                           f"ймовірно, у вибірці різні варіанти товару")
    band_total = market.n_kept + market.band_dropped
    if band_total and market.band_dropped / band_total > MAX_BAND_DROP:
        res.reasons.append(
            f"вибірка неоднорідна: {market.band_dropped} з {band_total} лотів "
            f"випали за ціною — ймовірно, під одним запитом кілька різних товарів")

    if res.reasons:
        res.verdict = "UNKNOWN"
        return res

    if buy_price is None:
        res.verdict = "UNKNOWN"
        res.reasons.append("ціну закупівлі не задано — показано лише орієнтири")
        return res

    d = res.deals["ebay"]
    if d.net is not None and d.net >= min_profit and (d.roi or 0) >= min_roi:
        if velocity.weeks_to_sell is None:
            res.verdict = "UNKNOWN"
            res.reasons.append("економіка сходиться, але швидкість продажів не виміряна")
        elif velocity.weeks_to_sell > MAX_WEEKS_TO_SELL:
            res.verdict = "SKIP"
            res.reasons.append(
                f"повільно: ~{velocity.weeks_to_sell} тиж до продажу "
                f"(> {MAX_WEEKS_TO_SELL:.0f}) — капітал стоятиме")
        else:
            res.verdict = "BUY"
            res.reasons.append(f"eBay: чистими €{d.net} ({d.roi:.0%}), "
                               f"~{velocity.weeks_to_sell} тиж до продажу")
    else:
        res.verdict = "SKIP"
        if d.net is None:
            res.reasons.append("немає ціни закупівлі")
        else:
            miss = []
            if d.net < min_profit:
                miss.append(f"прибуток €{d.net} < €{min_profit:.0f}")
            if (d.roi or 0) < min_roi:
                miss.append(f"ROI {(d.roi or 0):.0%} < {min_roi:.0%}")
            res.reasons.append(f"eBay: чистими €{d.net} ({(d.roi or 0):.0%}) — "
                               + "; ".join(miss))
            best = max((x for x in res.deals.values() if x.net is not None),
                       key=lambda x: x.net, default=None)
            if best and best.channel != "ebay" and best.net >= min_profit:
                res.warnings.append(
                    f"поза eBay було б краще ({best.channel}: €{best.net}), "
                    f"але ціна того каналу — припущення, не вимір")
    return res


# --------------------------------------------------------------------------- #
# Мережевий шар (тонкий; у тестах підміняється)
# --------------------------------------------------------------------------- #
class Fetcher:
    """Обгортка над eBay Browse API зі лічильником викликів."""

    def __init__(self, client=None, country: str = "DE"):
        from main import EbayClient
        self.client = client or EbayClient()
        self.country = country
        self.calls = 0

    def _get(self, url, params):
        from main import _request_with_backoff
        self.calls += 1
        return _request_with_backoff("GET", url, headers=self.client._headers(),
                                     params=params)

    def search(self, *, q=None, gtin=None, min_price=1, limit=200):
        flt = config.search_filter(min_price)
        if self.country:
            flt += f",itemLocationCountry:{self.country}"
        params = {"filter": flt, "limit": limit}
        if q:
            params["q"] = q
        if gtin:
            params["gtin"] = gtin
        data = self._get(config.EBAY_BROWSE_SEARCH_URL, params)
        return (data.get("itemSummaries") or []), int(data.get("total") or 0)

    def item(self, item_id):
        return self._get(ITEM_URL + item_id, {})


def resolve(fetcher: Fetcher, product: Product) -> Product:
    """Доповнити Product даними з eBay (для EAN — дізнатись назву/артикул).

    GTIN дає високу точність, але низьке покриття, тому назву/артикул із нього
    ми використовуємо лише щоб побудувати ЗАПИТ, а ринок міряємо звичайним
    пошуком (інакше видно 0–3 лоти замість 70).
    """
    if not product.gtin or product.query:
        return product
    items, _tot = fetcher.search(gtin=product.gtin, limit=10)
    if not items:
        raise LookupError(
            f"eBay не знає GTIN {product.gtin} (індекс GTIN неповний). "
            f"Спробуй --query «<бренд> <модель>» або --lego <номер>.")
    det = fetcher.item(items[0]["itemId"])
    brand = (det.get("brand") or "").strip()
    mpn = (det.get("mpn") or "").split(",")[0].strip()
    title = det.get("title") or items[0].get("title") or ""
    if brand and mpn:
        product.query = f"{brand} {mpn}"
        product.must = must_from_query(f"{brand} {mpn}")
        product.source = f"gtin → {brand} {mpn}"
    else:
        product.query = " ".join(title.split()[:6])
        product.must = must_from_query(product.query)
        product.source = f"gtin → назва лота: {title[:40]}"
    product.label = title[:70] or product.label
    return product


def run_check(fetcher: Fetcher, product: Product, *, min_price=1,
              velocity_sample=VELOCITY_SAMPLE, **kw) -> Result:
    product = resolve(fetcher, product)
    items, total = fetcher.search(q=product.query, min_price=min_price)
    market = build_market(items, product, total_reported=total)
    details = []
    if velocity_sample and market.velocity_items:
        for it in market.velocity_items[:velocity_sample]:
            try:
                details.append(fetcher.item(it["itemId"]))
            except Exception as exc:                      # не валимо перевірку
                print(f"  (getItem {it['itemId']} не вдався: {exc})", file=sys.stderr)
    velocity = build_velocity(details, n_market_lots=market.n_kept)
    return evaluate(product, market, velocity, **kw)


# --------------------------------------------------------------------------- #
# Вивід
# --------------------------------------------------------------------------- #
_ICON = {"BUY": "✅ BUY", "SKIP": "❌ SKIP", "UNKNOWN": "❔ UNKNOWN"}


def format_result(res: Result, *, calls: int | None = None, verbose: bool = False) -> str:
    m, v, out = res.market, res.velocity, []
    out.append(f"{_ICON.get(res.verdict, res.verdict)}  —  {res.product.label}")
    out.append(f"запит eBay: «{res.product.query}»  (ідентифікація: {res.product.source})")
    out.append("")
    out.append(f"РИНОК (eBay DE, нове, фікс. ціна): підійшло {m.n_kept} лотів "
               f"від {m.n_sellers} продавців (з {m.n_seen} переглянутих, "
               f"eBay повідомив total={m.total_reported})")
    if m.low5 is not None:
        out.append(f"  ціни з доставкою, по одній на продавця: найдешевший продавець "
                   f"€{m.minimum:.2f} | "
                   f"швидка (медіана {LOW5_N} найдешевших) €{m.low5:.2f} | "
                   f"медіана €{m.median:.2f} | розкид {m.dispersion}")
    if m.dropped:
        top = sorted(m.dropped.items(), key=lambda kv: -kv[1])[:6]
        out.append("  відсіяно: " + ", ".join(f"{k}×{n}" for k, n in top))
    if verbose and m.samples:
        out.append("  найдешевші придатні лоти:")
        for t, p, s in m.samples:
            out.append(f"    €{p:>8.2f}  {t}  [{s}]")

    out.append("")
    if v.per_listing_week is not None:
        wts = "—" if v.weeks_to_sell is None else f"~{v.weeks_to_sell} тиж"
        mk = "" if v.market_week is None else f", увесь ринок ≈{v.market_week} шт/тиж"
        out.append(f"ШВИДКІСТЬ: {wts} до продажу однієї штуки "
                   f"({v.per_listing_week} шт/тиж на лот{mk}) — "
                   f"впевненість: {v.confidence}")
        out.append(f"  вибірка {v.n_lots} лотів, {v.n_with_data} з даними, "
                   f"продано {v.sold_total}")
    else:
        out.append("ШВИДКІСТЬ: не виміряна")
    if v.note:
        out.append(f"  ({v.note})")

    out.append("")
    out.append(f"КОМІСІЯ: {res.fee.rate:.1%} ({res.fee.source})"
               + ("" if res.fee.known else "  ⚠ визначено НЕ точно"))
    if m.category_path:
        out.append(f"  категорія eBay: {m.category_path[:80]}")
    if res.deals:
        out.append("")
        out.append(f"  {'канал':16}{'ціна':>9}{'комісія':>9}{'пересил':>9}"
                   f"{'чистими':>10}{'ROI':>7}{'макс.закупівля':>16}")
        for cid in ("ebay", "kleinanzeigen", "vinted"):
            d = res.deals.get(cid)
            if not d:
                continue
            net = f"{d.net:+.2f}" if d.net is not None else "—"
            roi = f"{d.roi:.0%}" if d.roi is not None else "—"
            mark = "" if d.price_measured else " *"
            out.append(f"  {cid:16}{d.sale_price:>9.2f}{d.fee:>9.2f}"
                       f"{d.shipping + d.listing_cost:>9.2f}{net:>10}{roi:>7}"
                       f"{d.max_buy:>14.2f}{mark}")
        if any(not d.price_measured for d in res.deals.values()):
            out.append("  * ціна каналу — припущення від ціни eBay, не вимір")
        if res.patient:
            pn = f"{res.patient.net:+.2f}" if res.patient.net is not None else "—"
            pr = f" ({res.patient.roi:.0%})" if res.patient.roi is not None else ""
            out.append(f"  eBay за МЕДІАНОЮ €{res.patient.sale_price:.2f} "
                       f"(продаж не поспіхом): чистими {pn}{pr}, "
                       f"макс.закупівля €{res.patient.max_buy:.2f}")

    out.append("")
    for r in res.reasons:
        out.append(f"  → {r}")
    for w in res.warnings:
        out.append(f"  ⚠ {w}")
    if calls is not None:
        out.append(f"\n(викликів eBay API: {calls})")
    return "\n".join(out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Перевірити товар: ринок eBay DE + економіка")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--ean", help="EAN/GTIN (8, 12–14 цифр)")
    g.add_argument("--lego", help="номер набору LEGO (4–7 цифр)")
    g.add_argument("--query", help="назва товару")
    p.add_argument("--must", action="append", default=[],
                   help="слово, яке ОБОВ'ЯЗКОВО має бути в назві лота (можна кілька разів)")
    p.add_argument("--exclude", action="append", default=[],
                   help="слово, що відсіває лот (можна кілька разів)")
    p.add_argument("--buy", type=float, help="ціна закупівлі, € (з доставкою до нас)")
    p.add_argument("--carrier", default=E.DEFAULT_SHIPPING,
                   choices=sorted(E.SHIPPING), help="перевізник для відправки")
    p.add_argument("--ship-cost", type=float, help="своя ціна пересилки, € (бізнес-тариф)")
    p.add_argument("--target-roi", type=float, default=0.20, help="цільовий ROI (0.20 = 20%%)")
    p.add_argument("--min-profit", type=float, default=MIN_PROFIT)
    p.add_argument("--min-roi", type=float, default=MIN_ROI)
    p.add_argument("--ka-factor", type=float, default=0.90,
                   help="ціна Kleinanzeigen як частка від eBay (ПРИПУЩЕННЯ)")
    p.add_argument("--return-rate", type=float, default=0.0, help="частка повернень, 0.03 = 3%%")
    p.add_argument("--min-price", type=float, default=1, help="нижня межа ціни в пошуку")
    p.add_argument("--country", default="DE")
    p.add_argument("--no-velocity", action="store_true", help="без getItem (економія квоти)")
    p.add_argument("--velocity-sample", type=int, default=VELOCITY_SAMPLE)
    p.add_argument("--json", action="store_true", help="вивід у JSON")
    p.add_argument("-v", "--verbose", action="store_true", help="показати лоти")
    a = p.parse_args(argv)

    try:
        product = product_from_args(ean=a.ean, lego=a.lego, query=a.query,
                                    must=a.must, exclude=a.exclude)
    except ValueError as exc:
        print(f"Помилка: {exc}")
        return 2

    _logs_to_stderr()
    fetcher = Fetcher(country=a.country)
    try:
        res = run_check(
            fetcher, product, min_price=a.min_price,
            velocity_sample=0 if a.no_velocity else a.velocity_sample,
            buy_price=a.buy, ship=a.ship_cost, carrier=a.carrier,
            target_roi=a.target_roi, ka_factor=a.ka_factor,
            return_rate=a.return_rate, min_profit=a.min_profit, min_roi=a.min_roi)
    except LookupError as exc:
        print(f"Помилка: {exc}")
        return 2

    if a.json:
        print(json.dumps(_as_dict(res, fetcher.calls), ensure_ascii=False, indent=2))
    else:
        print(format_result(res, calls=fetcher.calls, verbose=a.verbose))
    return 0 if res.verdict != "UNKNOWN" else 1


def _logs_to_stderr() -> None:
    """main.log() пише в stdout — це ламало `--json` (рядок про OAuth-токен
    потрапляв у потік даних). У CLI-режимі відправляємо технічні логи в stderr."""
    import main as _m
    _m.log = lambda msg="": print(msg, file=sys.stderr, flush=True)


def _as_dict(res: Result, calls: int) -> dict:
    m = res.market
    return {
        "verdict": res.verdict, "label": res.product.label, "query": res.product.query,
        "identified_by": res.product.source,
        "market": {"kept": m.n_kept, "seen": m.n_seen, "sellers": m.n_sellers,
                   "total_reported": m.total_reported, "min": m.minimum,
                   "fast_price": m.low5, "median": m.median,
                   "dispersion": m.dispersion, "dropped": m.dropped,
                   "category_path": m.category_path},
        "velocity": {"weeks_to_sell": res.velocity.weeks_to_sell,
                     "per_listing_week": res.velocity.per_listing_week,
                     "market_week": res.velocity.market_week,
                     "per_week_sampled": res.velocity.per_week,
                     "confidence": res.velocity.confidence,
                     "lots": res.velocity.n_lots, "with_data": res.velocity.n_with_data,
                     "sold_total": res.velocity.sold_total, "note": res.velocity.note},
        "fee": {"rate": res.fee.rate, "group": res.fee.group, "known": res.fee.known,
                "source": res.fee.source},
        "channels": {k: {"sale": d.sale_price, "fee": d.fee, "shipping": d.shipping,
                         "listing": d.listing_cost, "net": d.net, "roi": d.roi,
                         "max_buy": d.max_buy, "breakeven_buy": d.breakeven_buy,
                         "price_measured": d.price_measured}
                     for k, d in res.deals.items()},
        "patient_ebay": (None if not res.patient else
                         {"sale": res.patient.sale_price, "net": res.patient.net,
                          "roi": res.patient.roi, "max_buy": res.patient.max_buy}),
        "reasons": res.reasons, "warnings": res.warnings, "api_calls": calls,
    }


if __name__ == "__main__":
    sys.exit(main())
