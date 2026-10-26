"""«продати N» (01.10): готове оголошення eBay.de для товару з обліку → бот «Облік і продаж».

Ланцюжок: бот «Облік і продаж» → Apps Script (ledger.gs: рядок N з таблиці) → GitHub sell.yml → цей файл → Telegram.
Що рахує:
  • тип товару тим самим розбором, що й при купівлі (RAM / PS5 / Xbox / Switch);
  • ринок: продажі за 30 днів (Terapeak, як для стелі купівлі) + живі конкуренти на eBay зараз (Browse API, 1 запит);
  • ціна «Sofort-Kaufen» з безкоштовною доставкою, пороги Preisvorschlag (автоприйняти / автовідхилити), прибуток;
  • німецька назва (≤80), опис, характеристики (Artikelmerkmale), що зробити перед відправкою.
Репозиторій публічний, тож ціна купівлі приходить ЗАШИФРОВАНОЮ (ключ — токен бота «Облік і продаж», він є і в
Apps Script, і в секретах GitHub); у лог не пишемо ні ціну купівлі, ні прибуток.

    python research/sell.py --blob … --nonce …          (з GitHub)
    python research/sell.py --title "OWC 2x16GB DDR4 SO-DIMM für iMac" --cost 45 --dry-run   (локально)
"""
import argparse
import base64
import hashlib
import hmac
import html
import json
import math
import os
import re
import sys
from urllib.parse import quote_plus

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import console_alert
import ram_alert
from ram_parse import parse_title

RAM_CAT, CONSOLE_CAT = "170083", "139971"
MIN_PROFIT_ABS, MIN_PROFIT_PCT = 5.0, 0.10      # нижче цього прибутку автоматично НЕ погоджуємось
# 10.10: фірмовий DDR4 2×16 пішов за хвилини через автоприйняття 147 € (93%) — поріг ближче до ціни
ACCEPT_SHARE, DECLINE_SHARE = 0.96, 0.90        # від ціни оголошення / від «швидкої» ціни
SHIP_CHARGE_RAM = 6.19   # 09.10: доставку платить покупець окремим рядком (DHL Paket); 20.10: 6,19 — як у конкурентів (= наша вартість DHL)
LOW_SHARE = 0.92                                # нижче 92% «швидкої» ціни не ставимо
COMP_PAGES = 3                                  # конкуренти: до 3 сторінок по 200 (3 виклики Browse API)


# ----------------------------------------------------------------------------- шифрування (пара до ledger.gs)
def _keystream(key: str, nonce: str, n: int) -> bytes:
    out, i = b"", 0
    while len(out) < n:
        out += hmac.new(key.encode(), f"{nonce}:{i}".encode(), hashlib.sha256).digest()
        i += 1
    return out[:n]


def seal(data: dict, key: str, nonce: str) -> tuple[str, str]:
    raw = json.dumps(data, ensure_ascii=False).encode()
    blob = base64.b64encode(bytes(a ^ b for a, b in zip(raw, _keystream(key, nonce, len(raw))))).decode()
    mac = hmac.new(key.encode(), f"mac:{nonce}:{blob}".encode(), hashlib.sha256).hexdigest()[:32]
    return blob, mac


