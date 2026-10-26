"""Перевірка живих оголошень (26.10, користувач: «бот і так ходить по eBay — нехай сам перевіряє, чи все збігається з
фактичними даними планки, і одразу пише, якщо ні; і KA так само»).

Раз на добу (разом із заміром цін, research/sell.py режим sync) і на «звірити»: кожне наше оголошення eBay і KA —
назва, характеристики eBay, опис, доставка, фото (наклейка / екран MemTest через Gemini) і рядок обліку. Розбіжність —
коротко, українською; що саме не так і де. Причина появи: 26.10 на eBay в №4 стояв чужий номер CMK32GX4M2B3200C16 (кіт 3200)
при 2133, у №5 модель «Corsair Vengeance OVP», у №8 неповний номер, в описах «DHL» при доставці Hermes.
"""
import hashlib
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ram_parse import parse_title  # noqa: E402

SPEEDS = {1600, 1866, 2133, 2400, 2666, 2933, 3000, 3200, 3466, 3600, 3733, 3866, 4000, 4133, 4266, 4400, 4600, 4800,
          5200, 5600, 6000, 6200, 6400, 6600, 6800, 7000, 7200, 7600, 8000, 8200, 8400}
_KINGSTON = {"21": 2133, "24": 2400, "26": 2666, "29": 2933, "32": 3200, "36": 3600, "48": 4800, "52": 5200, "56": 5600,
             "60": 6000, "64": 6400}
_JUNK_MODEL = re.compile(r"\b(?:ovp|neu|neuwertig|original|gebraucht|getestet|top)\b", re.I)
KA_AD = "https://www.kleinanzeigen.de/s-anzeige/{}"
KA_LIST = "https://www.kleinanzeigen.de/s-bestandsliste.html?userId={}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
      "Accept-Language": "de-DE,de;q=0.9"}


# ----------------------------------------------------------------------------- що каже кожне джерело
def snap(mhz: float) -> int | None:
    """Найближча стандартна частота (PC4-17000 → 2125 → 2133)."""
    best = min(SPEEDS, key=lambda s: abs(s - mhz))
    return best if abs(best - mhz) <= 40 else None


def pn_speed(pn: str) -> int | None:
    """Частота, закладена в номер моделі: CMK32GX4M2A2133C13 → 2133, F4-2400C16D-32GFT → 2400, KF432C16BBK2/32 → 3200."""
    p = (pn or "").upper()
    m = re.match(r"^(?:(?:KF|HX|KCP)\d|KVR|KSM)(\d{2})", p)   # KF432 / HX424 / KCP426 — з поколінням; KVR26 / KSM32 — без
    if m and m.group(1) in _KINGSTON:
        return _KINGSTON[m.group(1)]
    for x in re.findall(r"(?<!\d)(\d{4})(?!\d)", p):
        if int(x) in SPEEDS:
            return int(x)
    return None


def text_speeds(text: str) -> set[int]:
    """«DDR4-2133», «2400 MHz», «3200MT/s», «PC4-17000» → частоти."""
    t = text or ""
    out = {int(x) for x in re.findall(r"(?i)ddr\d-(\d{4})(?!\d)", t) if int(x) in SPEEDS}
    out |= {int(x) for x in re.findall(r"(?i)(?<!\d)(\d{4})\s*(?:mhz|mt/?s)", t) if int(x) in SPEEDS}
    out |= {s for s in (snap(int(x) / 8) for x in re.findall(r"(?i)pc\d-(\d{5})", t)) if s}
    return out


def norm_pn(pn: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (pn or "").upper())


def title_pn(title: str) -> str | None:
    from sell import _part_number
    return _part_number(title or "")


def desc_field(desc: str, label: str) -> str:
    m = re.search(rf"(?im)^\W*{label}\s*:\s*(.+)$", desc or "")
    return m.group(1).strip() if m else ""


def series(text: str) -> str:
    """«Corsair Vengeance LPX» — бренд і лінійка з назви або першого речення опису."""
    from sell import _brand_series
    p, _ = parse_title(text)
    brand = (p or {}).get("brand") or ""
    return _brand_series(text, "" if brand in (None, "other") else brand)


