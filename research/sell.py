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
ACCEPT_SHARE, DECLINE_SHARE = 0.93, 0.90        # від ціни оголошення / від «швидкої» ціни
SHIP_CHARGE_RAM = 6.49   # 09.10: доставку платить покупець окремим рядком (DHL Paket), як купує й сам користувач
LOW_SHARE = 0.92                                # нижче 92% «швидкої» ціни не ставимо


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
    d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=client._headers(), params={
        "q": q, "category_ids": cat, "limit": 100, "sort": "price",
        "filter": f"buyingOptions:{{FIXED_PRICE}},itemLocationCountry:DE,conditionIds:{{3000}},"
                  # нижче 80% «швидкої» ціни — здебільшого шахраї, дефекти й інші товари, а не конкуренти
                  f"price:[{int(r['quick_sale'] * 0.8)}..{int(r['median_sale'] * 1.6)}],priceCurrency:EUR"})
    out = []
    for it in d.get("itemSummaries") or []:
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
_SPEC_WORD = re.compile(r"^(?:\d+(?:[.,]\d+)?(?:gb|g|tb|mhz|mt/?s|cl\d*|x\d+)?|x|gb|ddr\d?l?|pc\d-?\d*\w?|ram|kit|set|"
                        r"so-?dimm|sodimm|dimm|udimm|ddr4-\d+|ddr5-\d+|arbeitsspeicher|speicher|memory|laptop|notebook|"
                        r"desktop|pc|module?|riegel|stück|stk|neu|neuwertig|gebraucht|top|zustand|getestet|original|"
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
    lines = [f"Verkauft wird {'ein Kit' if mods > 1 else 'ein Modul'}: {head + ' ' if head else ''}{cap} {gen} {form}.", "",
             f"• Kapazität: {total} GB" + (f" ({mods} × {per} GB)" if mods > 1 else ""),
             f"• Typ: {gen} {'SO-DIMM (Laptop' + (', ' + dev if dev else '') + ')' if lap else 'ECC UDIMM (unbuffered, ungepuffert)' if ecc else 'DIMM (Desktop-PC)'}",
             *(["• ECC ohne Buffer: für Server, NAS und Workstations; läuft in den meisten Desktop-PCs (Intel ohne ECC-Funktion)"]
               if ecc else []),
             *([f"• Geschwindigkeit: {speed} MHz ({pc})"] if speed else []),
             *([f"• Teilenummer: {pn}"] if pn else []),
             "• Zustand: gebraucht, voll funktionsfähig",
             "• Getestet mit MemTest86: 0 Fehler (Screenshot in den Fotos)", "",
             f"Lieferumfang: {mods} {'Module' if mods > 1 else 'Modul'} wie auf den Fotos.",
             f"Versand: als versichertes DHL-Paket mit Sendungsnummer ({money_de(SHIP_CHARGE_RAM)} €), antistatisch verpackt, "
             "in der Regel am nächsten Werktag.", "",
             "Privatverkauf: keine Gewährleistung und keine Rücknahme. Ihre Rechte aus dem eBay-Käuferschutz bleiben davon unberührt."]
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
    lines = [f"Verkauft wird: {head}.", "",
             f"• Zustand: gebraucht, voll funktionsfähig, getestet ({check}keine Fehler)",
             f"• Auf Werkseinstellungen zurückgesetzt und {acct}",
             "• Lieferumfang: wie auf den Fotos" + (" (inkl. Controller)" if ctrl else "") + ", Strom- und HDMI-Kabel", "",
             f"Versand: als versichertes DHL-Paket mit Sendungsnummer ({money_de(r.get('ship_out') or console_alert.SHIP)} €), sicher verpackt.", "",
             "Privatverkauf: keine Gewährleistung und keine Rücknahme. Ihre Rechte aus dem eBay-Käuferschutz bleiben davon unberührt."]
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


def item_prices(pr: dict, ship: float) -> dict:
    """Платна доставка (09.10, як купує сам користувач): ціна товару = ціна «разом» мінус доставка; пороги — теж без неї."""
    return dict(item=max(1, round(pr["list"] - ship)), accept=max(1, round(pr["accept"] - ship)),
                decline=max(1, round(pr["decline"] - ship)))


def build_card(row: int | None, title: str, r: dict, pr: dict, comp: list[float], tx: dict, cost: float | None) -> str:
    """Перше повідомлення — загальне, українською: скільки ставити, скільки лишиться, що на ринку."""
    e = lambda x: html.escape(x, quote=False)   # noqa: E731
    ip = item_prices(pr, tx["ship"])
    lines = [f"🏷 <b>Продаж{' №' + str(row) if row else ''}</b> · {e(r['type'])}", f"<i>{e(title[:90])}</i>", "",
             f"💶 Ціна: <b>{ip['item']} €</b> + доставка {money_de(tx['ship'])} € (разом ≈ {pr['list']} €)"]
    if cost:
        lines.append(f"   купив за {cost:.0f} € → чистими ≈ {pr['net_list']:.0f} €, прибуток ≈ <b>{pr['profit_list']:.0f} €</b>")
    lines += [f"🤝 Пропозиції ціни: приймати від <b>{ip['accept']} €</b>"
              + (f" (прибуток ≈ {pr['profit_accept']:.0f} €)" if cost else "") + f", відхиляти нижче <b>{ip['decline']} €</b>",
              f"📊 Продано за 30 днів: швидко {r['quick_sale']} €, медіана {r['median_sale']} € · {ram_alert._speed_label(r['sell_through'])}",
              (f"🔎 Зараз на eBay схожих: {len(comp)}, найдешевше {comp[0]:.0f} € з доставкою" if comp
               else "🔎 Зараз на eBay схожих не знайшов — ціна за медіаною продажів"),
              *[f"⚠️ {e(w)}" for w in pr["warn"]], "",
              "✅ <b>Перед публікацією:</b>", *[f"• {e(b)}" for b in tx["before"]], "",
              "👇 Далі — покроково, як у формі eBay. Сірі значення натисни — скопіюються."]
    return "\n".join(lines)


def build_steps(row: int | None, pr: dict, tx: dict) -> str:
    """Друге повідомлення — покроково в порядку форми eBay «Angebot fertigstellen»; назви полів німецькою, як на сайті."""
    e = lambda x: html.escape(x, quote=False)   # noqa: E731
    ip = item_prices(pr, tx["ship"])
    code = lambda v: f"<code>{e(str(v))}</code>"   # noqa: E731
    field = lambda k, v: f"   • {e(k)}: {code(v)}"   # noqa: E731
    out = ["📋 <b>Покроково на eBay</b> · «Angebot fertigstellen»", "",
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
            field("Käufer zahlt", money_de(tx["ship"])),
            "   • «Kostenlosen Versand anbieten» — без галочки", "",
            "9️⃣ <b>ANGEBOT BEWERBEN</b> → Basis і Premium вимкнені (перший тиждень)", "",
            "🔟 <b>Angebot einstellen</b> → потім у бот: " + code(f"виставив {row or 'N'} {ip['item']}"), "",
            "📦 <b>Після продажу:</b> Mein eBay → Verkauft → «Versandetikett kaufen» → DHL Paket → запакуй "
            "(антистатичний пакет + коробка з наповнювачем) → у бот: " + code(f"відправив {row or 'N'} трек")]
    return "\n".join(out)


def keyboard(tx: dict, r: dict, row: int | None = None, ebay: bool = False) -> dict:
    q, cat = competitor_query(r)
    auto = [[{"text": "🚀 Виставити на eBay автоматично", "callback_data": f"c|авто|{row}"}]] if row and ebay else []
    return {"inline_keyboard": [
        [{"text": "📋 Скопіювати назву", "copy_text": {"text": tx["title"][:256]}}],
        *([[{"text": "📋 Herstellernummer", "copy_text": {"text": tx["pn"][:256]}}]] if tx.get("pn") else []),
        [{"text": "🔎 Конкуренти на eBay", "url": f"https://www.ebay.de/sch/{cat}/i.html?_nkw={quote_plus(q)}&LH_BIN=1&LH_ItemCondition=3000&_sop=15"}],
        *auto,
        [{"text": "➕ Виставити вручну", "url": "https://www.ebay.de/sl/prelist/suggest"}]]}


def prepare(title: str, cost: float | None, row: int | None = None, comp_fn=competitors) -> dict | None:
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
    return dict(r=r, pr=pr, comp=comp, tx=tx)


def make(title: str, cost: float | None, row: int | None = None, comp_fn=competitors, ebay: bool = False) -> tuple[str, dict | None]:
    p = prepare(title, cost, row, comp_fn)
    if not p:
        return (f"🤔 Не впізнав товар «{html.escape(title[:80])}».\nНапиши повніше, наприклад:\n"
                f"<code>продати {row or 'N'} Kingston Fury 2x16GB DDR4 3200</code>", None, "")
    r, pr, comp, tx = p["r"], p["pr"], p["comp"], p["tx"]
    return build_card(row, title, r, pr, comp, tx, cost), keyboard(tx, r, row, ebay), build_steps(row, pr, tx)


def office_send_html(text: str, markup: dict | None) -> bool:
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
    if mode != "publish":
        return send(f"⚠️ Невідомий режим «{html.escape(str(mode))}» — напиши Claude.", None)

    row, dry = d.get("row"), bool(d.get("dry"))
    what = f"№{row}" if row else "товару"
    if not d.get("rt"):
        return send("🔑 eBay ще не підключено — напиши «ebay вхід».", None)
    p = prepare(d.get("title") or "", float(d["cost"]) if d.get("cost") not in (None, "") else None, row, comp_fn)
    if not p:
        return send(f"🤔 Не впізнав товар {what} — виставляти автоматично не буду. Напиши «продати {row} &lt;назва повніше&gt;».", None)
    tx, pr = p["tx"], p["pr"]
    ip = item_prices(pr, tx["ship"])
    try:
        token = el.access_token(d["rt"], app, cert, post)
        pics = []
        for i, ph in enumerate([x for x in d.get("photos") or [] if isinstance(x, dict) and x.get("id")]):
            if len(pics) >= el.MAX_PHOTOS:
                break
            data = file_fn(ph["id"], key)
            if data and el.is_image(data):
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
    ebay = False
    if a.blob:
        try:
            d = unseal(a.blob, a.mac, os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN or "", a.nonce)
        except Exception as e:
            office_send_html(f"⚠️ Не зміг прочитати запит «продати»: {html.escape(str(e))}", None)
            raise SystemExit(1)
        if d.get("mode"):   # 09.10: підключення eBay / автопублікація
            if not ebay_mode(d, os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN or ""):
                raise SystemExit(1)
            return
        title, cost, row = d.get("title") or "", d.get("cost"), d.get("row")
        photos, ebay = d.get("photos") or [], bool(d.get("ebay"))
    else:
        title, cost, row, photos = a.title or "", a.cost, None, []
    try:
        text, kb, steps = make(title, float(cost) if cost not in (None, "") else None, row, ebay=ebay)
    except Exception as e:   # картку-помилку — у бот, щоб не чекати мовчки
        office_send_html(f"⚠️ Не вийшло підготувати оголошення: {html.escape(e.__class__.__name__)} — напиши Claude", None)
        raise
    print("готово" + (" (dry-run)" if a.dry_run else ""))
    if a.dry_run:
        print(text + "\n\n" + steps)
    elif not office_send_html(text, None) or (steps and not office_send_html(steps, kb)):
        raise SystemExit(1)
    elif photos:
        print(f"фото альбомом: {office_send_album(photos, f'📸 Фото для оголошення №{row}')} з {len(photos)}")


if __name__ == "__main__":
    main()
