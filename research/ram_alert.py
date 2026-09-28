"""Миттєва і автоматична оцінка оголошень RAM: парсить назву, визначає тип,
порівнює ціну зі стелями купівлі з реальних продажів (Terapeak, 21.09.2026,
research/ram_terapeak.py) і формує вердикт BUY/CHECK/SKIP.

Ручна перевірка (пишете самі, коли скинули оголошення в чат):
    python research/ram_alert.py "SK Hynix 16GB DDR5 SODIMM RAM 5600MHz PC5-5600B" 50

Надіслати вердикт у Telegram (той самий бот/чат, що й основний бот, потрібні
секрети TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID в оточенні):
    python research/ram_alert.py "..." 50 --tg
"""
import json
import re
import os
import sys

sys.path.insert(0, ".")
from ram_parse import parse_title

# (gen, form, ecc, total, modules) -> реальні продажі (Terapeak). Період у полі `period`:
#   30 днів (23.09.2026) — для типів із великою вибіркою: ціна DDR5 росте ~12%/міс, 90-денна медіана
#   відставала на 5–14%; 90 днів (21–23.09.2026) — де продажів за 30 днів замало для висновку.
# КРИТИЧНО: ключ фіксує САМЕ КІЛЬКІСТЬ ПЛАНОК, а не лише "кіт це чи ні" — інакше DDR4
# 4x16 ГБ (дешевий, не наш тип) зливається з DDR4 2x32 ГБ (наш тип, дорожчий), бо в обох
# total=64 і kit=True. Усі наші "кіт"-типи — саме 2-планкові; усе інше (3x, 4x, 8x...) —
# інший ринок і в таблицю свідомо не входить.
# 26.09.2026 (вечір): 7 типів оновлено з заміру Terapeak (30 днів, ~840 проданих, класифікація парсером бота).
REAL_BASE = {
    ("ddr5", "udimm", False, 32, 2): dict(p25=331, med=382, st=19, period=30, name="DDR5 UDIMM 32 ГБ (2×16) кіт"),
    ("ddr5", "udimm", False, 64, 2): dict(p25=544, med=677, st=9, period=30, name="DDR5 UDIMM 64 ГБ (2×32) кіт"),
    ("ddr5", "udimm", False, 16, 1): dict(p25=147, med=180, st=6, period=90, name="DDR5 UDIMM 16 ГБ (одна планка)"),
    ("ddr5", "sodimm", False, 32, 2): dict(p25=231, med=255, st=8, period=90, name="DDR5 SO-DIMM 32 ГБ (2×16) кіт"),
    # 26.09.2026, Terapeak 30 днів: ≥13 проданих вживаних (Crucial/SK hynix/Samsung/Kingston Impact), p25 ~510, медіана ~556
    ("ddr5", "sodimm", False, 64, 2): dict(p25=421, med=567, st=10, period=30, name="DDR5 SO-DIMM 64 ГБ (2×32) кіт"),
    ("ddr5", "sodimm", False, 32, 1): dict(p25=230, med=255, st=16, period=90, name="DDR5 SO-DIMM 32 ГБ"),
    ("ddr5", "sodimm", False, 16, 1): dict(p25=120, med=149, st=18, period=90, name="DDR5 SO-DIMM 16 ГБ"),
    # 26.09.2026, Terapeak 30 днів (типи, яких бракувало): DDR5 2×24 — 9 продано, 2×8 — 13, DDR4 SO-DIMM 2×16 — 24
    ("ddr5", "udimm", False, 48, 2): dict(p25=409, med=459, st=9, period=30, name="DDR5 UDIMM 48 ГБ (2×24) кіт"),
    ("ddr5", "udimm", False, 16, 2): dict(p25=159, med=185, st=10, period=30, name="DDR5 UDIMM 16 ГБ (2×8) кіт"),
    ("ddr4", "sodimm", False, 32, 2): dict(p25=108, med=128, st=15, period=30, name="DDR4 SO-DIMM 32 ГБ (2×16) кіт"),
    ("ddr4", "udimm", False, 32, 2): dict(p25=134, med=154, st=30, period=30, name="DDR4 UDIMM 32 ГБ (2×16) кіт"),
    ("ddr4", "udimm", False, 64, 2): dict(p25=267, med=306, st=6, period=90, name="DDR4 UDIMM 64 ГБ (2×32) кіт"),
    ("ddr4", "sodimm", False, 32, 1): dict(p25=122, med=149, st=15, period=90, name="DDR4 SO-DIMM 32 ГБ"),
    ("ddr4", "sodimm", False, 64, 2): dict(p25=214, med=260, st=2.5, period=90, name="DDR4 SO-DIMM 64 ГБ (2×32) кіт"),
}