# ----------------------------------------------------------------------------- перевірка
def check(item: dict, row_title: str | None = None, facts: dict | None = None) -> list[str]:
    """item: where (eBay / KA), title, desc, specs {назва: [значення]}, ship_free, ship_service. row_title — назва з обліку
    (як купили), facts — прочитане з фото (Gemini). → розбіжності, коротко українською."""
    out = []
    title, desc, specs = item.get("title") or "", item.get("desc") or "", item.get("specs") or {}
    sp = lambda k: [v for v in specs.get(k) or [] if v]   # noqa: E731
    p, _ = parse_title(title)
    p = p or {}

    # номери моделі: Herstellernummer (кілька — уже помилка), у назві, в описі
    pns = sp("Herstellernummer")
    if len(pns) > 1:
        out.append(f"у Herstellernummer {len(pns)} номери ({', '.join(pns)}) — має бути один")
    tpn, dpn = title_pn(title), desc_field(desc, "Teilenummer")
    named = [("назва", tpn), ("Herstellernummer", pns[0] if pns else ""), ("опис", dpn)]
    known = [(k, v) for k, v in named if v]
    if len({norm_pn(v) for _, v in known}) > 1:
        out.append("номер моделі різний: " + ", ".join(f"{k} {v}" for k, v in known))

    # частота: назва, Busgeschwindigkeit, опис, кожен номер моделі, фото
    src = {}
    if p.get("speed"):
        src["назва"] = p["speed"]
    for v in sp("Busgeschwindigkeit"):
        for s in text_speeds(v if re.search(r"(?i)mhz|pc\d|ddr", v) else v + " MHz"):
            src["Busgeschwindigkeit"] = s
    for s in text_speeds(desc_field(desc, "Geschwindigkeit")):
        src["опис"] = s
    for v in set(pns + [tpn or "", dpn]):
        if v and pn_speed(v):
            src[f"номер {v}"] = pn_speed(v)
    if facts and facts.get("speed"):
        src["фото (номер на планці)"] = facts["speed"]
    if len(set(src.values())) > 1:
        out.append("частота не збігається: " + ", ".join(f"{k} — {v}" for k, v in src.items()))

    # обсяг і кількість планок
    cap = {}
    if p.get("total"):
        cap["назва"] = (p["total"], p.get("modules"))
    tot, per, mods = sp("Gesamtkapazität"), sp("Kapazität pro Modul"), sp("Anzahl der Module")
    num = lambda v: int(re.search(r"\d+", v).group()) if re.search(r"\d+", v or "") else None   # noqa: E731
    if tot or mods:
        t_, m_ = num(tot[0]) if tot else None, num(mods[0]) if mods else None
        if not t_ and per and m_:
            t_ = num(per[0]) * m_
        cap["характеристики"] = (t_, m_)
    m = re.search(r"(\d+)\s*GB(?:\s*\((\d+)\s*[×x]\s*\d+\s*GB\))?", desc_field(desc, "Kapazität"))
    if m:
        cap["опис"] = (int(m.group(1)), int(m.group(2)) if m.group(2) else 1)
    totals = {v[0] for v in cap.values() if v[0]}
    modset = {v[1] for v in cap.values() if v[1]}
    if len(totals) > 1 or len(modset) > 1:
        out.append("обсяг не збігається: " + ", ".join(f"{k} — {v[0]} ГБ" + (f" ({v[1]} план.)" if v[1] else "")
                                                       for k, v in cap.items()))
    if facts and facts.get("module_gb") and p.get("total") and p.get("modules") and \
            facts["module_gb"] != p["total"] // p["modules"]:
        out.append(f"на фото планки по {facts['module_gb']} ГБ, а в назві {p['modules']}×{p['total'] // p['modules']} ГБ")

    # покоління і тип (ноутбучна / десктопна)
    gens = {k: g for k, g in [("назва", p.get("gen")), ("характеристики", (re.search(r"(?i)ddr\d", " ".join(sp("Produktart")))
                                                                           or [None])[0]),
                                ("опис", (re.search(r"(?i)ddr\d", desc_field(desc, "Typ")) or [None])[0]),
                                ("фото", facts.get("gen") if facts else None)] if g}
    if len({g.lower() for g in gens.values()}) > 1:
        out.append("покоління не збігається: " + ", ".join(f"{k} — {v.upper()}" for k, v in gens.items()))
    forms = {}
    if p.get("form"):
        forms["назва"] = p["form"]
    for v in sp("Formfaktor"):
        forms["характеристики"] = "sodimm" if re.search(r"(?i)so-?dimm", v) else "udimm"
    typ = desc_field(desc, "Typ")
    if typ:
        forms["опис"] = "sodimm" if re.search(r"(?i)so-?dimm|laptop|notebook", typ) else "udimm"
    if facts and facts.get("form") in ("sodimm", "udimm"):
        forms["фото"] = facts["form"]
    if len(set(forms.values())) > 1:
        out.append("тип планки не збігається: " + ", ".join(f"{k} — {'SO-DIMM' if v == 'sodimm' else 'DIMM'}" for k, v in forms.items()))

    # модель: сміття в полі Modell; лінійка в описі ≠ лінійка в назві («Vengeance OVP» проти «Vengeance LPX»)
    for v in sp("Modell"):
        if _JUNK_MODEL.search(v):
            out.append(f"у полі Modell «{v}» — «{_JUNK_MODEL.search(v).group()}» не модель")
    # перше речення шаблону: «Biete hier ein Kit Corsair Vengeance LPX 32GB … an.» — лінійка з назви має бути в ньому
    m = re.search(r"(?i)biete hier\s+(?:(?:ein|eine|einen)\s+)?(?:(?:kit|modul|set)\s+)?(.+?)\s+\d+\s*GB", desc)
    st = series(title) if title else ""
    if m and st:
        phrase = m.group(1).strip()
        missing = [w for w in st.split() if w.lower() not in phrase.lower()]
        if missing:
            out.append(f"в описі «{phrase}», а в назві «{st}»")

    # доставка (eBay): опис не має суперечити тому, що стоїть в оголошенні
    if item.get("where") == "eBay" and item.get("ship_service"):
        if re.search(r"\bDHL\b", desc) and "DHL" not in item["ship_service"].upper():
            out.append(f"в описі DHL, а доставка в оголошенні — {item['ship_service'].replace('DE_', '')}")
        if re.search(r"(?i)kostenlos(?:er)? versand", desc) and item.get("ship_free") is False:
            out.append("в описі «Kostenloser Versand», а доставка платна")

    # облік: як купили (назва з таблиці) — ті самі обсяг, покоління, тип
    if row_title:
        r, _ = parse_title(row_title)
        if r:
            diff = [f"{lbl}: облік — {a}, оголошення — {b}" for lbl, a, b in [
                ("обсяг", f"{r['total']} ГБ", f"{p.get('total')} ГБ"), ("покоління", r["gen"].upper(), str(p.get("gen") or "").upper()),
                ("тип", r["form"], p.get("form"))] if p.get("total") and a != b]
            if r.get("speed") and p.get("speed") and r["speed"] != p["speed"]:
                diff.append(f"частота: облік — {r['speed']}, оголошення — {p['speed']}")
            out += diff
    return out


