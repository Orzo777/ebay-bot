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
def plan_price(r: dict, cost: float | None, comp: list[float]) -> dict:
    """Ціна «Sofort-Kaufen» (з безкоштовною доставкою) і пороги Preisvorschlag.
    Ціль — медіана реальних продажів (08.10, рішення користувача: раніше підлаштовувались під трьох найдешевших
    конкурентів, і ціна сповзала до «швидкої»); не нижче мінімуму, з яким продаж ще дає ≥5 € / 10% прибутку.
    Дешевші конкуренти — лише попередження. Автоприйняти — від 93% ціни, автовідхилити — нижче 87% ціни чи 90%
    «швидкої» (що менше), але ніколи нижче мінімуму."""
    quick, med = r["quick_sale"], r["median_sale"]
    warn = []
    target = med
    floor = price_for_net(r, cost + max(MIN_PROFIT_ABS, MIN_PROFIT_PCT * cost)) if cost else 0.0
    list_price = max(nice(target), math.ceil(floor))
    if comp and list_price >= comp[min(2, len(comp) - 1)]:
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
    m = re.search(r"\b(?=[A-Z0-9/-]*\d)(?=[A-Z0-9/-]*[A-Z])[A-Z0-9][A-Z0-9/-]{7,}\b", title)
    return m.group(0) if m and not re.match(r"^(?:DDR|PC)\d", m.group(0)) else None


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
    parts = [head, cap, gen + (f"-{speed}" if speed else ""), form]
    t = re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()
    if dev and len(t) + len(dev) + 5 <= 80:
        t += f" für {dev}"
    while len(t) > 80 and " " in t:
        t = t.rsplit(" ", 1)[0]
    for extra in (" – getestet", " getestet"):
        if len(t + extra) <= 80:
            t += extra
            break
    pn = _part_number(title)
    lines = [f"Verkauft wird {'ein Kit' if mods > 1 else 'ein Modul'}: {head + ' ' if head else ''}{cap} {gen} {form}.", "",
             f"• Kapazität: {total} GB" + (f" ({mods} × {per} GB)" if mods > 1 else ""),
             f"• Typ: {gen} {'SO-DIMM (Laptop' + (', ' + dev if dev else '') + ')' if lap else 'ECC UDIMM (unbuffered, ungepuffert)' if ecc else 'DIMM (Desktop-PC)'}",
             *(["• ECC ohne Buffer: für Server, NAS und Workstations; läuft in den meisten Desktop-PCs (Intel ohne ECC-Funktion)"]
               if ecc else []),
             *([f"• Geschwindigkeit: {speed} MHz ({pc})"] if speed else []),
             *([f"• Teilenummer: {pn}"] if pn else []),
             "• Zustand: gebraucht, voll funktionsfähig, getestet", "",
             f"Lieferumfang: {mods} {'Module' if mods > 1 else 'Modul'} wie auf den Fotos.",
             "Versand: kostenlos als versichertes DHL-Paket, antistatisch verpackt, in der Regel am nächsten Werktag.", "",
             "Privatverkauf: keine Gewährleistung und keine Rücknahme. Ihre Rechte aus dem eBay-Käuferschutz bleiben davon unberührt."]
    specs = [("Marke", head.split()[0] if head else "Markenlos"), ("Produktlinie", " ".join(head.split()[1:]) or "—"),
             ("Gesamtkapazität", f"{total} GB"), ("Kapazität pro Modul", f"{per} GB"), ("Anzahl der Module", str(mods)),
             ("Speichertyp", gen), ("Formfaktor", "SO-DIMM" if lap else "DIMM"),
             ("Bus-Geschwindigkeit", f"{speed} MHz" if speed else "—"), ("Zustand", "Gebraucht")]
    before = ["зроби фото наклейки з номером (покупці перевіряють партномер)",
              "опис каже «getestet» — переконайся, що планки пройшли тест (MemTest / запуск ПК)"]
    return dict(title=t[:80], desc="\n".join(lines), specs=specs, category="Computer & Zubehör › Speicher (RAM)", before=before)


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
             "Versand: kostenlos als versichertes DHL-Paket, sicher verpackt.", "",
             "Privatverkauf: keine Gewährleistung und keine Rücknahme. Ihre Rechte aus dem eBay-Käuferschutz bleiben davon unberührt."]
    specs = [("Marke", name.split()[0]), ("Modell", " ".join(name.split()[1:])), ("Zustand", "Gebraucht"),
             ("Region", "PAL"), ("Farbe", "—")]
    before = ["скинь консоль до заводських налаштувань і вийди з акаунту (" + acct.replace("vom ", "").replace(" abgemeldet", "") + ")",
              "сфотографуй консоль увімкненою (екран налаштувань) — покупці довіряють більше",
              "перевір, що в коробці все, що на фото й в описі (кабелі, контролер)"]
    return dict(title=tt[:80], desc="\n".join(lines), specs=specs, category="Konsolen & Videospiele › Konsolen", before=before)