def unseal(blob: str, mac: str, key: str, nonce: str) -> dict:
    want = hmac.new(key.encode(), f"mac:{nonce}:{blob}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(want, mac or ""):
        raise ValueError("підпис не збігається (інший токен бота або пошкоджені дані)")
    raw = base64.b64decode(blob)
    return json.loads(bytes(a ^ b for a, b in zip(raw, _keystream(key, nonce, len(raw)))).decode())


# ----------------------------------------------------------------------------- що це за товар
def identify(title: str, price: float = 9999) -> dict | None:
    """→ оцінка в форматі ram_alert.evaluate (type, quick_sale, median_sale, net_q…) + kind/spec, або None.
    Свій товар — з ціною 9999 (тип не залежить від ціни купівлі); конкуренти — з їхньою ціною (аксесуари відсіються)."""
    r = console_alert.evaluate_console(title, price) or ram_alert.evaluate(title, price)
    if not r or "quick_sale" not in r:
        return None
    if r.get("brand") in ("Sony", "Microsoft", "Nintendo"):
        r["kind"] = "console"
        r["ship_out"] = 7.69 if "Switch" in r["type"] and "Lite" not in r["type"] else \
            6.19 if "Lite" in r["type"] else console_alert.SHIP
    else:
        p, _ = parse_title(title)
        r["kind"], r["spec"] = "ram", p
        if r.get("kit_unknown"):   # «32GB DDR4» без розкладки, а продаємо як кіт — у назву пишемо 2×
            r["spec"] = dict(p, modules=2)
    return r


def net_of(r: dict, price: float) -> float:
    """Скільки лишиться з ціни продажу (комісія, пересилка покупцю безкоштовно, упаковка, страховка, резерв)."""
    if r["kind"] == "console":
        return price - console_alert.costs(price, r["ship_out"])
    return price - ram_alert.costs(price)


def _taxed(profit: float) -> str:
    """13.10: з комерційним акаунтом — ще й прибуток після податку (~30%)."""
    return f" (після податку ≈ {ram_alert.after_tax(profit):.0f} €)" if ram_alert.business() and profit > 0 else ""


def price_for_net(r: dict, net: float) -> float:
    """Найменша ціна продажу, що дає `net` чистими (обернена до net_of; кроком 0,5 €)."""
    p = max(net, 1.0)
    while net_of(r, p) < net:
        p += 0.5
    return p


def nice(p: float) -> int:
    """124, 129, 139… — «психологічна» ціна, не вища за p."""
    p = int(math.floor(p))
    if p < 30:
        return p
    return p if p % 5 == 4 else p - (p % 5) - 1


# ----------------------------------------------------------------------------- конкуренти
def competitor_query(r: dict) -> tuple[str, str]:
    if r["kind"] == "ram":
        s = r["spec"]
        form = "so-dimm" if s["form"] == "sodimm" else "ecc udimm" if s.get("ecc_udimm") else ""   # ECC — свої конкуренти
        return f"{s['gen']} {s['total']}gb {form}".strip(), RAM_CAT
    t = r["type"]
    q = ("xbox series x" if "Xbox" in t else "ps5 digital" if "Digital" in t else "ps5" if "PS5" in t else
         "switch 2" if "Switch 2" in t else "switch oled" if "OLED" in t else "switch lite" if "Lite" in t else "nintendo switch")
    return q, CONSOLE_CAT


def competitors(r: dict, client=None) -> list[float]:
    """Повні ціни (товар + пересилка) живих «Sofort-Kaufen» того самого типу в Німеччині, від найдешевшої."""
    from main import EbayClient, _request_with_backoff
    client = client or EbayClient()
    q, cat = competitor_query(r)
    # 19.10: було лише 100 найдешевших результатів «ddr4 32gb» — серед них переважно SO-DIMM і серверні, і з 36 живих
    # конкурентів DDR4 UDIMM 2×16 фірмових бот бачив 3. Тепер — усі сторінки (до 3 × 200).
    items = []
    for off in range(0, COMP_PAGES * 200, 200):
        d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=client._headers(), params={
            "q": q, "category_ids": cat, "limit": 200, "offset": off, "sort": "price",
            "filter": f"buyingOptions:{{FIXED_PRICE}},itemLocationCountry:DE,conditionIds:{{3000}},"
                      # нижче 80% «швидкої» ціни — здебільшого шахраї, дефекти й інші товари, а не конкуренти
                      f"price:[{int(r['quick_sale'] * 0.8)}..{int(r['median_sale'] * 1.6)}],priceCurrency:EUR"})
        page = d.get("itemSummaries") or []
        items += page
        if len(page) < 200 or off + 200 >= int(d.get("total") or 0):
            break
    out = []
    for it in items:
        try:
            price = float(it["price"]["value"])
            ship = float(((it.get("shippingOptions") or [{}])[0].get("shippingCost") or {}).get("value") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if not (it.get("seller") or {}).get("feedbackScore"):   # новий акаунт без відгуків — часто шахрай-приманка
            continue
        c = identify(it.get("title", ""), price)
        if c and c["type"] == r["type"]:   # той самий тип; дефект/гра/аксесуар/інша ємність сюди не потрапляють
            out.append(round(price + ship, 2))
    return sorted(out)


# ----------------------------------------------------------------------------- ціна
def plan_price(r: dict, cost: float | None, comp: list[float], undercut: bool = False) -> dict:
    """Ціна «Sofort-Kaufen» (з безкоштовною доставкою) і пороги Preisvorschlag.
    Ціль — медіана реальних продажів (08.10, рішення користувача: раніше підлаштовувались під трьох найдешевших
    конкурентів, і ціна сповзала до «швидкої»); не нижче мінімуму, з яким продаж ще дає ≥5 € / 10% прибутку.
    Дешевші конкуренти — лише попередження. undercut=True (тижневий звіт: товар не продається тиждень+) — як раніше,
    серед трьох найдешевших, але не дешевше 92% «швидкої» ціни. Автоприйняти — від 93% ціни, автовідхилити — нижче 87% ціни чи 90%
    «швидкої» (що менше), але ніколи нижче мінімуму."""
    quick, med = r["quick_sale"], r["median_sale"]
    warn = []
    target = med
    if undercut and comp:   # залежалося — стати серед трьох найдешевших
        target = max(min(med, comp[min(2, len(comp) - 1)] - 1), quick * LOW_SHARE)
    floor = price_for_net(r, cost + max(MIN_PROFIT_ABS, MIN_PROFIT_PCT * cost)) if cost else 0.0
    list_price = max(nice(target), math.ceil(floor))
    if comp and not undercut and list_price >= comp[min(2, len(comp) - 1)]:
        ref = comp[min(2, len(comp) - 1)]
        warn.append(f"три найдешевші схожі вже по {', '.join(f'{c:.0f}' for c in comp[:3])} € — за медіаною продаватиметься "
                    f"повільніше; не продається тиждень — знизь до ~{nice(max(ref - 1, quick * LOW_SHARE, floor))} €")
    accept = max(math.ceil(floor), round(list_price * ACCEPT_SHARE))
    decline = max(math.ceil(floor), round(min(quick * DECLINE_SHARE, list_price * 0.87)))
    accept = min(accept, list_price)
    decline = min(decline, accept)
    if cost and math.ceil(floor) > nice(target):
        warn.append("щоб не продати в мінус, ціна вища за ринкову — продаватиметься довше")
    return dict(list=list_price, accept=accept, decline=decline, floor=floor, warn=warn,
                profit_list=(net_of(r, list_price) - cost) if cost else None,
                profit_accept=(net_of(r, accept) - cost) if cost else None,
                net_list=net_of(r, list_price))


# ----------------------------------------------------------------------------- тексти німецькою
# 26.10: «Corsair Vengeance OVP …» → модель «Vengeance OVP» (OVP — оригінальна упаковка, а не серія; №5 на eBay)
_SPEC_WORD = re.compile(r"^(?:\d+(?:[.,]\d+)?(?:gb|g|tb|mhz|mt/?s|cl\d*|x\d+)?|x|gb|ddr\d?l?|pc\d-?\d*\w?|ram|kit|set|"
                        r"so-?dimm|sodimm|dimm|udimm|ddr4-\d+|ddr5-\d+|arbeitsspeicher|speicher|memory|laptop|notebook|"
                        r"desktop|pc|module?|riegel|stück|stk|neu|neuwertig|gebraucht|top|zustand|getestet|original|ovp|verpackung|"
                        r"für|fur|for|mit|und|inkl|der|die|das|ein|eine|von|im|in|cl|v|\(|\)|-|–|/|\+|&|,|\|)$", re.I)


def _brand_series(title: str, brand: str) -> str:
    """«Corsair Vengeance LPX», «Kingston Fury Beast» — бренд і лінійка з назви продавця (до 3 слів)."""
    cut = re.split(r"\b(?:für|fur|for|passend|kompatibel)\b", title, flags=re.I)[0]
    words = [w for w in re.findall(r"[A-Za-z][\w.-]*", cut)
             if not _SPEC_WORD.match(w) and not re.match(r"(?:x\d|mhz|mts|cl\d|pc\d|ddr)", w, re.I)
             and not (re.search(r"\d", w) and len(w) > 3)]
    keep = []
    for w in words:
        if len(keep) == 3:
            break
        if brand and brand.lower() in w.lower() and keep:
            continue
        keep.append(w)
    head = " ".join(keep)
    if brand and brand not in ("other",) and brand.lower() not in head.lower():
        head = (brand + " " + head).strip()
    return head


def _part_number(title: str) -> str | None:
    cands = [m for m in re.findall(r"\b(?=[A-Z0-9/-]*\d)(?=[A-Z0-9/-]*[A-Z])[A-Z0-9][A-Z0-9/-]{7,}\b", title)
             if not re.match(r"^(?:DDR|PC)\d|^\d+X\d+(?:GB)?$", m) and not re.search(r"(?:GB|MHZ|MT/S)$", m)]
    return max(cands, key=len) if cands else None


def _for_device(title: str) -> str | None:
    m = re.search(r"\b(?:für|for|passend für)\s+((?:i?mac|macbook|thinkpad|dell|hp|lenovo|asus|acer|intel nuc|nuc)\b[\w .-]{0,18})",
                  title, re.I)
    return m.group(1).strip(" -") if m else None


def ram_texts(title: str, r: dict) -> dict:
    s = r["spec"]
    gen, mods, total = s["gen"].upper(), s["modules"], s["total"]
    per = total // mods
    lap = s["form"] == "sodimm"
    speed = s.get("speed")
    pc = f"PC{gen[-1]}-{speed * 8 // 100 * 100}" if speed else None
    cap = f"{total}GB ({mods}x{per}GB)" if mods > 1 else f"{total}GB"
    brand = s.get("brand") if s.get("brand") not in (None, "other") else ""
    head = _brand_series(title, brand)
    dev = _for_device(title)
    ecc = bool(s.get("ecc_udimm"))   # 04.10: ECC без буфера — пишемо в назві, шукають за «ECC UDIMM» (сервери, NAS)
    form = "SO-DIMM Laptop RAM" if lap else "ECC Unbuffered DIMM" if ecc else "DIMM Desktop RAM"
    pn = _part_number(title)
    genspeed = gen + (f"-{speed}" if speed else "")
    t = None
    if pn:   # 09.10 (прохання користувача): номер моделі в назві — за ним шукають покупці, яким потрібен саме цей набір
        short = "SO-DIMM" if lap else "ECC UDIMM" if ecc else "DIMM"
        for parts in ([head, cap, genspeed, pn, form, "getestet"], [head, cap, genspeed, pn, short, "getestet"],
                      [head, cap, genspeed, pn, short], [head, cap, genspeed, pn]):
            c = re.sub(r"\s+", " ", " ".join(x for x in parts if x)).strip()
            if len(c) <= 80:
                t = c
                break
    if not t:
        t = re.sub(r"\s+", " ", " ".join(x for x in [head, cap, genspeed, form] if x)).strip()
        if dev and len(t) + len(dev) + 5 <= 80:
            t += f" für {dev}"
        while len(t) > 80 and " " in t:
            t = t.rsplit(" ", 1)[0]
        for extra in (" – getestet", " getestet"):
            if len(t + extra) <= 80:
                t += extra
                break
    # 16.10 (користувач): по-людськи, без канцеляриту; без «in der Regel am nächsten Werktag» і «Rechte aus dem
    # eBay-Käuferschutz bleiben unberührt» — і так зрозуміло. Без «мій / лежав у мене»: це перепродаж, пишемо лише правду.
    many = mods > 1
    lines = [f"Biete hier {'ein Kit' if many else 'ein Modul'} {head + ' ' if head else ''}{cap} {gen} {form} an.",
             f"{'Die Riegel sind' if many else 'Das Modul ist'} gebraucht, {'laufen' if many else 'läuft'} einwandfrei und "
             f"{'wurden' if many else 'wurde'} vor dem Verkauf mit MemTest86 getestet – 0 Fehler (Screenshot bei den Fotos).", "",
             "Die Daten:",
             f"• Kapazität: {total} GB" + (f" ({mods} × {per} GB)" if mods > 1 else ""),
             f"• Typ: {gen} {'SO-DIMM (Laptop' + (', ' + dev if dev else '') + ')' if lap else 'ECC UDIMM (unbuffered, ungepuffert)' if ecc else 'DIMM (Desktop-PC)'}",
             *(["• ECC ohne Buffer: für Server, NAS und Workstations; läuft in den meisten Desktop-PCs (Intel ohne ECC-Funktion)"]
               if ecc else []),
             *([f"• Geschwindigkeit: {speed} MHz ({pc})"] if speed else []),
             *([f"• Teilenummer: {pn}"] if pn else []), "",
             f"Mit dabei: {mods} {'Module' if many else 'Modul'} wie auf den Fotos. Verschickt wird gut und antistatisch verpackt "
             "als versichertes Paket mit Sendungsnummer.", "",   # 26.10: без «DHL» — у лоті може стояти Hermes; SHIP_PAID_DE
             "Privatverkauf, daher keine Garantie und keine Rücknahme. Bei Fragen einfach melden!"]
    pins = {("DDR4", True): 260, ("DDR4", False): 288, ("DDR5", True): 262, ("DDR5", False): 288}.get((gen, lap))
    # поля — як у формі eBay «Angebot fertigstellen» (09.10): обов'язкові, потім інші; «—» — не заповнювати
    required = [("Marke", head.split()[0] if head else "Markenlos"), ("Produktart", f"{gen} SDRAM")]
    more = [("Formfaktor", "SO-DIMM" if lap else "DIMM"), ("Anzahl der Module", str(mods)), ("Kapazität pro Modul", f"{per} GB"),
            ("Gesamtkapazität", f"{total} GB"), ("Busgeschwindigkeit", f"{speed} MHz" if speed else "—"),
            ("Modell", head or "—"), ("Herstellernummer", pn or "—"), ("Anzahl der Pins", str(pins) if pins else "—"),
            ("Speicher-Eigenschaften", "ECC-Speicher" if ecc else "—")]
    cond = "Gebraucht, voll funktionsfähig. Mit MemTest86 getestet: 0 Fehler (Foto anbei)."
    before = ["фото наклейки крупно, щоб читався номер моделі (покупці перевіряють), обидві планки, екран MemTest86 "
              "(«фото N» — бот пришле збережені)",
              "опис каже «getestet» — переконайся, що планки пройшли тест (MemTest86, 0 помилок)"]
    warn_ecc = None if ecc else ("«Alle übernehmen» у підказках eBay не тисни, якщо там «ECC-Speicher» — "
                                 "ця пам'ять не ECC; познач галочками лише правильні пункти")
    return dict(title=t[:80], desc="\n".join(lines), required=required, more=more, cond=cond, warn=warn_ecc,
                category="Arbeitsspeicher (RAM)", before=before, weight="0 kg 300 g", dims="20 × 15 × 5 cm",
                ship=SHIP_CHARGE_RAM, pn=pn)


def console_texts(title: str, r: dict) -> dict:
    t, low = r["type"], title.lower()
    ctrl = bool(re.search(r"controller|joy-?con|dualsense", low))
    games = re.search(r"(\d+)\s*(?:spiele|games)", low)
    if "PS5" in t:
        slim = " Slim" if "slim" in low else ""
        ed = "Digital Edition" if "Digital" in t else "Disc Edition"
        stor = "1TB" if re.search(r"\b1\s?tb\b|slim", low) else "825GB" if "825" in low else ""
        name = f"Sony PlayStation 5{slim} {ed} {stor}".strip()
        check = "Laufwerk liest Discs, " if "Digital" not in t else ""
        acct = "vom PSN-Konto abgemeldet"
    elif "Xbox" in t:
        name, check, acct = "Microsoft Xbox Series X 1TB", "Laufwerk liest Discs, ", "vom Microsoft-Konto abgemeldet"
    else:
        name = ("Nintendo Switch 2" if "Switch 2" in t else "Nintendo Switch OLED" if "OLED" in t else
                "Nintendo Switch Lite" if "Lite" in t else "Nintendo Switch")
        check, acct = "Joy-Con ohne Drift, ", "vom Nintendo-Konto abgemeldet"
    inc = "Konsole" + (" + Controller" if ctrl and "Switch" not in name else "")
    head = f"{name} {inc}"
    tt = head + (f" + {games.group(1)} Spiele" if games else "")
    for extra in (" – gebraucht, getestet", " – getestet"):
        if len(tt + extra) <= 80:
            tt += extra
            break
    lines = [f"Biete hier eine {head} an.",   # 16.10: по-людськи, без канцеляриту
             f"Die Konsole ist gebraucht, voll funktionsfähig und getestet ({check}keine Fehler), auf Werkseinstellungen "
             f"zurückgesetzt und {acct}.", "",
             "Mit dabei: alles wie auf den Fotos" + (" (inkl. Controller)" if ctrl else "") + ", Strom- und HDMI-Kabel. "
             "Verschickt wird sicher verpackt als versichertes Paket mit Sendungsnummer.", "",
             "Privatverkauf, daher keine Garantie und keine Rücknahme. Bei Fragen einfach melden!"]
    required = [("Marke", name.split()[0]), ("Modell", " ".join(name.split()[1:]))]
    # 09.10: назви й значення — з Taxonomy API eBay.de (категорія 139971), щоб і автопублікація їх прийняла
    more = [("Plattform", "Sony PlayStation 5" if "PS5" in t else "Microsoft Xbox Series X|S" if "Xbox" in t else
             "Nintendo Switch 2" if "Switch 2" in t else "Nintendo Switch"), ("Regionalcode", "PAL"),
            ("Produktart", "Handheld-System" if "Lite" in name else "Heimkonsole"), ("Farbe", "—")]
    cond = "Gebraucht, voll funktionsfähig, getestet. Auf Werkseinstellungen zurückgesetzt."
    before = ["скинь консоль до заводських налаштувань і вийди з акаунту (" + acct.replace("vom ", "").replace(" abgemeldet", "") + ")",
              "сфотографуй консоль увімкненою (екран налаштувань) — покупці довіряють більше",
              "перевір, що в коробці все, що на фото й в описі (кабелі, контролер)"]
    return dict(title=tt[:80], desc="\n".join(lines), required=required, more=more, cond=cond, warn=None,
                category="Videospielkonsolen", before=before, weight="5 kg", dims="50 × 40 × 20 cm",
                ship=r.get("ship_out") or console_alert.SHIP, pn=None)


# ----------------------------------------------------------------------------- картка
def money_de(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


# 26.10 (користувач: «при виставленні — кнопка: безкоштовна чи платна доставка; ціна перераховується, навіть якщо я задав
# свою; описи — під варіант»). Ціна «разом» (товар + доставка) лишається тою самою: безкоштовна — доставка в ціні товару.
SHIP_PAID_DE = {"ram": "Verschickt wird gut und antistatisch verpackt als versichertes Paket mit Sendungsnummer.",
                "console": "Verschickt wird sicher verpackt als versichertes Paket mit Sendungsnummer."}
SHIP_FREE_DE = {"ram": "Kostenloser Versand: gut und antistatisch verpackt als versichertes Paket mit Sendungsnummer.",
                "console": "Kostenloser Versand: sicher verpackt als versichertes Paket mit Sendungsnummer."}


def set_shipping(tx: dict, kind: str, free: bool, base: float) -> None:
    """Безкоштовна / платна доставка: вартість для покупця, позначка для eBay і речення в описі."""
    tx.update(ship=0.0 if free else base, ship_free=free)
    a, b = (SHIP_PAID_DE[kind], SHIP_FREE_DE[kind]) if free else (SHIP_FREE_DE[kind], SHIP_PAID_DE[kind])
    tx["desc"] = tx["desc"].replace(a, b)


def item_prices(pr: dict, ship: float) -> dict:
    """Платна доставка (09.10, як купує сам користувач): ціна товару = ціна «разом» мінус доставка; пороги — теж без неї."""
    return dict(item=max(1, round(pr["list"] - ship)), accept=max(1, round(pr["accept"] - ship)),
                decline=max(1, round(pr["decline"] - ship)))


def build_card(row: int | None, title: str, r: dict, pr: dict, comp: list[float], tx: dict, cost: float | None,
               edited: list[str] | None = None) -> str:
    """Перше повідомлення — загальне, українською: скільки ставити, скільки лишиться, що на ринку."""
    e = lambda x: html.escape(x, quote=False)   # noqa: E731
    ip = item_prices(pr, tx["ship"])
    # 10.10 (прохання користувача): коротко; «перед публікацією» і поля форми — у покрокових (кнопка «📋 Покроково»)
    lines = [f"🏷 <b>Продаж{' №' + str(row) if row else ''}</b> · <i>{e(title[:80])}</i>"
             + (f" · ✏️ змінено: {', '.join(edited)}" if edited else ""),
             f"📝 На eBay: <i>{e(tx['title'])}</i>",   # 14.10: видно, що саме піде в оголошення
             (f"💶 <b>{ip['item']} €</b> з безкоштовною доставкою" if tx.get("ship_free") else
              f"💶 <b>{ip['item']} €</b> + доставка {money_de(tx['ship'])} €")
             + (f" · купив за {cost:.0f} € → прибуток ≈ <b>{pr['profit_list']:.0f} €</b>" + _taxed(pr["profit_list"]) if cost else ""),
             f"🤝 Автоприйняти від {ip['accept']} € · відхиляти нижче {ip['decline']} €",
             f"📊 Продано за 30 днів {r['quick_sale']}–{r['median_sale']} € · "
             + (f"зараз схожих {len(comp)}, від {comp[0]:.0f} €" if comp else "схожих зараз немає"),
             *[f"⚠️ {e(w)}" for w in pr["warn"]]]
    return "\n".join(lines)


def build_steps(row: int | None, pr: dict, tx: dict) -> str:
    """Друге повідомлення — покроково в порядку форми eBay «Angebot fertigstellen»; назви полів німецькою, як на сайті."""
    e = lambda x: html.escape(x, quote=False)   # noqa: E731
    ip = item_prices(pr, tx["ship"])
    before = ["✅ <b>Перед публікацією:</b>", *[f"• {e(b)}" for b in tx["before"]], ""]
    code = lambda v: f"<code>{e(str(v))}</code>"   # noqa: E731
    field = lambda k, v: f"   • {e(k)}: {code(v)}"   # noqa: E731
    out = ["📋 <b>Покроково на eBay</b> · «Angebot fertigstellen»", "", *before,
           "1️⃣ <b>FOTOS &amp; VIDEO</b> — Hauptfoto: товар цілком; далі наклейка крупно, екран тесту.", "",
           "2️⃣ <b>TITEL</b> → Angebotstitel:", code(tx["title"]), "",
           f"3️⃣ <b>ARTIKELKATEGORIE</b> → {e(tx['category'])} (eBay підставить сам; інше — «Bearbeiten»)", "",
           "4️⃣ <b>ARTIKELMERKMALE</b>"]
    if tx.get("warn"):
        out.append(f"   ⚠️ {e(tx['warn'])}")
    out += ["   <i>Erforderlich:</i>", *[field(k, v) for k, v in tx["required"]],
            "   <i>Weitere (optional) — «Mehr anzeigen»:</i>", *[field(k, v) for k, v in tx["more"] if v != "—"],
            *([f"   • {e(k)}: залиш порожнім" for k, v in tx["more"] if v == "—" and k == "Speicher-Eigenschaften"]), "",
            "5️⃣ <b>ZUSTAND</b> → Artikelzustand: <b>Gebraucht</b>", "   Zustandsbeschreibung:", code(tx["cond"]), "",
            "6️⃣ <b>BESCHREIBUNG</b>:", code(tx["desc"]), "",
            "7️⃣ <b>PREISGESTALTUNG</b>", "   • Format: Sofort-Kaufen", field("Artikelpreis", f"{ip['item']},00"),
            "   • Preisvorschläge zulassen: увімкни",
            field("Mindestbetrag für Preisvorschlag", f"{ip['decline']},00"),
            field("Automatisch akzeptieren", f"{ip['accept']},00"), "",
            "8️⃣ <b>DETAILS ZUR LIEFERUNG</b> → «Nur Versand»",
            f"   • Paketgewicht: {e(tx['weight'])} · Paketmaße: {e(tx['dims'])}",
            "   • Inlandsversand → Ersten Versandservice hinzufügen → <b>DHL Paket</b>",
            *(["   • «Kostenlosen Versand anbieten» — <b>з галочкою</b> (доставка вже в ціні)"] if tx.get("ship_free") else
              [field("Käufer zahlt", money_de(tx["ship"])), "   • «Kostenlosen Versand anbieten» — без галочки"]), "",
            "9️⃣ <b>ANGEBOT BEWERBEN</b> → Basis і Premium вимкнені (перший тиждень)", "",
            "🔟 <b>Angebot einstellen</b> → потім у бот: " + code(f"виставив {row or 'N'} {ip['item']}"), "",
            "📦 <b>Після продажу:</b> Mein eBay → Verkauft → «Versandetikett kaufen» → DHL Paket → запакуй "
            "(антистатичний пакет + коробка з наповнювачем) → у бот: " + code(f"відправив {row or 'N'} трек")]
    return "\n".join(out)


def keyboard(tx: dict, r: dict, row: int | None = None, ebay: bool = False, listed: bool = False) -> dict:
    """10.10: під карткою «продати» — дії: автоматично, покроково (вручну), конкуренти.
    16.10: уже виставлене на eBay — «🔁 Оновити на eBay» (правки в живе оголошення) замість «🚀»."""
    q, cat = competitor_query(r)
    rows = []
    if row and ebay:
        rows.append([{"text": "🔁 Оновити оголошення на eBay", "callback_data": f"c|оновити|{row}"}] if listed else
                    [{"text": "🚀 Виставити на eBay автоматично", "callback_data": f"c|авто|{row}"}])
        rows.append([{"text": "✏️ Змінити ціну / назву / опис / фото", "callback_data": f"c|змінити|{row}"}])
        rows.append([{"text": "📦 Зробити доставку платною", "callback_data": f"c|платна|{row}"} if tx.get("ship_free") else
                     {"text": "🚚 Зробити доставку безкоштовною", "callback_data": f"c|безкоштовна|{row}"}])
    if row:
        rows.append([{"text": "📋 Покроково (вручну) + фото", "callback_data": f"c|кроки|{row}"}])
    rows.append([{"text": "🔎 Конкуренти на eBay",
                  "url": f"https://www.ebay.de/sch/{cat}/i.html?_nkw={quote_plus(q)}&LH_BIN=1&LH_ItemCondition=3000&_sop=15"}])
    return {"inline_keyboard": rows}


def steps_keyboard(tx: dict, r: dict) -> dict:
    """Під покроковими: скопіювати назву / Herstellernummer, відкрити форму eBay."""
    q, cat = competitor_query(r)
    return {"inline_keyboard": [
        [{"text": "📋 Скопіювати назву", "copy_text": {"text": tx["title"][:256]}}],
        *([[{"text": "📋 Herstellernummer", "copy_text": {"text": tx["pn"][:256]}}]] if tx.get("pn") else []),
        [{"text": "🔎 Конкуренти на eBay", "url": f"https://www.ebay.de/sch/{cat}/i.html?_nkw={quote_plus(q)}&LH_BIN=1&LH_ItemCondition=3000&_sop=15"}],
        [{"text": "➕ Відкрити форму eBay", "url": "https://www.ebay.de/sl/prelist/suggest"}]]}


def apply_edits(r: dict, pr: dict, tx: dict, cost: float | None, ed: dict | None, base_ship: float | None = None) -> list[str]:
    """14.10 (користувач: «хочу підправити перед фінальним пушем, з телефона»): правки з бота — «ціна N X» (ціна товару без
    доставки), «назва N …» (назва на eBay), «опис N …» (дописати в опис). Пороги Preisvorschlag — від нової ціни, як у
    plan_price, але не вище за неї. → що змінено (для картки)."""
    ed = ed or {}
    done = []
    if ed.get("price"):
        # 26.10: «ціна N X» — ціна товару при доставці, що була в момент правки (pship: free / paid); далі «разом» не
        # змінюється, а ціна товару перераховується під поточну доставку (кнопки 🚚 / 📦). Без pship — поточна доставка.
        ps = ed.get("pship")
        lst = float(ed["price"]) + (0.0 if ps == "free" else (tx["ship"] if base_ship is None else base_ship) if ps == "paid"
                                    else tx["ship"])
        floor = math.ceil(pr["floor"]) if cost else 0
        pr["accept"] = min(lst, max(floor, round(lst * ACCEPT_SHARE)))
        pr["decline"] = min(pr["accept"], max(floor, round(min(r["quick_sale"] * DECLINE_SHARE, lst * 0.87))))
        pr["list"] = lst
        pr["warn"] = [w for w in pr["warn"] if "три найдешевші" not in w]
        if cost:
            pr["profit_list"], pr["profit_accept"] = net_of(r, lst) - cost, net_of(r, pr["accept"]) - cost
            if pr["profit_list"] < 0:
                pr["warn"].insert(0, f"за цією ціною — мінус {-pr['profit_list']:.0f} €")
        pr["net_list"] = net_of(r, lst)
        done.append("ціна")
    if ed.get("title"):
        tx["title"] = str(ed["title"]).strip()[:80]
        done.append("назва")
    if ed.get("desc"):
        tx["desc"] = tx["desc"].rstrip() + "\n\n" + str(ed["desc"]).strip()[:1000]
        done.append("опис")
    if ed.get("ship") in ("free", "paid"):
        done.append("доставка")
    return done


def prepare(title: str, cost: float | None, row: int | None = None, comp_fn=competitors, edits: dict | None = None,
            ship: dict | None = None) -> dict | None:
    """Товар → ринок, ціна, німецькі тексти (спільне для картки й автопублікації). None — не впізнали."""
    r = identify(title)
    if not r:
        return None
    try:
        comp = comp_fn(r)
    except Exception as e:
        print(f"конкуренти: {e.__class__.__name__}")
        comp = []
    pr = plan_price(r, cost, comp)
    if r["kind"] == "ram" and r["spec"]["modules"] == 1 and not r["spec"].get("explicit_single"):
        pr["warn"].insert(0, f"з назви не видно, скільки планок — порахував як ОДНУ на {r['spec']['total']} ГБ. "
                             f"Якщо це кіт, напиши: продати {row or 'N'} <назва з 2x…GB>")
    tx = ram_texts(title, r) if r["kind"] == "ram" else console_texts(title, r)
    base = tx["ship"]
    if ship:   # 20.10: перевиставлення / живе оголошення — доставка як на eBay (безкоштовна лишається безкоштовною)
        tx.update(ship_service=ship.get("service"))
        set_shipping(tx, r["kind"], bool(ship.get("free")), float(ship["cost"]) or base)
    if (edits or {}).get("ship") in ("free", "paid"):   # 26.10: кнопка 🚚 / 📦
        set_shipping(tx, r["kind"], edits["ship"] == "free", base)
    edited = apply_edits(r, pr, tx, cost, edits, base)
    return dict(r=r, pr=pr, comp=comp, tx=tx, edited=edited)


def make(title: str, cost: float | None, row: int | None = None, comp_fn=competitors, ebay: bool = False,
         edits: dict | None = None, listed: bool = False, ship: dict | None = None) -> tuple[str, dict | None]:
    p = prepare(title, cost, row, comp_fn, edits, ship)
    if not p:
        return (f"🤔 Не впізнав товар «{html.escape(title[:80])}».\nНапиши повніше, наприклад:\n"
                f"<code>продати {row or 'N'} Kingston Fury 2x16GB DDR4 3200</code>", None, "")
    r, pr, comp, tx = p["r"], p["pr"], p["comp"], p["tx"]
    make.steps_kb = steps_keyboard(tx, r)
    return build_card(row, title, r, pr, comp, tx, cost, p["edited"]), keyboard(tx, r, row, ebay, listed), build_steps(row, pr, tx)


# ----------------------------------------------------------------------------- Kleinanzeigen (24.10)
# Користувач: «другий eBay із KA» — той самий товар паралельно на KA. API в KA немає, а автопостинг через сайт порушує
# правила KA (10.10 вже був блок за IP), тож бот готує все для ручного виставлення за 2 хв: назва, опис, ціна, пороги
# пропозицій, фото; далі автоматично — фільтр пропозицій (tools/kafilter.gs), продаж → зняти з eBay (tools/ledger.gs).
KA_TITLE_MAX = 65
KA_ACCEPT, KA_DECLINE = 0.93, 0.87     # від ціни на KA: «погоджуйся» / нижче — бот не показує
KA_DISCOUNT = 0.90                     # 24.10 (користувач: «124 € — це 20 € заробітку, хочу нормальну ціну»): на KA —
                                       # як чистими з продажу на eBay за медіаною, мінус 10% (на KA чекають знижки)
KA_POST_URL = "https://www.kleinanzeigen.de/p-anzeige-aufgeben.html"


def ka_plan(r: dict, cost: float | None, price: float | None = None) -> dict:
    """Ціна на KA: стільки, скільки лишилось би з продажу на eBay за медіаною, мінус 10% (доставку на KA платить покупець,
    комісії немає); або своя (`продати N ka 159`). Не нижче мінімуму прибутку від ціни купівлі. Пороги пропозицій — як на
    eBay, але від ціни на KA."""
    floor = cost + max(MIN_PROFIT_ABS, MIN_PROFIT_PCT * cost) + ram_alert.PACK_RAM if cost else 0.0
    price = round(price) if price else max(nice(KA_DISCOUNT * net_of(r, r["median_sale"])), math.ceil(floor))
    accept = max(math.ceil(floor), round(price * KA_ACCEPT))
    decline = min(accept, max(math.ceil(floor), round(price * KA_DECLINE)))
    net = price - ram_alert.PACK_RAM
    return dict(price=price, accept=accept, decline=decline, floor=floor, net=net,
                profit=(net - cost) if cost else None, ebay_net=net_of(r, r["median_sale"]))


def ka_title(t: str, pn: str | None = None) -> str:
    """Назва eBay → до 65 знаків (ліміт KA). 24.10 (користувач: «не додає getestet — менше охоплення»): «getestet» —
    завжди; номер моделі — лише якщо влазить (він і так в описі, а пошук KA за замовчуванням шукає й в описі);
    «DIMM Desktop RAM» → «RAM» (ноутбучна — «SO-DIMM RAM»)."""
    t = re.sub(r"\s*[–-]?\s*getestet\b", "", re.sub(r"\s+", " ", t)).strip()
    if pn:
        t = re.sub(r"\s*" + re.escape(pn), "", t).strip()
    t = re.sub(r"\bSO-DIMM Laptop RAM\b", "SO-DIMM RAM", t)
    t = re.sub(r"\b(?:DIMM Desktop RAM|ECC Unbuffered DIMM|Desktop RAM)\b", "RAM", t)
    t = re.sub(r"(?<!SO-)(?<!U)\bDIMM\b", "RAM", t)                          # коротка форма з eBay «… CMK… DIMM getestet»
    t = re.sub(r"\bSO-DIMM\b(?! RAM)", "SO-DIMM RAM", t).strip(" –-,")
    for cand in ([t, pn, "getestet"] if pn else [], [t, "getestet"]):
        c = " ".join(x for x in cand if x)
        if cand and len(c) <= KA_TITLE_MAX:
            return c
    while len(t) + len(" getestet") > KA_TITLE_MAX and " " in t:
        t = t.rsplit(" ", 1)[0]
    return t.rstrip(" –-,(") + " getestet"


def ka_desc(desc: str) -> str:
    ka = ("Versand gut und antistatisch verpackt mit Sendungsnummer – am liebsten über „Direkt kaufen“ "
          "(mit Käuferschutz). Abholung in Hamburg ist auch möglich.")
    return desc.replace(SHIP_PAID_DE["ram"], ka).replace(SHIP_FREE_DE["ram"], ka)


def ka_card(row: int | None, r: dict, tx: dict, cost: float | None, price: float | None = None) -> tuple[str, dict]:
    e = lambda x: html.escape(str(x), quote=False)   # noqa: E731
    pl, title, desc = ka_plan(r, cost, price), ka_title(tx["title"], tx.get("pn")), ka_desc(tx["desc"])
    n = row or "N"
    lines = [f"🟢 <b>Продаж №{n} на Kleinanzeigen</b>",
             f"💶 <b>{pl['price']} € VB</b> + доставка (платить покупець) · тобі ≈ {pl['net']:.0f} €"
             + (f" · купив за {cost:.0f} € → прибуток ≈ <b>{pl['profit']:.0f} €</b>" if cost else ""),
             f"🤝 Пропозиції: від <b>{pl['accept']} €</b> — погоджуйся; нижче {pl['decline']} € — бот у Telegram не покаже",
             f"📊 На eBay за медіаною тобі лишилось би ≈ {pl['ebay_net']:.0f} € — але там чекати довше",
             *([f"⚠️ Нижче мінімуму прибутку ({math.ceil(pl['floor'])} €) — пропозиції бот рахуватиме від нього"]
               if price and cost and price < pl["floor"] else []),
             f"Інша ціна — <code>продати {row or 'N'} ka 159</code>",
             "",
             "<b>Як виставити (2 хв)</b> — застосунок або сайт KA → «Anzeige aufgeben»:",
             "1. Kategorie: <b>Elektronik → PC-Zubehör &amp; Software</b> (KA підкаже сам за назвою)",
             "2. Titel:", f"<code>{e(title)}</code>",
             "3. Beschreibung:", f"<code>{e(desc)}</code>",
             f"4. Preis: <code>{pl['price']}</code> → «VB»",
             "5. Versand: «Versand möglich» → увімкни <b>«Direkt kaufen»</b> → Hermes S або DHL до 2 кг (платить покупець)",
             "6. Фото — з альбому нижче (білий фон; екран MemTest — як доказ)",
             "7. «Anzeige aufgeben» → у бот: " + f"<code>виставив {n} ka {pl['price']} посилання</code>",
             "",
             "Далі сам: пропозиції нижче порогу й «а з доставкою?» на самовивіз — не показую; продалось через «Direkt kaufen» — "
             "зніму з eBay; продалось на eBay — нагадаю зняти з KA."]
    kb = {"inline_keyboard": [[{"text": "📋 Скопіювати назву", "copy_text": {"text": title}}],
                              [{"text": "➕ Відкрити «Anzeige aufgeben»", "url": KA_POST_URL}]]}
    return "\n".join(lines), kb


def ka_mode(d: dict, comp_fn=None) -> bool:
    row, cost = d.get("row"), d.get("cost")
    p = prepare(d.get("title") or "", float(cost) if cost not in (None, "") else None, row, comp_fn or (lambda r: []))
    if not p or p["r"].get("kind") != "ram":
        return office_send_html(f"🤔 Не впізнав товар №{row} — картку для KA не зробив.", None)
    text, kb = ka_card(row, p["r"], p["tx"], float(cost) if cost not in (None, "") else None, d.get("ka_price") or None)
    ok = office_send_html(text, kb)
    if d.get("photos"):
        print(f"фото для KA: {office_send_studio_preview(d['photos'], row, caption=f'📸 Фото для KA №{row} — збережи й додай в оголошення')}")
    return ok


def split_html(text: str, limit: int = 3900) -> list[str]:
    """18.10 (аудит): Telegram — до 4096 символів; обрізаний посеред тегу HTML він відхиляє цілком (повідомлення губилось,
    напр. «🏷 Виставлено» з ~16+ лотами). Ріжемо між абзацами (порожній рядок), не розриваючи блоків."""
    out, cur = [], ""
    for block in text.split("\n\n"):
        if cur and len(cur) + len(block) + 2 > limit:
            out.append(cur)
            cur = ""
        cur = (cur + "\n\n" + block) if cur else block
    out.append(cur)
    return [x[:4096] for x in out if x]


def office_send_html(text: str, markup: dict | None) -> bool:
    parts = split_html(text)
    if len(parts) > 1:   # кнопки — під останнім шматком
        return all([office_send_html(x, markup if i == len(parts) - 1 else None) for i, x in enumerate(parts)])
    import requests
    token = os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN
    data = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if markup:
        data["reply_markup"] = json.dumps(markup, ensure_ascii=False)
    r = requests.post(f"{config.TELEGRAM_API_BASE}/bot{token}/sendMessage", data=data, timeout=15)
    if r.status_code == 400 and markup:   # старий клієнт без copy_text — картка важливіша за кнопку
        data["reply_markup"] = json.dumps({"inline_keyboard": markup["inline_keyboard"][1:]}, ensure_ascii=False)
        r = requests.post(f"{config.TELEGRAM_API_BASE}/bot{token}/sendMessage", data=data, timeout=15)
    print(f"Telegram: {r.status_code}")
    return r.status_code == 200


def office_send_album(photos: list, caption: str) -> int:
    """08.10: фото товару, збережені в обліку (file_id Telegram), — альбомом слідом за оголошенням. → скільки надіслано."""
    import requests
    token = os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN
    api, n = f"{config.TELEGRAM_API_BASE}/bot{token}/", 0
    for kind in ("photo", "document"):
        part = [p["id"] for p in photos or [] if isinstance(p, dict) and p.get("t") == kind and p.get("id")]
        for i in range(0, len(part), 10):
            chunk = part[i:i + 10]
            if len(chunk) == 1:
                r = requests.post(api + ("sendPhoto" if kind == "photo" else "sendDocument"),
                                  data={"chat_id": config.TELEGRAM_CHAT_ID, kind: chunk[0], "caption": caption}, timeout=30)
            else:
                media = [dict({"type": kind, "media": fid}, **({"caption": caption} if j == 0 else {})) for j, fid in enumerate(chunk)]
                r = requests.post(api + "sendMediaGroup", data={"chat_id": config.TELEGRAM_CHAT_ID,
                                                                "media": json.dumps(media, ensure_ascii=False)}, timeout=30)
            n += len(chunk) if r.status_code == 200 else 0
    return n


def product_first(blobs: list[bytes]) -> list[bytes]:
    """17.10 (користувач: головним на eBay став екран MemTest, бо його надіслав першим): спершу фото товару (світлий рівний
    фон), потім екрани тесту, квитанції тощо; порядок усередині кожної групи — як надіслано."""
    import io
    try:
        import photo_studio
        from PIL import Image, ImageOps
    except ImportError:
        return blobs

    def screen(b: bytes) -> int:
        try:
            return 0 if photo_studio.plain_background(ImageOps.exif_transpose(Image.open(io.BytesIO(b))).convert("RGB")) else 1
        except Exception:
            return 1
    return sorted(blobs, key=screen)


def studio_or_original(data: bytes) -> bytes:
    """16.10: фото на білому фоні (research/photo_studio.py); екран тесту / темний фон / помилка — оригінал.
    17.10: контур — Poof.bg, якщо є POOF_KEY (інакше наша маска)."""
    try:
        import photo_studio
        import requests
        return photo_studio.studio(data, os.getenv("POOF_KEY", ""), requests.post, os.getenv("PHOTO_CACHE", "")) or data
    except Exception as e:
        print(f"обробка фото: {e.__class__.__name__}")
        return data


def office_send_studio_preview(photos: list, row: int, file_fn=None, caption: str | None = None) -> int:
    """Альбом «так фото підуть на eBay» — оброблені версії збережених фото (до 10). → скільки надіслано."""
    import requests
    token = os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN
    file_fn = file_fn or tg_file
    files, media = {}, []
    blobs = [b for b in (file_fn(ph["id"], token) for ph in [x for x in photos or [] if isinstance(x, dict) and x.get("id")][:10]) if b]
    for i, data in enumerate(product_first(blobs)):
        files[f"p{i}"] = (f"p{i}.jpg", studio_or_original(data), "image/jpeg")
        media.append({"type": "photo", "media": f"attach://p{i}"})
    if not media:
        return 0
    media[0]["caption"] = caption or (f"📸 Так фото №{row} підуть на eBay: білий фон, без тіні (екран тесту — як є). "
                                      f"Не подобається — напиши «оригінали {row}».")
    api = f"{config.TELEGRAM_API_BASE}/bot{token}/"
    if len(media) == 1:
        resp = requests.post(api + "sendPhoto", data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": media[0]["caption"]},
                             files={"photo": files["p0"] if "p0" in files else next(iter(files.values()))}, timeout=60)
    else:
        resp = requests.post(api + "sendMediaGroup", data={"chat_id": config.TELEGRAM_CHAT_ID,
                                                           "media": json.dumps(media, ensure_ascii=False)}, files=files, timeout=90)
    return len(media) if resp.status_code == 200 else 0


def office_send_photo(data: bytes, caption: str) -> bool:
    """Одне фото (JPEG) у бот «Облік і продаж»."""
    import requests
    token = os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN
    r = requests.post(f"{config.TELEGRAM_API_BASE}/bot{token}/sendPhoto", data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption},
                      files={"photo": ("onhand.jpg", data, "image/jpeg")}, timeout=60)
    print(f"Telegram: {r.status_code}")
    return r.status_code == 200


def office_send_url_album(pics: list[tuple[str, str]], get) -> int:
    """16.10: фото за посиланнями (eBay / KA) — завантажуємо самі й шлемо альбомом (Telegram не завжди тягне чужі адреси).
    pics — [(підпис, url)]. → скільки надіслано."""
    import requests
    token = os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN
    api, files, media = f"{config.TELEGRAM_API_BASE}/bot{token}/", {}, []
    for i, (cap, url) in enumerate(pics[:10]):
        try:
            r = get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200 or not r.content:
                continue
        except Exception as e:
            print(f"фото {i}: {e.__class__.__name__}")
            continue
        files[f"p{i}"] = (f"p{i}.jpg", r.content, "image/jpeg")
        media.append({"type": "photo", "media": f"attach://p{i}", "caption": cap[:200]})
    if not media:
        return 0
    if len(media) == 1:
        k = next(iter(files))
        resp = requests.post(api + "sendPhoto", data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": media[0]["caption"]},
                             files={"photo": files[k]}, timeout=60)
    else:
        resp = requests.post(api + "sendMediaGroup", data={"chat_id": config.TELEGRAM_CHAT_ID,
                                                           "media": json.dumps(media, ensure_ascii=False)}, files=files, timeout=60)
    return len(media) if resp.status_code == 200 else 0


# ----------------------------------------------------------------------------- автопублікація на eBay (09.10)
def tg_file(file_id: str, token: str) -> bytes | None:
    import requests
    r = requests.get(f"{config.TELEGRAM_API_BASE}/bot{token}/getFile", params={"file_id": file_id}, timeout=20)
    path = ((r.json() if r.status_code == 200 else {}).get("result") or {}).get("file_path")
    if not path:
        return None
    f = requests.get(f"{config.TELEGRAM_API_BASE}/file/bot{token}/{path}", timeout=60)
    return f.content if f.status_code == 200 else None


def post_back(data: dict, key: str) -> bool:
    """Зашифровано → Apps Script (адреса — з вебхука бота «Облік і продаж»): ключ eBay, «виставлено» в облік.
    Підпис — токеном бота, тож підробити такий запит без токена не можна."""
    import uuid
    import requests
    info = requests.get(f"{config.TELEGRAM_API_BASE}/bot{key}/getWebhookInfo", timeout=20).json()
    url = (info.get("result") or {}).get("url") or ""
    if not url.startswith("https://script.google.com/"):
        print("post_back: немає адреси Apps Script")
        return False
    nonce = uuid.uuid4().hex
    blob, mac = seal(data, key, nonce)
    r = requests.post(url, data=json.dumps({"sealed": {"blob": blob, "mac": mac, "nonce": nonce}}),
                      headers={"Content-Type": "application/json"}, timeout=30, allow_redirects=False)
    print(f"post_back: {r.status_code}")
    return r.status_code in (200, 302)


def _short(items: list[str], n: int = 4) -> str:
    return "\n".join(f"• {html.escape(x[:300])}" for x in items[:n])


def ebay_mode(d: dict, key: str, post=None, file_fn=tg_file, send=None, back=post_back, comp_fn=competitors) -> bool:
    """«ebay вхід» → посилання на дозвіл; вставлена адреса з кодом → ключ в Apps Script; «🚀 Виставити» → оголошення."""
    import ebay_list as el
    if post is None:
        import requests
        post = requests.post
    send = send or office_send_html
    app, cert, runame = config.EBAY_APP_ID, config.EBAY_CERT_ID, os.getenv("EBAY_RUNAME") or ""
    mode = d.get("mode")
    if mode == "auth_url":
        if not runame:
            return send("⚠️ Підключення eBay ще не налаштоване (немає RuName у секретах GitHub) — напиши Claude.", None)
        return send("🔑 <b>Підключення eBay</b>\n1. Натисни кнопку, увійди у свій eBay і натисни «Agree» / «Zustimmen».\n"
                    "2. eBay покаже сторінку «Authorization successfully completed» (або схожу).\n"
                    "3. Скопіюй <b>адресу цієї сторінки</b> (рядок браузера, починається з https://) і встав сюди.\n"
                    "⏱ Код в адресі дійсний 5 хвилин.", {"inline_keyboard": [[{"text": "🔑 Дозволити боту на eBay",
                                                                              "url": el.consent_url(app, runame)}]]})
    if mode == "auth_code":
        code = el.code_from(d.get("code") or "")
        if not code:
            return send("⚠️ В адресі немає коду eBay (…code=…). Натисни «ebay вхід» ще раз.", None)
        try:
            rt, exp = el.exchange_code(code, app, cert, runame, post)
        except el.EbayError as e:
            return send("⚠️ eBay не прийняв код: " + html.escape(str(e)[:200]) +
                        "\nСкоріш за все, минуло понад 5 хвилин — напиши «ebay вхід» і встав адресу одразу.", None)
        if not back({"kind": "ebay_rt", "rt": rt, "exp": exp}, key):
            return send("⚠️ Ключ eBay отримав, але не зміг зберегти в Apps Script — напиши Claude.", None)
        print("ключ eBay передано в Apps Script")
        return True
    if mode == "ebay":   # 10.10: трек, ціна, відповідь покупцю, пропозиції — кнопками (research/ebay_actions.py)
        import ebay_actions
        if not d.get("rt"):
            return send("🔑 eBay ще не підключено — напиши «ebay вхід».", None)
        try:
            return ebay_actions.run(d, el.access_token(d["rt"], app, cert, post), post, send, back, key)
        except el.AuthError as e:
            back({"kind": "ebay_bad"}, key)
            return send("🔑 Ключ eBay більше не діє (" + html.escape(str(e)[:120]) + "). Напиши «ebay вхід» — підключимо знову.", None)
        except el.EbayError as e:
            return send(f"⚠️ eBay: {html.escape(str(e)[:300])} — перешли це Claude.", None)
    if mode == "parcels":   # 12.10: «📦 Де посилки» — eBay (якщо підключено) + живий статус (17TRACK / DHL) + облік
        import parcels
        token = None
        if d.get("rt"):
            try:
                token = el.access_token(d["rt"], app, cert, post)
            except Exception as e:   # без eBay — лишаються DHL і облік
                print(f"eBay токен: {e.__class__.__name__}")
        import requests
        if d.get("watch"):   # фонова перевірка кожні 3 години — без повідомлення й фото, лише події
            text, events = parcels.check(d, token, post, requests.get)
        else:
            text, events, pics = parcels.check(d, token, post, requests.get, photos=True)
        if events:   # 13.10: доставлено / у відділенні → облік (статус «Отримано», сповіщення — один раз)
            back({"kind": "parcels", "events": events}, key)
        if d.get("watch"):
            print(f"parcels watch: {len(events)} подій")
            return True
        ok = send(text, None)
        if pics:   # 16.10: головні фото посилок до тебе — альбомом, з підписом «№N назва»
            print(f"фото посилок: {office_send_url_album(pics, requests.get)} з {len(pics)}")
        return ok
    if mode == "merge":   # 18.10: такий самий товар уже на eBay — +1 шт до того оголошення замість дубля
        row, item_id = d.get("row"), str(d.get("item_id") or "")
        if not d.get("rt") or not item_id:
            return send(f"⚠️ Не знаю оголошення eBay для №{row}.", None)
        try:
            res = el.add_quantity(item_id, el.access_token(d["rt"], app, cert, post), post)
        except el.AuthError as e:
            back({"kind": "ebay_bad"}, key)
            return send("🔑 Ключ eBay більше не діє (" + html.escape(str(e)[:120]) + "). Напиши «ebay вхід».", None)
        except el.EbayError as e:
            return send(f"⚠️ Не вийшло додати №{row}: {html.escape(str(e)[:300])}\nНічого не змінено.", None)
        if not res["ok"]:
            return send(f"⚠️ eBay не змінив кількість:\n{_short(res['errors'])}\nНічого не змінено.", None)
        back({"kind": "merged", "row": row, "item_id": item_id, "price": d.get("price"), "twin": d.get("twin"), "qty": res["qty"]}, key)
        return send(f"➕ №{row} додано до оголошення №{d.get('twin')}: тепер <b>{res['qty']} шт</b>"
                    + (f" за {d['price']} €" if d.get("price") else "") + ". Продаж запишеться на кожен номер по черзі.",
                    {"inline_keyboard": [[{"text": "🔗 Оголошення", "url": f"https://www.ebay.de/itm/{item_id}"}]]})
    if mode == "relist":   # 18.10 (крок 6): «♻️ Перевиставити» — завершити й виставити заново (новий старт у пошуку)
        row, item_id = d.get("row"), str(d.get("item_id") or "")
        what = f"№{row}" if row else "товару"
        if not d.get("rt") or not item_id:
            return send(f"⚠️ Не знаю оголошення eBay для {what} — перевиставити не можу.", None)
        try:
            token = el.access_token(d["rt"], app, cert, post)
            try:
                ship = el.shipping_of(item_id, token, post)
            except el.EbayError:
                ship = None
            res = el.end_item(item_id, token, post)
        except el.AuthError as e:
            back({"kind": "ebay_bad"}, key)
            return send("🔑 Ключ eBay більше не діє (" + html.escape(str(e)[:120]) + "). Напиши «ebay вхід».", None)
        except el.EbayError as e:
            return send(f"⚠️ Не вийшло завершити {what}: {html.escape(str(e)[:300])}\nНічого не змінено.", None)
        if not res["ok"]:
            return send(f"⚠️ eBay не завершив {what}:\n{_short(res['errors'])}\nНічого не змінено.", None)
        back({"kind": "ended", "row": row, "item_id": item_id}, key)
        return ebay_mode(dict(d, mode="publish", item_id="", ship=ship), key, post, file_fn, send, back, comp_fn)
    if mode == "revise":   # 16.10: «🔁 Оновити на eBay» — правки в живе оголошення
        return revise_mode(d, key, app, cert, post, file_fn, send, back, comp_fn)
    if mode == "listings":   # 16.10: «🏷 Виставлено» — активні лоти eBay (перегляди, спостерігачі) + KA з обліку
        import listings
        token = None
        if d.get("rt"):
            try:
                token = el.access_token(d["rt"], app, cert, post)
            except Exception as e:
                print(f"eBay токен: {e.__class__.__name__}")
        lots = None
        if token:
            try:
                lots = listings.active(token, post)
            except Exception as e:
                print(f"GetMyeBaySelling: {e.__class__.__name__}")
        text, kb = listings.report(d, token, post, lots=lots)
        if lots is not None:   # 17.10: заодно — зміни на eBay в облік (як фонова перевірка)
            ch = listings.sync(lots, d.get("rows") or [])
            if ch["lots"] or ch["ask"] or ch["ended"]:
                back({"kind": "sync", **ch}, key)
        return send(text, kb if kb["inline_keyboard"] else None)
    if mode == "sync":   # 17.10: фонова перевірка (раз на 3 години) — нові оголошення, зміна ціни, завершені → облік
        import listings
        if not d.get("rt"):
            return True
        try:
            token = el.access_token(d["rt"], app, cert, post)
            lots = listings.active(token, post)
        except Exception as e:
            print(f"sync: {e.__class__.__name__}")
            return True
        ch = listings.sync(lots, d.get("rows") or [])
        print(f"sync: лотів {len(lots)}, відомих {len(ch['lots'])}, питань {len(ch['ask'])}, завершених {len(ch['ended'])}")
        # 24.10 (користувач: «бот сам має оновлювати за статусом, мінімально йому писати»): продане, яке eBay уже показує
        # «Versendet» (етикетка eBay чи трек), → облік: відправлено, трек — і ранковий огляд більше не нагадує «відправ»
        try:
            import ebay_actions as ea
            ch["shipped"] = [dict(item_id=o["item_id"], sku=o["sku"], track=o["track"], carrier=o["carrier"], date=o["shipped_at"])
                             for o in ea.recent_orders(token, post) if o["shipped"]]
        except Exception as e:
            print(f"замовлення: {e.__class__.__name__}")
            ch["shipped"] = []
        if d.get("market"):   # 19.10: раз на добу — ціни наших лотів проти конкурентів
            ch["market"] = listings.market_check(lots, d.get("rows") or [], comp_fn=comp_fn)
            print(f"ринок: дорожчі за конкурентів — {len(ch['market'])}")
        if ch["lots"] or ch["ask"] or ch["ended"] or d.get("manual") or ch.get("market") or ch.get("shipped"):   # 19.10: «звірити» — відповідь і коли все збігається
            back({"kind": "sync", "manual": d.get("manual") or 0, "count": len(lots), **ch}, key)
        return True
    if mode == "got":   # 17.10: «отримав N» — фото з оголошення продавця: звірити, чи прийшло саме те
        import parcels
        import requests
        token = None
        if d.get("rt") and d.get("id"):
            try:
                token = el.access_token(d["rt"], app, cert, post)
            except Exception as e:
                print(f"eBay токен: {e.__class__.__name__}")
        url = parcels.picture_for({"id": d.get("id") or "", "link": d.get("link") or ""}, None, token, post, requests.get)
        if not url:
            print("фото продавця: немає")
            return True
        cap = (f"🔍 №{d.get('row')}: так було в оголошенні продавця — звір планки й наклейку"
               + (f" ({d['pn']})" if d.get("pn") else "") + ".\nТест пройде — надішли фото планок і екрана MemTest з підписом "
               f"«{d.get('row')}», «Перевірено» поставлю сам.")
        return office_send_url_album([(cap, url)], requests.get) > 0
    if mode == "onhand":   # 17.10: «🗂 На руках» — аркуш з фото: під кожним №N — сама планка
        import onhand
        import requests
        token = None
        if d.get("rt"):
            try:
                token = el.access_token(d["rt"], app, cert, post)
            except Exception as e:
                print(f"eBay токен: {e.__class__.__name__}")
        data = onhand.build(d, file_fn, key, token, post, requests.get)
        if not data:
            return True
        return office_send_photo(data, "🗂 На руках — фото")
    if mode != "publish":
        return send(f"⚠️ Невідомий режим «{html.escape(str(mode))}» — напиши Claude.", None)

    row, dry = d.get("row"), bool(d.get("dry"))
    what = f"№{row}" if row else "товару"
    if not d.get("rt"):
        return send("🔑 eBay ще не підключено — напиши «ebay вхід».", None)
    p = prepare(d.get("title") or "", float(d["cost"]) if d.get("cost") not in (None, "") else None, row, comp_fn,
                d.get("edits"), d.get("ship"))
    if not p:
        return send(f"🤔 Не впізнав товар {what} — виставляти автоматично не буду. Напиши «продати {row} &lt;назва повніше&gt;».", None)
    tx, pr = p["tx"], p["pr"]
    ip = item_prices(pr, tx["ship"])
    try:
        token = el.access_token(d["rt"], app, cert, post)
        pics = []
        blobs = []
        for ph in [x for x in d.get("photos") or [] if isinstance(x, dict) and x.get("id")]:
            if len(blobs) >= el.MAX_PHOTOS:
                break
            data = file_fn(ph["id"], key)
            if data and el.is_image(data):
                blobs.append(data)
        for i, data in enumerate(product_first(blobs)):   # 17.10: головне — фото товару, екран MemTest — після
            if not (d.get("edits") or {}).get("raw"):   # 16.10: білий фон («оригінали N» — без обробки)
                data = studio_or_original(data)
            pics.append(el.upload_picture(data, f"n{row}-{i + 1}", token, post))
        if not pics:
            return send(f"📸 Для {what} немає фото — надішли фото в бот з номером у підписі («{row}»), потім натисни ще раз.", None)
        res = el.verify_and_add(p["r"]["kind"], tx, ip, pics, f"ledger-{row}", token, post, dry)
    except el.AuthError as e:
        back({"kind": "ebay_bad"}, key)
        return send("🔑 Ключ eBay більше не діє (" + html.escape(str(e)[:120]) + "). Напиши «ebay вхід» — підключимо знову.", None)
    except el.EbayError as e:
        return send(f"⚠️ Не вийшло виставити {what}: {html.escape(str(e)[:300])}\nНічого не опубліковано. Перешли це Claude.", None)
    warn = ("\nℹ️ eBay каже:\n" + _short(el.explain(res["warnings"]), 4)) if res["warnings"] else ""
    parts = ", ".join(f"{k} {money_de(v)} €" for k, v in (res.get("fees") or {}).items())
    fee = (f"комісія за виставлення {money_de(res['fee'])} €" + (f": {parts}" if parts else "")) if res["fee"] else "без комісії за виставлення"
    if set(res.get("fees") or {}) <= {"InsertionFee"} and 0 < res["fee"] <= 0.5:
        # 09.10, довідка eBay «Gebühren für private Verkäufer»: 320 оголошень на місяць без Angebotsgebühr, далі 0,50 €;
        # Verify показує 0,50 € без урахування цього ліміту — реально 0 €
        fee = "виставлення безкоштовне (eBay рахує 0,50 € лише після 320 оголошень на місяць)"
    if not res["ok"]:
        return send(f"⚠️ eBay не прийняв оголошення {what}:\n{_short(res['errors'])}\nНічого не опубліковано. Перешли це Claude." + warn, None)
    if dry:
        var = "".join(f"\n• {k}: " + ("eBay не прийняв" if v is None else f"{money_de(v)} €")
                      for k, v in (res.get("variants") or {}).items())
        return send(f"🧪 Перевірка {what}: eBay прийняв би оголошення ✅ ({fee}, фото: {len(pics)}). Нічого не опубліковано.\n"
                    f"<i>{html.escape(tx['title'])}</i> — {ip['item']} € + {money_de(tx['ship'])} € доставка" +
                    (f"\n💸 Комісія за виставлення з іншими налаштуваннями:{var}" if var else "") + warn, None)
    link = f"https://www.ebay.de/itm/{res['item_id']}"
    back({"kind": "listed", "row": row, "price": ip["item"], "item_id": res["item_id"]}, key)
    return send(f"🚀 <b>{what} виставлено на eBay</b>\n<i>{html.escape(tx['title'])}</i>\n"
                f"💶 {ip['item']} € + доставка {money_de(tx['ship'])} € · пропозиції: автоприйняти від {ip['accept']} €, "
                f"відхиляти нижче {ip['decline']} €\n📸 фото: {len(pics)} · {fee}\n"
                "Перевір оголошення за посиланням; змінити щось — «Bearbeiten» на eBay." + warn,
                {"inline_keyboard": [[{"text": "🔗 Відкрити на eBay", "url": link}]]})


def revise_mode(d: dict, key: str, app: str, cert: str, post, file_fn, send, back, comp_fn) -> bool:
    """Ті самі назва / опис / ціна / пороги / фото, що в картці «продати N» з правками → ReviseFixedPriceItem."""
    import ebay_list as el
    row, item_id = d.get("row"), str(d.get("item_id") or "")
    what = f"№{row}" if row else "товару"
    if not d.get("rt"):
        return send("🔑 eBay ще не підключено — напиши «ebay вхід».", None)
    if not item_id:
        return send(f"⚠️ Не знаю номер оголошення eBay для {what} — онови вручну («Bearbeiten») або виставив заново.", None)
    parts = set(d["parts"]) if isinstance(d.get("parts"), list) else None   # 17.10: лише змінене (✏️), решта як на eBay
    if parts is not None and not parts:
        return send(f"✏️ У {what} нічого не змінено — спершу ✏️ (ціна / назва / опис / фото), потім 🔁.", None)
    if parts is not None and "ship" in parts:   # 26.10: інша доставка — інша ціна товару й речення в описі
        parts |= {"price", "desc"}
    try:
        token = el.access_token(d["rt"], app, cert, post)
    except el.AuthError as e:
        back({"kind": "ebay_bad"}, key)
        return send("🔑 Ключ eBay більше не діє (" + html.escape(str(e)[:120]) + "). Напиши «ebay вхід».", None)
    except el.EbayError as e:
        return send(f"⚠️ Не вийшло оновити {what}: {html.escape(str(e)[:300])}\nНічого не змінено.", None)
    try:
        live = el.shipping_of(item_id, token, post)   # 26.10: правки ціни — від доставки, що зараз на eBay
    except Exception as e:   # не дізнались — як раніше, від доставки за замовчуванням
        print(f"доставка з eBay: {e.__class__.__name__}")
        live = None
    p = prepare(d.get("title") or "", float(d["cost"]) if d.get("cost") not in (None, "") else None, row, comp_fn,
                d.get("edits"), live)
    if not p:
        return send(f"🤔 Не впізнав товар {what} — оновлювати автоматично не буду.", None)
    tx, ip = p["tx"], item_prices(p["pr"], p["tx"]["ship"])
    try:
        pics = []
        photos = [x for x in d.get("photos") or [] if isinstance(x, dict) and x.get("id")]
        if parts is not None and "photos" in parts and not photos:
            return send(f"📸 Для {what} немає фото — надішли нові з підписом «{row}», потім 🔁.", None)
        blobs = []
        for ph in (photos if parts is None or "photos" in parts else []):
            if len(blobs) >= el.MAX_PHOTOS:
                break
            data = file_fn(ph["id"], key)
            if data and el.is_image(data):
                blobs.append(data)
        for i, data in enumerate(product_first(blobs)):
            if not (d.get("edits") or {}).get("raw"):
                data = studio_or_original(data)
            pics.append(el.upload_picture(data, f"n{row}-r{i + 1}", token, post))
        res = el.revise_listing(item_id, tx, ip, pics, token, post, parts)
    except el.AuthError as e:
        back({"kind": "ebay_bad"}, key)
        return send("🔑 Ключ eBay більше не діє (" + html.escape(str(e)[:120]) + "). Напиши «ebay вхід».", None)
    except el.EbayError as e:
        return send(f"⚠️ Не вийшло оновити {what}: {html.escape(str(e)[:300])}\nНічого не змінено.", None)
    if not res["ok"]:
        return send(f"⚠️ eBay не прийняв зміни {what}:\n{_short(res['errors'])}\nОголошення лишилось як було.", None)
    price_sent = parts is None or "price" in parts
    back({"kind": "revised", "row": row, "item_id": item_id, **({"price": ip["item"]} if price_sent else {})}, key)
    warn = ("\nℹ️ eBay каже:\n" + _short(el.explain(res["warnings"]), 3)) if res["warnings"] else ""
    lines = [f"🔁 <b>{what} оновлено на eBay</b>"]
    if parts is None or "title" in parts:
        lines.append(f"<i>{html.escape(tx['title'])}</i>")
    if price_sent:
        lines.append(f"💶 {ip['item']} € " + ("з безкоштовною доставкою" if tx.get("ship_free") else f"+ доставка {money_de(tx['ship'])} €")
                     + f" · автоприйняти від {ip['accept']} €, відхиляти нижче {ip['decline']} €")
    if parts is not None and "desc" in parts:
        lines.append("📝 опис оновлено")
    if pics:
        lines.append(f"📸 фото: {len(pics)}")
    if parts is not None:
        lines.append("Решта — як була на eBay.")
    return send("\n".join(lines) + warn,
                {"inline_keyboard": [[{"text": "🔗 Відкрити на eBay", "url": f"https://www.ebay.de/itm/{item_id}"}]]})


def plan_line(title: str, cost: float | None, row=None) -> str | None:
    """«📈 План: продати за ≈ X € + доставка → прибуток ≈ Y €» — без запитів до eBay (ціни продажів з щоденних замірів)."""
    p = prepare(title, cost, row, comp_fn=lambda r: [])
    if not p:
        return None
    ip = item_prices(p["pr"], p["tx"]["ship"])
    return (f"📈 План: продати за ≈ {ip['item']} € + доставка" +
            (f" → прибуток ≈ {p['pr']['profit_list']:.0f} €" + _taxed(p["pr"]["profit_list"])
             if cost and p["pr"].get("profit_list") is not None else ""))


def plan_mode(d: dict, key: str, post=None) -> bool:
    import requests
    line = plan_line(d.get("title") or "", float(d["cost"]) if d.get("cost") not in (None, "") else None, d.get("row"))
    if not line or not d.get("mid"):
        print("план: не впізнав товар")
        return False
    data = {"chat_id": config.TELEGRAM_CHAT_ID, "message_id": d["mid"], "text": (d.get("text") or "") + "\n" + line,
            "disable_web_page_preview": "true"}
    if d.get("markup"):
        data["reply_markup"] = json.dumps(d["markup"], ensure_ascii=False)
    r = (post or requests.post)(f"{config.TELEGRAM_API_BASE}/bot{key}/editMessageText", data=data, timeout=15)
    print(f"план: Telegram {r.status_code}")
    return r.status_code == 200


def _event_inputs() -> dict:
    """Параметри запуску — з файлу події GitHub, а не з env (env видно в журналі кроку; репозиторій публічний)."""
    path = os.getenv("GITHUB_EVENT_PATH")
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("inputs") or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blob")
    ap.add_argument("--mac")
    ap.add_argument("--nonce")
    ap.add_argument("--title")
    ap.add_argument("--cost", type=float)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-event", action="store_true", help="blob/mac/nonce/test_title — з GITHUB_EVENT_PATH")
    a = ap.parse_args()
    if a.from_event:
        ev = _event_inputs()
        a.blob, a.mac, a.nonce = ev.get("blob") or None, ev.get("mac"), ev.get("nonce")
        if not a.blob and ev.get("test_title"):
            a.title, a.dry_run = ev["test_title"], True
    ebay, steps_only = False, False
    if a.blob:
        try:
            d = unseal(a.blob, a.mac, os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN or "", a.nonce)
        except Exception as e:
            office_send_html(f"⚠️ Не зміг прочитати запит «продати»: {html.escape(str(e))}", None)
            raise SystemExit(1)
        if d.get("mode") == "plan":   # 10.10: план продажу в повідомлення «📒 Записав покупку»
            plan_mode(d, os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN or "")
            return
        if d.get("ka"):   # 24.10: «продати N ka» — картка для Kleinanzeigen
            if not ka_mode(d):
                raise SystemExit(1)
            return
        if d.get("mode"):   # 09.10: підключення eBay / автопублікація
            if not ebay_mode(d, os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN or ""):
                raise SystemExit(1)
            return
        title, cost, row = d.get("title") or "", d.get("cost"), d.get("row")
        photos, ebay, steps_only = d.get("photos") or [], bool(d.get("ebay")), bool(d.get("steps"))
        edits, listed = d.get("edits"), bool(d.get("item_id"))
    else:
        title, cost, row, photos, edits, listed, d = a.title or "", a.cost, None, [], None, False, {}
    live = None
    if listed and d.get("rt"):   # 26.10: картка виставленого — з доставкою, що зараз на eBay
        try:
            import ebay_list as el
            live = el.shipping_of(str(d["item_id"]), el.access_token(d["rt"], config.EBAY_APP_ID, config.EBAY_CERT_ID,
                                                                     __import__("requests").post), __import__("requests").post)
        except Exception as e:
            print(f"доставка з eBay: {e.__class__.__name__}")
    try:
        text, kb, steps = make(title, float(cost) if cost not in (None, "") else None, row, ebay=ebay, edits=edits, listed=listed,
                               ship=live)
    except Exception as e:   # картку-помилку — у бот, щоб не чекати мовчки
        office_send_html(f"⚠️ Не вийшло підготувати оголошення: {html.escape(e.__class__.__name__)} — напиши Claude", None)
        raise
    print("готово" + (" (dry-run)" if a.dry_run else ""))
    want_steps = steps_only or not ebay or not row
    if a.dry_run:
        print(text + "\n\n" + steps)
        return
    if not steps_only and not office_send_html(text, kb if kb and row else None):
        raise SystemExit(1)
    if ebay and row and photos and not steps_only and not (edits or {}).get("raw"):   # 16.10: як фото підуть на eBay
        print(f"фото після обробки: {office_send_studio_preview(photos, row)} з {len(photos)}")
    if steps and want_steps:
        if not office_send_html(steps, getattr(make, "steps_kb", None)):
            raise SystemExit(1)
        if photos:
            print(f"фото альбомом: {office_send_album(photos, f'📸 Фото для оголошення №{row}')} з {len(photos)}")


if __name__ == "__main__":
    main()