def digest(issues: list[str]) -> str:
    return hashlib.sha1("\n".join(sorted(issues)).encode()).hexdigest()[:12]


# ----------------------------------------------------------------------------- eBay
def ebay_item(item_id: str, token: str, post) -> dict:
    """GetItem → назва, опис (текстом), характеристики, доставка, фото, SKU."""
    import ebay_list as el
    root = el.trading("GetItem", f"<ItemID>{el._x(item_id)}</ItemID><DetailLevel>ReturnAll</DetailLevel>"
                                 "<IncludeItemSpecifics>true</IncludeItemSpecifics>", token, post)
    ns, it = el.NS, root.find(el.NS + "Item")
    t = lambda tag: (it.findtext(ns + tag) or "").strip() if it is not None else ""   # noqa: E731
    specs = {}
    for nv in (it.iter(ns + "NameValueList") if it is not None else []):
        name = (nv.findtext(ns + "Name") or "").strip()
        specs.setdefault(name, []).extend(v.text.strip() for v in nv.findall(ns + "Value") if v.text and v.text.strip())
    opt = it.find(f"{ns}ShippingDetails/{ns}ShippingServiceOptions") if it is not None else None
    raw = t("Description")
    desc = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", raw)))
    return dict(where="eBay", id=item_id, url=f"https://www.ebay.de/itm/{item_id}", title=t("Title"), desc=desc, specs=specs,
                sku=t("SKU"), ship_free=(opt.findtext(ns + "FreeShipping") or "").lower() == "true" if opt is not None else None,
                ship_service=(opt.findtext(ns + "ShippingService") or "") if opt is not None else "",
                pics=[u.text for u in it.iter(ns + "PictureURL")] if it is not None else [])