def _with_refresh(base: dict) -> dict:
    """Ціни зі щотижневого оновлення (research/ram_prices.json, пише price_refresh.py) поверх Terapeak.
    RAM_PRICES_OFF=1 — лише базові цифри Terapeak (тести, щоб оновлення не змінювало очікувані вердикти)."""
    table = {k: dict(v) for k, v in base.items()}
    if os.getenv("RAM_PRICES_OFF") == "1":
        return table
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ram_prices.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return table
    for k, v in table.items():
        e = (data.get("types") or {}).get(f"{k[0]}|{k[1]}|{int(k[2])}|{k[3]}|{k[4]}")
        # поправка рахувалась від старого знімка Terapeak (REAL_BASE з того часу оновлено) — вона застаріла:
        # до наступного щотижневого запуску (він сам скине якір) беремо свіжий знімок як є
        if e and "p25_tp" in e and (e["p25_tp"], e.get("med_tp")) != (v["p25"], v["med"]):
            continue
        if e and e.get("p25") and e.get("med"):
            v["p25"], v["med"] = e["p25"], e["med"]
    return table


REAL = _with_refresh(REAL_BASE)
NOISE_BRANDS = {"other", "HP", "Dell", "Lenovo", "Apple", "Supermicro", "Medion", "ASUS", "QNAP/Synology", "2-Power"}
# Найбільша споживча одиночна планка. «DDR4 64GB» без розкладки — це точно набір (2×32 або 4×16),
# а не одна планка: такі не відкидаємо, а оцінюємо як 2-планковий кіт із вимогою уточнити розкладку.
MAX_SINGLE_GB = {"ddr4": 32, "ddr5": 48}


# Продаж на eBay.de (перевірено 26.09.2026): комісія 6,5% + €0.45 за замовлення (Computer/Speicher, як і Konsolen);
# пересилка — DHL Paket до 2 кг €6.19 (Päckchen €4.19 не має відстеження і страховки — для eBay не годиться),
# упаковка ~€1; якщо дорожче €500 — DHL-страховка до €2 500 за €6.99 (базова покриває лише €500).
FEE_SELL, ORDER_FEE_SELL, SHIP_OUT_RAM, PACK_RAM, INSURE_OVER_500 = 0.065, 0.45, 6.19, 1.0, 6.99


def costs(sale_price: float) -> float:
    """Комісія + пересилка покупцю (як безкоштовна — консервативно) + упаковка + страховка + 3% резерв повернень."""
    return (FEE_SELL * sale_price + ORDER_FEE_SELL + SHIP_OUT_RAM + PACK_RAM
            + (INSURE_OVER_500 if sale_price > 500 else 0) + 0.03 * (2 * SHIP_OUT_RAM + ORDER_FEE_SELL))


# Що платить ПОКУПЕЦЬ понад ціну в оголошенні: пересилка продавцю + «Sicher bezahlen» (0,50 € + 4,5%, 2026).
BUY_FEE_FIXED, BUY_FEE_PCT = 0.50, 0.045
SHIP_IN_RAM = 5.50   # DHL Päckchen, який обирає продавець у «Sicher bezahlen»


def buy_cost(item: float, ship_in: float) -> float:
    return item + ship_in + BUY_FEE_FIXED + BUY_FEE_PCT * item


def item_for_cost(total: float, ship_in: float) -> float:
    """Яка ціна в оголошенні дає таку повну вартість покупки."""
    return (total - ship_in - BUY_FEE_FIXED) / (1 + BUY_FEE_PCT)


# Самовивіз у Гамбурзі (підписки «… in Hamburg», з 27.09): платимо готівкою при зустрічі, тож ні пересилки, ні
# збору «Sicher bezahlen»; лише дорога (HVV туди й назад). Шахрайство з передоплатою тут неможливе: гроші — після огляду.
PICKUP_COST = 6.0


