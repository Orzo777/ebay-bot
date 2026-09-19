"""Шар рішення: чи є цей лот РЕАЛЬНОЮ знахідкою відносно порівнянних лотів.

Чиста логіка без мережі/файлів — використовується і в main.py (бойовий/тіньовий
режим), і в симуляціях (analyze.py, replay). Кожен відсів повертає короткий код
причини; лот, що пройшов усі, отримує ok=True.

Еталон = ціни ІНШИХ лотів того ж ключа (не сам кандидат), по одному на продавця
(медіана його лотів) — щоб один продавець із 10 однаковими лотами не «створював
ринок». Ціна — із доставкою (total_price).
"""
import statistics
from dataclasses import dataclass, field

import config


@dataclass
class Verdict:
    ok: bool
    reasons: list = field(default_factory=list)      # коди відсіву (порожній, якщо ok)
    median: float | None = None
    n_comps: int = 0
    n_sellers: int = 0
    dispersion: float | None = None
    ratio: float | None = None
    saving: float | None = None
    near: int = 0                                     # ІНШІ продавці у ±cluster_pct від ціни
    near_own: int = 0                                 # дублі того самого продавця поруч
    lowest_other: float | None = None
    q1: float | None = None
    q3: float | None = None


def _quartiles(vals):
    v = sorted(vals)
    if len(v) < 2:
        return v[0], v[0]
    q = statistics.quantiles(v, n=4, method="inclusive")
    return q[0], q[2]


def per_seller_prices(comps):
    """comps: [(price, seller, item_id)] → медіана ціни на продавця."""
    by = {}
    for price, seller, _iid in comps:
        by.setdefault(seller or "?", []).append(price)
    return {s: statistics.median(v) for s, v in by.items()}


def assess(price: float, comps: list, self_seller: str | None = None, *, cfg=config) -> Verdict:
    """price — ціна кандидата (з доставкою); comps — інші лоти ключа
    [(price, seller, item_id)] (кандидата вже виключено). Лоти самого продавця
    кандидата НЕ входять в еталон (незалежність), але враховуються в кластері."""
    v = Verdict(ok=False)
    others = [c for c in comps if not self_seller or (c[1] or "?") != self_seller]
    per = per_seller_prices(others)
    v.n_comps = len(others)
    v.n_sellers = len(per)
    if v.n_comps < cfg.QUALITY_MIN_COMPS or v.n_sellers < cfg.QUALITY_MIN_SELLERS:
        v.reasons.append("thin-ref")
        return v

    ref = list(per.values())                          # по продавцю
    med = statistics.median(ref)
    v.median = med
    q1, q3 = _quartiles(ref)
    v.q1, v.q3 = q1, q3
    v.dispersion = (q3 - q1) / med if med else 9.9
    v.ratio = price / med if med else 9.9
    v.saving = med - price
    v.lowest_other = min(p for p, _s, _i in others)
    tol = cfg.QUALITY_CLUSTER_PCT
    close = [(p, s_) for p, s_, _i in comps if abs(p - price) <= tol * price]
    v.near_own = sum(1 for _p, s_ in close if self_seller and (s_ or "?") == self_seller)
    v.near = len(close) - v.near_own

    strong = (v.n_comps >= cfg.QUALITY_STRONG_COMPS
              and v.n_sellers >= cfg.QUALITY_STRONG_SELLERS)
    if v.dispersion > cfg.QUALITY_MAX_DISPERSION:
        v.reasons.append("blended-ref")
    elif not strong and v.dispersion > cfg.QUALITY_TIGHT_DISPERSION:
        v.reasons.append("weak-ref")               # мало лотів і ринок неоднорідний
    if v.ratio > cfg.ANOMALY_THRESHOLD:
        v.reasons.append("not-cheap")
    elif v.ratio < cfg.QUALITY_MIN_RATIO:
        v.reasons.append("too-deep")
    if v.saving < cfg.QUALITY_MIN_SAVING:
        v.reasons.append("small-saving")
    if v.near >= cfg.QUALITY_CLUSTER_MAX:
        v.reasons.append("price-cluster")           # інші продавці ставлять так само → це ринок
    if v.near_own >= 2:
        v.reasons.append("seller-dupes")            # клони лота від одного продавця = скам-патерн
    v.ok = not v.reasons
    return v


# --------------------------------------------------------------------------- #
# Придатність САМОГО лота (поля відповіді eBay, не назва)
# --------------------------------------------------------------------------- #
def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def listing_flags(item: dict, *, cfg=config) -> list:
    """Причини, чому лот не можна використовувати ні як еталон, ні як кандидата."""
    flags = []
    if item.get("itemGroupType") or item.get("itemGroupHref"):
        flags.append("variation-group")           # ціна = одного з варіантів («від …»)
    so = item.get("shippingOptions") or []
    if not so:
        flags.append("no-shipping")               # лише самовивіз / невідомо
    else:
        cost = so[0].get("shippingCost") or {}
        if so[0].get("shippingCostType") == "CALCULATED" or _f(cost.get("value")) is None:
            flags.append("calc-shipping")         # реальна доставка невідома → ціна занижена
    country = (item.get("itemLocation") or {}).get("country")
    if cfg.MARKET_COUNTRIES and country not in cfg.MARKET_COUNTRIES:
        flags.append(f"foreign:{country}")
    if str(item.get("conditionId") or "") != "1000":
        flags.append(f"cond:{item.get('conditionId')}")
    if (item.get("price") or {}).get("currency") not in (None, "EUR"):
        flags.append("currency")
    return flags