# ----------------------------------------------------------------------------- Kleinanzeigen
def ka_page(ad_id: str, get) -> str | None:
    try:
        r = get(KA_AD.format(ad_id), headers=UA, timeout=15)
        return r.text if r.status_code == 200 and "viewad-title" in r.text else None
    except Exception:
        return None


def ka_item(ad_id: str, page: str) -> dict:
    from photo_check import ka_images
    g = lambda pat: html.unescape(re.sub(r"<[^>]+>", "", (re.search(pat, page, re.S) or [None, ""])[1])).strip()   # noqa: E731
    desc = re.sub(r"(?i)<br\s*/?>", "\n", (re.search(r'id="viewad-description-text"[^>]*>(.*?)</p>', page, re.S) or [None, ""])[1])
    return dict(where="KA", id=ad_id, url=KA_AD.format(ad_id), title=g(r'id="viewad-title"[^>]*>(.*?)</h1>'),
                desc=html.unescape(re.sub(r"<[^>]+>", "", desc)).strip(), specs={}, pics=ka_images(page),
                price=g(r'id="viewad-price"[^>]*>(.*?)</h2>'))


def ka_user(page: str) -> str | None:
    m = re.search(r"s-bestandsliste\.html\?userId=(\d+)", page or "")
    return m.group(1) if m else None


def ka_ads(user: str, get) -> list[tuple[str, str]]:
    """Усі оголошення продавця (публічна сторінка): [(id, назва)]."""
    try:
        r = get(KA_LIST.format(user), headers=UA, timeout=15)
    except Exception:
        return []
    out = []
    for m in re.finditer(r'data-adid="(\d+)"(.*?)(?=data-adid="|\Z)', r.text if r.status_code == 200 else "", re.S):
        t = re.search(r'<a[^>]*class="ellipsis"[^>]*>(.*?)</a>|<h2[^>]*>(.*?)</h2>', m.group(2), re.S)
        name = html.unescape(re.sub(r"<[^>]+>", "", (t.group(1) or t.group(2)) if t else "")).strip()
        if name:
            out.append((m.group(1), name))
    return list(dict.fromkeys(out))


def match_row(title: str, rows: list[dict]) -> dict | None:
    """Оголошення KA без запису в обліку → рядок з тим самим обсягом, поколінням, типом і частотою (лише якщо один)."""
    p, _ = parse_title(title)
    if not p:
        return None
    hit = []
    for r in rows:
        q, _ = parse_title(r.get("title") or "")
        if q and all(q.get(k) == p.get(k) for k in ("gen", "form", "total", "modules")) and \
                (not q.get("speed") or not p.get("speed") or q["speed"] == p["speed"]):
            hit.append(r)
    return hit[0] if len(hit) == 1 else None


# ----------------------------------------------------------------------------- фото (Gemini)
FACTS_PROMPT = """These photos are from our own listing of computer RAM. Read ONLY what is printed on the module stickers
or shown on a MemTest86 screen ("RAM Config: DDR4 2400MT/s / ... / <vendor> <part number>"). Do not guess.
Return JSON: {"part_number": the module or kit part number as printed (e.g. "CMK32GX4M2A2400C16", "F4-2400C16-16GFT"),
or "", "module_gb": capacity of ONE module in GB as printed (e.g. 16) or 0, "gen": "ddr4" | "ddr5" | "", "form":
"sodimm" | "udimm" | "" (SO-DIMM is short laptop RAM), "confidence": "high" | "medium" | "low"}"""