def total_for(r: dict, item: float) -> float:
    """Повна вартість купівлі за ціни товару item (з урахуванням самовивозу)."""
    return item + PICKUP_COST if r.get("pickup") else buy_cost(item, r.get("ship_in", 0.0))


def item_for(r: dict, total: float) -> float:
    """Ціна в оголошенні, що дає таку повну вартість (з урахуванням самовивозу)."""
    return total - PICKUP_COST if r.get("pickup") else item_for_cost(total, r.get("ship_in", 0.0))


def apply_pickup(r: dict) -> dict:
    """Перерахунок вердикту для самовивозу. SKIP/UNKNOWN без цін лишаються як є."""
    if "net_q" not in r:
        return r
    cost = r["price"] + PICKUP_COST
    r.update(pickup=True, buy_cost=cost, ship_in=0.0, profit_est=r["net_q"] - cost,
             verdict=tier(cost, r["cap"], r["good"], r["excellent"], r.get("vb", False)))
    return r


# Ціна-заглушка (28.09: «RAM 1x32Gb 2x16Gb DDR 4» за 1 € → «ВІДМІННО»): дешевше 10 € або 15% ринку — не ціна.
PLACEHOLDER_MIN, PLACEHOLDER_SHARE = 10.0, 0.15


def evaluate(title: str, price: float, shipping: float | None = None, vb: bool = False) -> dict:
    ship_in = SHIP_IN_RAM if shipping is None else shipping
    total_price = price
    p, reason = parse_title(title)
    if not p:
        return dict(verdict="UNKNOWN", reason=f"парсер не розпізнав назву: {reason}", title=title, price=total_price)
    if p["ecc"]:
        return dict(verdict="SKIP", reason="ECC/серверна пам'ять — поза нашими прибутковими типами", title=title,
                    price=total_price, wrong_type=True)
    if p.get("mixed"):
        return dict(verdict="SKIP", reason="дві різні планки (різні бренди) — не заводський кіт, продається дешевше",
                    title=title, price=total_price, wrong_type=True)
    key = (p["gen"], p["form"], p["ecc"], p["total"], p["modules"])
    real = REAL.get(key)
    kit_unknown = False
    # Кількість планок не вказана: «32GB DDR5 Corsair» / «64GB Kit» майже завжди 2 планки (28.09: 7 з ~60 оголошень DDR5
    # на KA писали лише «32GB» і бот їх відкидав як «тип, що не купуємо»). «(1x64GB)» — точно одна, не вгадуємо.
    if p["modules"] == 1 and not p.get("explicit_single"):
        two = REAL.get(key[:4] + (2,))
        big = p["total"] > MAX_SINGLE_GB.get(p["gen"], 32) or p["total"] >= 32
        if two and (p.get("kit_word") or (not real and big)):
            real, kit_unknown = two, True
    if not real:
        return dict(verdict="SKIP",
                    reason=f"{p['gen'].upper()} {'ноутбучна' if p['form'] == 'sodimm' else p['form'].upper()} "
                           f"{p['total']} ГБ ({p['modules']} план.) — цей тип не купуємо: на eBay продається дешево "
                           f"або рідко (заміри 21–26.09)",
                    title=title, price=total_price, wrong_type=True)
    if price < max(PLACEHOLDER_MIN, PLACEHOLDER_SHARE * real["p25"]):
        return dict(verdict="SKIP", reason=f"ціна {price:.0f} € — заглушка («1 €», «VB»), а не справжня ціна",
                    title=title, price=total_price, wrong_type=False)
    net_q = real["p25"] - costs(real["p25"])
    cap, good, excellent = net_q / 1.3, net_q / 1.6, net_q / 2.0
    cost = buy_cost(price, ship_in)
    profit_est = net_q - cost
    verdict = tier(cost, cap, good, excellent, vb)
    brand_flag = "невідомий/сумнівний бренд" if (p["brand"] in NOISE_BRANDS or not p["brand"]) else p["brand"]
    # Одна планка (modules==1) — назва каже лише сумарний обсяг, і за текстом НЕМОЖЛИВО
    # перевірити, чи це справді одна фізична планка, чи продавець просто не написав "2x8"
    # для того самого числа (продавці часто пишуть лише підсумкову ємність). Фото рятує не
    # завжди (нове фото легко попросити, старе — ні), тож попереджаємо завжди для цих типів.
    single_module_warning = p["modules"] == 1 and not kit_unknown
    return dict(verdict=verdict, type=real["name"], price=total_price, cap=cap, good=good, excellent=excellent,
                quick_sale=real["p25"], median_sale=real["med"], sell_through=real["st"], profit_est=profit_est,
                net_q=net_q, brand=brand_flag, title=title, single_module_warning=single_module_warning,
                total=p["total"], kit_unknown=kit_unknown, buy_cost=cost, ship_in=ship_in, vb=vb)