# ----------------------------------------------------------------------------- картка
def build_card(row: int | None, title: str, r: dict, pr: dict, comp: list[float], tx: dict, cost: float | None) -> str:
    e = html.escape
    lines = [f"🏷 <b>Продаж{' №' + str(row) if row else ''}</b> · {e(r['type'])}", f"<i>{e(title[:90])}</i>", "",
             f"💶 Ціна: <b>{pr['list']} €</b> «Sofort-Kaufen», доставка безкоштовна"]
    if cost:
        lines.append(f"   купив за {cost:.0f} € → чистими ≈ {pr['net_list']:.0f} €, прибуток ≈ <b>{pr['profit_list']:.0f} €</b>")
    lines += [f"🤝 Preisvorschlag: автоматично приймати від <b>{pr['accept']} €</b>"
              + (f" (прибуток ≈ {pr['profit_accept']:.0f} €)" if cost else "") + f", відхиляти нижче <b>{pr['decline']} €</b>",
              f"📊 Продано за 30 днів: швидко {r['quick_sale']} €, медіана {r['median_sale']} € · {ram_alert._speed_label(r['sell_through'])}",
              (f"🔎 Зараз на eBay схожих: {len(comp)}, найдешевше {comp[0]:.0f} € з доставкою" if comp
               else "🔎 Зараз на eBay схожих не знайшов — ціна за медіаною продажів"),
              *[f"⚠️ {e(w)}" for w in pr["warn"]], "",
              "📝 <b>Назва</b> (натисни — скопіюється):", f"<code>{e(tx['title'])}</code>", "",
              "📄 <b>Опис</b>:", f"<code>{e(tx['desc'])}</code>", "",
              f"📂 Категорія: {e(tx['category'])}",
              "🏷 Artikelmerkmale: " + "; ".join(f"{e(k)}: {e(v)}" for k, v in tx["specs"] if v != "—"), "",
              "✅ <b>Перед публікацією:</b>", *[f"• {e(b)}" for b in tx["before"]],
              "• Preisvorschlag: увімкни «Preisvorschläge» і впиши обидва пороги — eBay сам прийме чи відхилить",
              "• Пересилка: «Kostenloser Versand», DHL Paket; відправ протягом 1–2 днів (рейтинг продавця)"]
    return "\n".join(lines)


def keyboard(tx: dict, r: dict) -> dict:
    q, cat = competitor_query(r)
    return {"inline_keyboard": [
        [{"text": "📋 Скопіювати назву", "copy_text": {"text": tx["title"][:256]}}],
        [{"text": "🔎 Конкуренти на eBay", "url": f"https://www.ebay.de/sch/{cat}/i.html?_nkw={quote_plus(q)}&LH_BIN=1&LH_ItemCondition=3000&_sop=15"}],
        [{"text": "➕ Виставити на eBay", "url": "https://www.ebay.de/sl/prelist/suggest"}]]}


def make(title: str, cost: float | None, row: int | None = None, comp_fn=competitors) -> tuple[str, dict | None]:
    r = identify(title)
    if not r:
        return (f"🤔 Не впізнав товар «{html.escape(title[:80])}».\nНапиши повніше, наприклад:\n"
                f"<code>продати {row or 'N'} Kingston Fury 2x16GB DDR4 3200</code>", None)
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
    return build_card(row, title, r, pr, comp, tx, cost), keyboard(tx, r)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blob")
    ap.add_argument("--mac")
    ap.add_argument("--nonce")
    ap.add_argument("--title")
    ap.add_argument("--cost", type=float)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.blob:
        try:
            d = unseal(a.blob, a.mac, os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN or "", a.nonce)
        except Exception as e:
            office_send_html(f"⚠️ Не зміг прочитати запит «продати»: {html.escape(str(e))}", None)
            raise SystemExit(1)
        title, cost, row = d.get("title") or "", d.get("cost"), d.get("row")
        photos = d.get("photos") or []
    else:
        title, cost, row, photos = a.title or "", a.cost, None, []
    try:
        text, kb = make(title, float(cost) if cost not in (None, "") else None, row)
    except Exception as e:   # картку-помилку — у бот, щоб не чекати мовчки
        office_send_html(f"⚠️ Не вийшло підготувати оголошення: {html.escape(e.__class__.__name__)} — напиши Claude", None)
        raise
    print("готово" + (" (dry-run)" if a.dry_run else ""))
    if a.dry_run:
        print(text)
    elif not office_send_html(text, kb):
        raise SystemExit(1)
    elif photos:
        print(f"фото альбомом: {office_send_album(photos, f'📸 Фото для оголошення №{row}')} з {len(photos)}")


if __name__ == "__main__":
    main()