def photo_facts(pics: list[str], cache: dict, ask=None, download=None) -> dict | None:
    """Номер, обсяг планки, покоління і тип — з наклейки / екрана MemTest. Один запит на набір фото (кеш)."""
    if not pics:
        return None
    key = hashlib.sha1("|".join(pics).encode()).hexdigest()[:16]
    if key in cache:
        return cache[key] or None
    from photo_check import _download, ask_gemini
    ask, download = ask or ask_gemini, download or _download
    imgs = [b for b in (download(re.sub(r"/s-l\d+\.", "/s-l1600.", u)) for u in pics[:4]) if b]
    res = ask(imgs, FACTS_PROMPT) if imgs else None
    facts = None
    if isinstance(res, dict) and res.get("confidence") == "high":
        facts = {"pn": str(res.get("part_number") or ""), "module_gb": int(res.get("module_gb") or 0) or None,
                 "gen": str(res.get("gen") or "").lower() or None, "form": res.get("form") or None}
        facts["speed"] = pn_speed(facts["pn"])
    if res is not None:   # відповідь була (навіть невпевнена) — не питаємо знову
        cache[key] = facts or {}
    return facts


# ----------------------------------------------------------------------------- разом
def run(lots: list[dict], known: list[dict], rows: list[dict], token: str, post, get=None, cache: dict | None = None,
        ask=None, download=None) -> dict:
    """lots — активні лоти eBay; known — [{n, id}] з listings.sync (лот → рядок); rows — рядки обліку (з ka).
    → {audit: [{n, where, id, url, title, issues, key}], ka_links: [{n, id}]}."""
    if get is None:
        import requests
        get = requests.get
    cache = cache if cache is not None else {}
    by_id = {str(x["id"]): x["n"] for x in known if x.get("id")}
    rows_n = {int(r["n"]): r for r in rows}
    report, links = [], []

    def add(n, item):
        r = rows_n.get(int(n)) if n else None
        facts = photo_facts(item.get("pics") or [], cache, ask, download)
        issues = check(item, r.get("title") if r else None, facts)
        if issues:
            report.append(dict(n=n, where=item["where"], id=item["id"], url=item["url"], title=item["title"][:60],
                               issues=issues, key=digest(issues)))

    for lot in lots:
        try:
            add(by_id.get(str(lot["id"])), ebay_item(str(lot["id"]), token, post))
        except Exception as e:
            print(f"перевірка eBay {lot.get('id')}: {e.__class__.__name__}")
    # KA: відомі оголошення з обліку, далі — решта оголошень продавця (зі сторінки продавця)
    ka_rows = [r for r in rows if r.get("ka")]
    user, seen = None, set()
    for r in ka_rows:
        page = ka_page(str(r["ka"]), get)
        if not page:
            continue
        user = user or ka_user(page)
        seen.add(str(r["ka"]))
        add(r["n"], ka_item(str(r["ka"]), page))
    if user:
        free = [r for r in rows if not r.get("ka") and r.get("status") in ("Перевірено", "Виставлено")]
        for ad_id, name in ka_ads(user, get):
            if ad_id in seen:
                continue
            r = match_row(name, free)
            if not r:
                continue
            links.append({"n": r["n"], "id": ad_id})
            free = [x for x in free if x is not r]
            page = ka_page(ad_id, get)
            if page:
                add(r["n"], ka_item(ad_id, page))
    print(f"перевірка оголошень: eBay {len(lots)}, KA {len(seen) + len(links)}, з розбіжностями {len(report)}")
    return {"audit": report, "ka_links": links}


def load_cache() -> dict:
    path = os.path.join(os.getenv("PHOTO_CACHE") or "photo_cache", "audit_facts.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict) -> None:
    d = os.getenv("PHOTO_CACHE") or "photo_cache"
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "audit_facts.json"), "w", encoding="utf-8") as f:
            json.dump(dict(list(cache.items())[-300:]), f)
    except OSError:
        pass