# Вердикти, на які йде картка. NEGOTIATE — трохи понад стелю: після торгу стає вигідним.
# «VB» (Verhandlungsbasis) — продавець сам готовий торгуватись: до +15%; фіксована ціна — лише до +8%.
SEND_VERDICTS = ("BUY-EXCELLENT", "BUY-GOOD", "BUY", "NEGOTIATE")
NEGOTIATE_UP, NEGOTIATE_UP_FIXED = 1.15, 1.08


def tier(total: float, cap: float, good: float, excellent: float, vb: bool = False) -> str:
    """total — ПОВНА вартість покупки (товар + пересилка + Sicher bezahlen)."""
    if total <= excellent:
        return "BUY-EXCELLENT"
    if total <= good:
        return "BUY-GOOD"
    if total <= cap:
        return "BUY"
    if total <= cap * (NEGOTIATE_UP if vb else NEGOTIATE_UP_FIXED):
        return "NEGOTIATE"
    return "SKIP"


def offer_price(r: dict) -> int | None:
    """Зустрічна ціна ТОВАРУ для «МОЖНА» і «ТОРГУЙСЯ». Ціль — повна вартість покупки ~10% нижче
    теперішньої, не нижче «добре», не вище стелі; з неї віднімаємо пересилку і Sicher bezahlen і
    округлюємо ВНИЗ до цілих 5 € — продавець бачить круглу суму, а разом виходить не більше цілі."""
    if r.get("verdict") not in ("BUY", "NEGOTIATE"):
        return None
    target = min(r["cap"], max(r["good"], r.get("buy_cost", r["price"]) * 0.9))
    offer = int(item_for(r, target) // 5 * 5)
    if r["verdict"] == "NEGOTIATE":   # «торгуйся» без суми безглуздий: щонайменше −5% від ціни
        offer = min(offer, int(r["price"] * 0.95 // 5 * 5))
    return offer if 0 < offer <= r["price"] * 0.95 else None   # торг на 5 € виглядає дріб'язково — купуй як є


# Ціна нижче третини ринку (28.09: «ADATA DDR5 2×8» за €30 при ринку €150–180 → «ВІДМІННО — БЕРИ»). Так буває,
# коли продавець не знає ціни, але частіше — помилка в назві (одна планка / інший тип), шахрай або «Suche».
TOO_CHEAP = 0.35


def too_cheap(r: dict) -> bool:
    return bool(r.get("quick_sale")) and r["price"] < TOO_CHEAP * r["quick_sale"]


def cheap_headline(r: dict, ebay: bool = False) -> str:
    pay = "оплата лише через eBay" if ebay else "лише «Sicher bezahlen»"
    return (f"⚠️ <b>ПІДОЗРІЛО ДЕШЕВО — {100 * r['price'] / r['quick_sale']:.0f}% ринку.</b> Або продавець не знає ціни, "
            f"або помилка в назві (одна планка, інший тип), або шахрай. Перевір фото етикетки, {pay}."
            if too_cheap(r) else "")


# Що продавець уже написав в описі (сторінку оголошення бот і так відкриває для перевірки на шахраїв) —
# про це не питаємо, а на картці показуємо. Вирази вузькі: «ohne Gewähr» (юридична формула) — не «не тестовано».
_DESC_BROKEN = re.compile(r"funktioniert nicht|nicht funktionsfähig|\bdefekt|kaputt|startet nicht|kein bild|"
                          # симптоми без слова «defekt» (27.09: «geht nach 5–30 Minuten von alleine aus … an Bastler»)
                          r"geht\s+(?:\w+\s+){0,3}(?:von\s+)?(?:alleine|allein|selbst|einfach)\s+(?:\w+\s+)?aus|"
                          r"schaltet\s+sich\s+(?:\w+\s+){0,4}(?:ab|aus)\b|(?:für|an|als)\s+bastler|bastlerware|"
                          r"überhitz|blue\s?light|blaue[sn]?\s+licht|stürzt\s+(?:\w+\s+){0,2}ab|hängt\s+sich\s+(?:\w+\s+)?auf|"
                          r"liest\s+keine|zieht\s+(?:die\s+)?dis[ck]s?\s+nicht|kein\s+(?:ton|signal)", re.I)
_DESC_UNTESTED = re.compile(r"ungetestet|nicht getestet|ungeprüft|nicht geprüft|(?:kann|konnte)\s+(?:\w+\s+)?nicht\s+test", re.I)
_DESC_WORKS = re.compile(r"getestet|funktioniert|funktionsfähig|einwandfrei|fehlerfrei|problemlos|memtest|"
                         r"läuft\s+(?:stabil|super|perfekt|top|ohne)|keine\s+(?:probleme|fehler|mängel)", re.I)
_DESC_NO_RECEIPT = re.compile(r"\b(?:keine?n?|ohne)\s+(?:\w+\s+)?(?:rechnung|kaufbeleg|quittung|kassenbon)|"
                              r"rechnung\s+(?:ist\s+)?(?:nicht|leider nicht)", re.I)
_DESC_RECEIPT = re.compile(r"rechnung|kaufbeleg|quittung|kassenbon|kaufnachweis", re.I)


# Несправне не купуємо зовсім (рішення 27.09: ремонт консолей не робимо). «keine Defekte», «nicht defekt»,
# «Controller defekt» у наборі з робочою консоллю — НЕ дефект самого товару.
_NEG_BEFORE = re.compile(r"(?:kein\w*|ohne|nicht|frei\s+von)\s+(?:\w+\s+){0,2}$", re.I)
_PART_BEFORE = re.compile(r"(?:controller|joy-?con\w*|fernbedienung|kabel|ovp|verpackung|karton|hülle|dock\w*)\W+(?:\w+\W+){0,2}$", re.I)


def broken_reason(desc: str | None) -> str | None:
    """Фрагмент опису, де продавець пише про несправність самого товару, або None."""
    for m in _DESC_BROKEN.finditer(desc or ""):
        before = desc[max(0, m.start() - 40):m.start()]
        if _NEG_BEFORE.search(before):   # «kein Überhitzen», «keine Defekte», «nicht defekt» (28.09)
            continue
        if m.group(0).lower().startswith("defekt") and _PART_BEFORE.search(before):
            continue
        return desc[max(0, m.start() - 30):m.end() + 20].strip()
    return None


def desc_facts(desc: str | None, r: dict | None = None) -> dict:
    """Опис → {works: True/False/None, receipt: True/False/None, kit_ok: bool}. None — опис не згадує."""
    d = desc or ""
    works = (False if broken_reason(d) or _DESC_UNTESTED.search(d)
             else True if _DESC_WORKS.search(d) else None)
    receipt = False if _DESC_NO_RECEIPT.search(d) else True if _DESC_RECEIPT.search(d) else None
    kit_ok = False
    if r and r.get("total"):
        t = r["total"]
        if r.get("kit_unknown"):
            kit_ok = bool(re.search(rf"\b2\s*[x×]\s*{t // 2}\s*gb|\b(?:zwei|2)\s+(?:riegel|module|stück)", d, re.I))
        elif r.get("single_module_warning"):
            kit_ok = bool(re.search(rf"\b1\s*[x×]\s*{t}\s*gb|\bein(?:en|zelner)?\s+(?:riegel|modul)", d, re.I))
    return dict(works=works, receipt=receipt, kit_ok=kit_ok)


def _questions(r: dict) -> list[str]:
    f = desc_facts(r.get("desc"), r)
    q = []
    if r.get("single_module_warning") and not f["kit_ok"]:
        q.append(f'Ist es genau EIN Riegel mit {r["total"]} GB?')
    elif r.get("kit_unknown") and not f["kit_ok"]:
        q.append(f'Sind es 2x{r["total"] // 2} GB oder mehr Riegel?')
    if too_cheap(r) and r.get("item_acc", "den RAM") == "den RAM":
        q.append("Können Sie ein Foto vom Aufkleber schicken?")
    if f["works"] is None:
        q.append(r.get("check_q", "Lief er fehlerfrei?"))
    if f["receipt"] is None:
        q.append("Gibt es eine Rechnung?")
    return q


def buyer_message(r: dict, offer: int | None = None) -> str:
    """Текст продавцю (німецькою, ≤256 символів — ліміт кнопки «копіювати» в Telegram).
    Як «хук»: спершу рішення (беру, зарезервуй), потім умови, в кінці лише питання, на які опис не відповів.
    «Ще в наявності?» не питаємо: зняте оголошення продавець і так закриває або ставить «Reserviert»."""
    what = r.get("item_acc", "den RAM")
    head = (f"Hallo! Ich nehme {what} für {offer} € und kaufe sofort – bitte für mich reservieren."
            if offer is not None else f"Hallo! Ich nehme {what} und kaufe sofort – bitte für mich reservieren.")
    if r.get("pickup"):
        head = (f"Hallo! Ich nehme {what} für {offer} € – bitte für mich reservieren."
                if offer is not None else f"Hallo! Ich nehme {what} – bitte für mich reservieren.")
        terms = "Ich hole heute oder morgen in Hamburg ab und zahle bar. Wann passt es Ihnen?"
    else:
        terms = 'Ich zahle per „Sicher bezahlen", Versand und Gebühr übernehme ich.'
    parts = [head, terms, *_questions(r), "Danke!"]
    while len(" ".join(parts)) > 256 and len(parts) > 3:   # задовге — спершу жертвуємо останнім питанням (чек)
        parts.pop(-2)
    return " ".join(parts)


def offer_template(r: dict) -> str | None:
    o = offer_price(r)
    return None if o is None else buyer_message(r, o)


def seller_template(r: dict) -> str:
    return buyer_message(r)


def desc_line(r: dict) -> str | None:
    """Рядок на картку: що продавець сам написав в описі."""
    if r.get("desc") is None:
        return None
    f = desc_facts(r["desc"], r)
    bits = []
    if f["works"] is True:
        bits.append("✓ пише, що справне / протестоване")
    elif f["works"] is False:
        bits.append("⚠️ пише, що НЕ тестоване або з дефектом")
    if f["receipt"] is True:
        bits.append("🧾 є чек")
    elif f["receipt"] is False:
        bits.append("без чека")
    if f["kit_ok"]:
        bits.append("✓ кількість планок вказана")
    return "📝 В описі: " + (" · ".join(bits) if bits else "нічого про справність і чек — питання в тексті")


def _speed_label(st: float) -> str:
    if st >= 30:
        return "продається швидко"
    if st >= 15:
        return "продається помірно"
    return "продається повільно"


def _item_cap(r: dict, key: str) -> int:
    """Стеля як ціна В ОГОЛОШЕННІ (пересилку й Sicher bezahlen уже враховано)."""
    return int(item_for(r, r[key]))


def format_html(r: dict) -> str:
    """Картка для телефона: короткі рядки, найважливіше зверху, текст продавцю — окремим блоком,
    який копіюється дотиком (тег <code>). Лише для вердиктів BUY*."""
    from html import escape

    tag = {"BUY-EXCELLENT": "🟢🟢 <b>ВІДМІННО — БЕРИ</b>", "BUY-GOOD": "🟢 <b>ДОБРЕ — БЕРИ</b>",
           "BUY": "🟡 <b>МОЖНА, але маржа тонка</b>",
           "NEGOTIATE": "💬 <b>ТОРГУЙСЯ — трохи дорожче стелі</b>"}[r["verdict"]]
    if too_cheap(r):
        tag = cheap_headline(r)
    offer = offer_price(r)
    profit = (f"💶 Заробіток ≈ <b>{r['profit_est']:.0f} €</b>" if r["verdict"] != "NEGOTIATE"
              else f"💶 За поточною ціною ≈ {r['profit_est']:.0f} € (маржа нижча за 30%)")
    lines = [
        tag,
        *(["⚡ <b>Є «Direkt kaufen»</b> — тисни «Kaufen» в оголошенні: лот твій одразу, без чекання відповіді "
            "(оплата через «Sicher bezahlen», з пересилкою)"] if r.get("buy_now") and r["verdict"] != "NEGOTIATE" else []),
        *r.get("risk_lines", []),   # ka_listing_check: ризик шахрайства (вже екрановано)
        f"<b>{escape(r['type'])}</b>",
        f"{escape(r['brand'])} · <b>{r['price']:.0f} €</b>" + (" VB" if r.get("vb") else "")
        + ((f" 🚶 самовивіз, готівкою (з дорогою ≈ {r['buy_cost']:.0f} €)" if r.get("pickup")
            else f" (з пересилкою і Sicher bezahlen ≈ {r['buy_cost']:.0f} €)") if r.get("buy_cost") else ""),
        "",
        profit,
        *([f"🤝 Запропонуй <b>{offer} €</b> (разом ≈ {total_for(r, offer):.0f} €) "
           f"→ заробіток ≈ <b>{r['net_q'] - total_for(r, offer):.0f} €</b>"]
          if offer is not None and r.get("net_q") else []),
        f"🛒 Ціна в оголошенні до {_item_cap(r, 'cap')} € (добре ≤ {_item_cap(r, 'good')}, "
        f"супер ≤ {_item_cap(r, 'excellent')})",
        f"🏷 Продати: {r['quick_sale']}–{r['median_sale']} €",
        f"⏱ {_speed_label(r['sell_through'])}",
    ]
    if desc_line(r):
        lines += ["", escape(desc_line(r))]
    if desc_facts(r.get("desc"), r)["kit_ok"]:
        pass   # продавець уже написав у описі, скільки планок
    elif r.get("single_module_warning"):
        lines += ["", f"⚠️ Перевір, що це <b>одна</b> планка на {r['total']} ГБ, а не кілька менших."]
    elif r.get("kit_unknown"):
        half = r["total"] // 2
        lines += ["", f"⚠️ Скільки планок — не вказано. Бери лише якщо це <b>2×{half} ГБ</b>; "
                      f"4×{r['total'] // 4} ГБ коштує набагато менше."]
    for note in r.get("notes", []):
        lines += ["", f"⚠️ {escape(note)}"]
    lines += ["", f"<i>{escape(r['title'][:90])}</i>", "",
              "✉️ Текст продавцю (натисни — скопіюється):", f"<code>{escape(seller_template(r))}</code>"]
    if offer is not None:
        lines += ["", f"✉️ З пропозицією {offer} €:", f"<code>{escape(offer_template(r))}</code>"]
    return "\n".join(lines)


def format_message(r: dict) -> str:
    if r["verdict"] in ("UNKNOWN", "SKIP"):
        return f"⏭ {r['title'][:70]}\n{r.get('reason', '')} (ціна {r['price']:.0f}€)"
    tag = {"BUY-EXCELLENT": "🟢🟢 ВІДМІННО, БЕРИ", "BUY-GOOD": "🟢 ДОБРЕ, БЕРИ", "BUY": "🟡 CHECK (тонка маржа)",
           "NEGOTIATE": "💬 ТОРГУЙСЯ (до +15% над стелею)"}[r["verdict"]]
    warn = ("\n⚠️ У назві вказано лише сумарний обсяг — уточніть у продавця, що це РІВНО ОДНА планка, "
            "а не 2+ менших модулі разом (наприклад 2×8 замість однієї 16 ГБ).") if r.get("single_module_warning") else ""
    return (f"{tag}: {r['type']}\n"
            f"«{r['title'][:80]}»\n"
            f"Ціна: {r['price']:.0f}€ | стеля ROI30%: {r['cap']:.0f}€ | добра: {r['good']:.0f}€ | відмінна: {r['excellent']:.0f}€\n"
            f"Реальний продаж (90 дн.): медіана {r['median_sale']}€, швидкий {r['quick_sale']}€, sell-through {r['sell_through']}%\n"
            f"Бренд: {r['brand']}\n"
            f"Орієнтовно чистими (продаж за {r['quick_sale']}€): ≈{r['profit_est']:.0f}€"
            f"{warn}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("title")
    ap.add_argument("price", type=float)
    ap.add_argument("--shipping", type=float, default=0.0)
    ap.add_argument("--tg", action="store_true", help="надіслати вердикт у Telegram (ті самі секрети, що й основний бот)")
    args = ap.parse_args()
    res = evaluate(args.title, args.price, args.shipping)
    msg = format_message(res)
    print(msg)
    if args.tg:
        import config
        import main as botmain

        if config.TELEGRAM_CREDS_OK:
            botmain.send_telegram("[RAM] " + msg)
        else:
            print("[DRY RUN] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не задані в оточенні")
