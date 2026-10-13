"""Оцінювач оперативної пам'яті: ринок eBay.de (нове і вживане) + економіка перепродажу.

Навіщо окремо від check.py: там ринок = лише НОВИЙ товар за наявності стан-фільтра; для RAM ключовий якраз ВЖИВАНИЙ
ринок (комісія 5%), а «ідентичність» товару — це покоління × форм-фактор × ємність, а не назва лота.

Вхід — текст із оголошення (наприклад «SK Hynix DDR5 32GB 2x16GB PC5-5600B UDIMM») і ціна закупівлі.
Вихід: ринок нового/вживаного (лотів, продавців, мін/швидка/медіана), економіка, вердикт, чек-лист перевірки.

ВАЖЛИВО (виміряно 21.09.2026): дефіцит DRAM через ШІ — ціни eBay DDR5 UDIMM ≈ €14–19/ГБ, DDR4 ≈ €6–9/ГБ.
Ціна, набагато нижча за ринок, — або застаріла ціна приватного продавця, або ШАХРАЙСТВО/інший товар:
тому при ціні < 35% ринку вердикт CHECK (перевірити), а не BUY.
Ціни ОГОЛОШЕНЬ, не проданих; реальні продажі перевіряти в Terapeak.

Запуск:  python ram_check.py "DDR5 32GB 2x16GB 5600 UDIMM" --buy 75
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from dataclasses import dataclass, field

import check
import config
import econ_model as E

NEW_COND = "1000|1500|1750"
USED_COND = "2750|3000|4000|5000"
SANE_GB = (4, 8, 16, 24, 32, 48, 64, 96, 128)
JUNK = re.compile(r"\b(defekt|bastler|ersatzteil|kühler|kuehler|heatsink|leer|nur verpackung|lüfter|gehäuse|mainboard|motherboard|"
                  r"grafikkarte|ssd|festplatte|cpu|prozessor|adapter|gaming pc|komplett)\b", re.I)
SUSPICIOUS_RATIO = 0.35     # цінa < 35% швидкої вживаного ринку → CHECK
MIN_PROFIT = 25.0
MIN_ROI = 0.25
MIN_LOTS = 3
SHIP = "dhl_paeckchen"


@dataclass
class Spec:
    gen: str | None = None          # ddr3/ddr4/ddr5
    form: str = "udimm"             # udimm / sodimm / server
    total_gb: int | None = None
    modules: int = 1
    speed: int | None = None
    ecc: bool = False


def parse_spec(text: str) -> Spec:
    """Текст → специфікація. Пусте/нерозпізнане поле = None (тоді вердикт UNKNOWN)."""
    t = (text or "").lower()
    s = Spec()
    if re.search(r"ddr\s?-?5|pc5", t):
        s.gen = "ddr5"
    elif re.search(r"ddr\s?-?4|pc4", t):
        s.gen = "ddr4"
    elif re.search(r"ddr\s?-?3|pc3", t):
        s.gen = "ddr3"
    if re.search(r"so-?dimm|laptop|notebook", t):
        s.form = "sodimm"
    elif re.search(r"rdimm|lrdimm|reg\.? ?ecc|ecc ?reg|server", t):
        s.form = "server"
    s.ecc = bool(re.search(r"\becc\b", t))
    m = re.search(r"(\d)\s?x\s?(\d{1,3})\s?gb", t)
    if m:
        s.modules, per = int(m.group(1)), int(m.group(2))
        s.total_gb = s.modules * per
    else:
        m = re.search(r"(?<![\d.x])(\d{1,3})\s?gb", t)
        if m:
            s.total_gb = int(m.group(1))
    if s.total_gb not in SANE_GB:
        s.total_gb = None
    m = re.search(r"(?<!\d)(\d{4})\s?(?:mhz|mt/s)|ddr\d[- ](\d{4})|pc\d-?(\d{4,5})", t)
    if m:
        v = next(int(g) for g in m.groups() if g)
        s.speed = v if v <= 9000 else None
    return s


def queries(s: Spec) -> list:
    gen = s.gen.upper()
    form = {"udimm": "UDIMM", "sodimm": "SODIMM", "server": "RDIMM ECC"}[s.form]
    qs = []
    if s.modules > 1:
        per = s.total_gb // s.modules
        qs.append(f"{gen} {s.total_gb}GB {s.modules}x{per}GB {form}")
    qs.append(f"{gen} {s.total_gb}GB {form}")
    return qs


def title_spec(title: str) -> Spec:
    return parse_spec(title)


def comparable(title: str, s: Spec) -> bool:
    """Лот того ж покоління, форм-фактора й загальної ємності. Кіт 2x16 і планка 32 порівнянні за €/ГБ, але
    точніше збігається кількість модулів — це враховує stats() (exact)."""
    if JUNK.search(title):
        return False
    ts = title_spec(title)
    return ts.gen == s.gen and ts.form == s.form and ts.total_gb == s.total_gb and ts.ecc == s.ecc


@dataclass
class MarketSide:
    cond: str
    n: int = 0
    sellers: int = 0
    minimum: float | None = None
    fast: float | None = None
    median: float | None = None
    disp: float | None = None
    exact: bool = False               # ціна за лотами з тією ж кількістю модулів
    category_path: str | None = None
    samples: list = field(default_factory=list)


def _price(it):
    return check._total_price(it)


def side_stats(items: list, s: Spec, cond: str) -> MarketSide:
    rows = []
    cat = None
    for it in items:
        if not comparable(it["title"], s):
            continue
        p = _price(it)
        if p is None:
            continue
        sl = it.get("seller") or {}
        if (sl.get("feedbackScore") or 0) < 10:
            continue
        rows.append((p, sl.get("username") or "?", title_spec(it["title"]).modules, it["title"][:80]))
        cat = cat or "|".join(c["categoryName"] for c in (it.get("categories") or [])[:3])
    exact_rows = [r for r in rows if r[2] == s.modules]
    use, exact = (exact_rows, True) if len({r[1] for r in exact_rows}) >= MIN_LOTS else (rows, False)
    by = {}
    for p, sel, _m, _t in use:
        by[sel] = min(p, by.get(sel, 1e9))
    v = sorted(by.values())
    ms = MarketSide(cond=cond, n=len(use), sellers=len(v), exact=exact, category_path=cat,
                    samples=[(r[0], r[3]) for r in sorted(use)[:3]])
    if len(v) >= 1:
        ms.minimum = v[0]
        ms.fast = round(statistics.median(v[:5]), 2)
        ms.median = round(statistics.median(v), 2)
        if len(v) >= 4:
            q = statistics.quantiles(v, n=4)
            ms.disp = round((q[2] - q[0]) / statistics.median(v), 2)
    return ms


@dataclass
class RamResult:
    spec: Spec
    new: MarketSide
    used: MarketSide
    verdict: str = "UNKNOWN"
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    sale: float | None = None
    sale_basis: str = ""
    deal: object = None
    fee: object = None


def evaluate(spec: Spec, new: MarketSide, used: MarketSide, buy: float | None, *, target_roi: float = 0.30) -> RamResult:
    res = RamResult(spec, new, used)
    if not spec.gen or not spec.total_gb:
        res.reasons.append("не розпізнано покоління (DDR3/4/5) або ємність (ГБ) — уточніть текст")
        return res
    if used.sellers >= MIN_LOTS and used.fast:
        res.sale, res.sale_basis = used.fast, "швидка ціна ВЖИВАНОГО на eBay (виміряно)"
    elif new.sellers >= MIN_LOTS and new.fast:
        res.sale, res.sale_basis = round(0.75 * new.fast, 2), "75% швидкої ціни нового (вживаних лотів мало — ПРИПУЩЕННЯ)"
        res.warnings.append("ринок вживаного тонкий: ціну продажу взято як 75% нового, не виміряно")
    else:
        res.reasons.append(f"ринок нечитабельний: нових лотів {new.sellers}, вживаних {used.sellers} (потрібно ≥{MIN_LOTS} продавців)")
        return res
    # вживане не дорожче за нове: з малих вибірок «швидка» вживаного може перевищити нове — обмежуємо 90% нового
    if new.sellers >= MIN_LOTS and new.fast and res.sale > 0.9 * new.fast:
        res.sale = round(0.9 * new.fast, 2)
        res.sale_basis += "; обмежено 90% швидкої ціни нового"
    cat = used.category_path or new.category_path
    res.fee = E.fee_category(cat, condition_id="3000", price=res.sale)
    if not res.fee.known:
        res.warnings.append(f"комісія категорії: {res.fee.source} ({res.fee.rate:.0%}) — перевір вручну")
    ship = E.shipping_cost(SHIP)
    res.deal = E.deal(res.sale, fee_rate=res.fee.rate, buy_price=buy, channel=E.channels()["ebay"], ship=ship,
                      target_roi=target_roi, return_rate=0.03)
    if used.exact is False and spec.modules > 1:
        res.warnings.append("порівняно за загальною ємністю (кіт і планка), не точно за кількістю модулів")
    if buy is None:
        res.reasons.append("ціну закупівлі не задано — показано лише ринок")
        return res
    ratio = buy / res.sale
    d = res.deal
    if ratio < SUSPICIOUS_RATIO:
        res.verdict = "CHECK"
        res.reasons.append(f"ціна {ratio:.0%} від ринку — або застаріла ціна продавця, або шахрайство/інший товар: "
                           f"спершу ПЕРЕВІРИТИ (див. чек-лист), не переказувати гроші наперед")
    elif d.net is not None and d.net >= MIN_PROFIT and (d.roi or 0) >= MIN_ROI:
        res.verdict = "BUY"
        res.reasons.append(f"чистими €{d.net} ({d.roi:.0%}) при продажі ≈ €{res.sale}")
    else:
        res.verdict = "SKIP"
        res.reasons.append(f"чистими €{d.net} ({(d.roi or 0):.0%}) — менше порогу €{MIN_PROFIT:.0f}/{MIN_ROI:.0%}")
    return res


CHECKLIST = [
    "Це той самий тип модуля: DDR-покоління, UDIMM (настільний) чи SO-DIMM (ноутбук) чи ECC/Reg (сервер) — з наклейки/фото, не зі слів.",
    "Ємність і кількість планок: 2×16 ГБ це кіт з двох модулів. Попросіть фото наклейки (парт-номер, PC5-…).",
    "Оплата: самовивіз і перевірка на місці, або «Sicher bezahlen». Ніколи не переказувати наперед незнайомцю.",
    "Тест: вставити в ПК, перевірити ємність (CPU-Z) і стабільність (MemTest86 ≥1 прохід) до перепродажу.",
    "Перепродаж: «gebraucht, getestet», парт-номер у назві; комерційний продавець: гарантія на вживане 1 рік, право повернення.",
]


def format_result(res: RamResult, buy) -> str:
    icon = {"BUY": "✅ BUY", "SKIP": "❌ SKIP", "CHECK": "⚠ CHECK", "UNKNOWN": "❔ UNKNOWN"}[res.verdict]
    s = res.spec
    lines = [f"{icon} — {s.gen or '?'} {s.form} {s.total_gb or '?'} ГБ" + (f" ({s.modules} мод.)" if s.modules > 1 else "")
             + (f", {s.speed} MT/s" if s.speed else "")]
    for side, name in ((res.new, "НОВЕ"), (res.used, "ВЖИВАНЕ")):
        if side.sellers:
            lines.append(f"  {name:8}: {side.n} лотів від {side.sellers} продавців | мін €{side.minimum:.0f} | "
                         f"швидка €{side.fast:.0f} | медіана €{side.median:.0f}"
                         + (f" | розкид {side.disp}" if side.disp is not None else "")
                         + ("" if side.exact or s.modules == 1 else "  (за загальною ємністю)"))
        else:
            lines.append(f"  {name:8}: лотів немає")
    if res.sale and res.deal:
        d = res.deal
        lines.append(f"  Продаж ≈ €{res.sale}  [{res.sale_basis}]")
        lines.append(f"  Комісія eBay {res.fee.rate:.0%} = €{d.fee}, пересилка €{d.shipping}, повернення 3% враховано")
        if buy is not None:
            lines.append(f"  Чистими: €{d.net} ({(d.roi or 0):.0%}) при закупівлі €{buy}")
        lines.append(f"  Макс. закупівля для ROI 30%: €{d.max_buy}")
    for r in res.reasons:
        lines.append(f"  → {r}")
    for w in res.warnings:
        lines.append(f"  ⚠ {w}")
    if res.verdict in ("CHECK", "BUY"):
        lines.append("  Перед покупкою:")
        lines += [f"   {i}. {c}" for i, c in enumerate(CHECKLIST, 1)]
    return "\n".join(lines)


class RamFetcher(check.Fetcher):
    def search_cond(self, q: str, cond: str, min_price: float = 15):
        flt = (f"buyingOptions:{{FIXED_PRICE}},conditionIds:{{{cond}}},price:[{min_price}..],"
               f"priceCurrency:EUR,itemLocationCountry:{self.country}")
        d = self._get(config.EBAY_BROWSE_SEARCH_URL, {"q": q, "filter": flt, "limit": 200})
        return d.get("itemSummaries") or []


def run(fetcher, text: str, buy: float | None) -> RamResult:
    spec = parse_spec(text)
    if not spec.gen or not spec.total_gb:
        return evaluate(spec, MarketSide("new"), MarketSide("used"), buy)
    new_items, used_items = [], []
    for q in queries(spec):
        new_items += fetcher.search_cond(q, NEW_COND)
        used_items += fetcher.search_cond(q, USED_COND)
    seen = set()
    uniq = lambda items: [it for it in items if not (it["itemId"] in seen or seen.add(it["itemId"]))]
    new_items = uniq(new_items)
    seen.clear()
    used_items = uniq(used_items)
    return evaluate(spec, side_stats(new_items, spec, "new"), side_stats(used_items, spec, "used"), buy)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Оцінювач оперативної пам'яті (eBay.de)")
    ap.add_argument("text", help="опис з оголошення, напр. «SK Hynix DDR5 32GB 2x16GB PC5-5600B UDIMM»")
    ap.add_argument("--buy", type=float, help="ціна закупівлі, € (з доставкою до вас)")
    a = ap.parse_args(argv)
    check._logs_to_stderr()
    f = RamFetcher()
    res = run(f, a.text, a.buy)
    print(format_result(res, a.buy))
    print(f"\n(викликів eBay API: {f.calls})")
    return 0 if res.verdict in ("BUY", "CHECK") else 1


if __name__ == "__main__":
    sys.exit(main())
