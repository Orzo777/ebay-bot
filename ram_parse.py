"""Розбір назви лота оперативної пам'яті: покоління, форм-фактор, ємність, кіт, швидкість, бренд.

Принцип: краще ВІДКИНУТИ неоднозначний лот (None + причина), ніж записати його не в ту клітинку.
Клітинка = покоління × форм-фактор (+ECC) × загальна ємність × кіт/планка.
"""
from __future__ import annotations

import re

SANE_GB = (1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024)
SPEEDS = {
    "ddr3": (800, 1066, 1333, 1600, 1866, 2133),
    "ddr4": (1600, 2133, 2400, 2666, 2933, 3000, 3200, 3466, 3600, 3733, 4000, 4133, 4266, 4400, 4600, 4800),
    "ddr5": (3200, 4000, 4400, 4800, 5200, 5600, 6000, 6400, 6800, 7000, 7200, 7600, 8000, 8200, 8400, 8800),
    "ddr2": (533, 667, 800, 1066),
}

# --- відсів шуму (не модуль пам'яті, лот-набір, дефект) ---
# Завжди не модуль пам'яті (або пам'ять разом з дорожчою залізякою).
JUNK = re.compile(
    r"\b(rgb[- ]?(strip|kit|licht)|halter|blende|leer|dummy|attrappe|festplatte|ssd|hdd|nvme|mainboard|motherboard|"
    r"grafikkarte|gpu|rtx|gtx|radeon|smartphone|handy|komplett|barebone|gehaeuse|netzteil|tester|testkarte|"
    r"box only|sticker)\b|\bnur\s+(?:die\s+)?(?:ovp|verpackung|karton)|\bleere?\s+(?:ovp|verpackung|karton)", re.I)
# Не модуль, лише якщо стоїть ДО першого «DDR»: «Gaming PC 7800X3D 64GB DDR5» — ПК, а «32 GB DDR4 … – Gaming PC»,
# «DDR5 … für Ryzen», «… + UDIMM Adapter» — сама пам'ять (28.09: такі оголошення бот відкидав).
JUNK_BEFORE_DDR = re.compile(
    r"\b(k[uü]e?hler|heatsink|heat ?spreader|cooler|kabel|adapter|cpu|prozessor|core (?:i\d|ultra)|ryzen|xeon e?\d|"
    r"windows|win ?1[01]|display|bildschirm|tablet|gaming[- ]?pc|pc[- ]?bundle|aufr[uü]e?st)\b", re.I)
DEFECT = re.compile(r"\b(defekt|bastler|ersatzteil|kaputt|nicht funktionsf|for parts|not working|funktioniert nicht|teildefekt)\b", re.I)
# «2 Stk», «2 Stück» — це кіт, а не лот (28.09); лот — від 5 штук або слова «Konvolut», «Posten»…
LOTS = re.compile(r"\b(lot|konvolut|posten|sammlung|gemischt|mixed|verschiedene|diverse|paket|bundle)\b|\b\d{2,3}\s?x\b|"
                  r"\b(?:[5-9]|\d{2,})\s?(?:stk|stueck|st[uü]ck)\b", re.I)

SERVER = re.compile(r"\b(rdimm|lrdimm|r-dimm|registered|reg\.?\s?ecc|ecc\s?reg\.?|fb-?dimm|proliant|poweredge|supermicro|xeon|server)\b", re.I)
SODIMM = re.compile(r"so-?\s?dimm|\b(laptop|notebook|imac|macbook|mac ?mini|thinkpad|elitebook|latitude|probook|nuc|mini[- ]?pc)\b", re.I)

# (канонічна назва, regex) — порядок = пріоритет; бренди модулів раніше за виробників чипів
BRANDS = [
    ("Corsair", r"corsair|vengeance|dominator"), ("G.Skill", r"g\.?\s?skill|trident z|ripjaws|flare x|aegis"),
    ("Kingston", r"kingston|fury (beast|renegade|impact)|value ?ram|kvr"), ("HyperX", r"hyperx"),
    ("Crucial", r"crucial|ballistix"), ("TeamGroup", r"team ?group|t-?force|teamgroup|elite plus"),
    ("Patriot", r"patriot|viper"), ("ADATA/XPG", r"a-?data|xpg|lancer"), ("Lexar", r"lexar"), ("PNY", r"\bpny\b"),
    ("Transcend", r"transcend"), ("Apacer", r"apacer"), ("Mushkin", r"mushkin"), ("Netac", r"netac"),
    ("Fanxiang", r"fanxiang"), ("Silicon Power", r"silicon[- ]?power"), ("Goodram", r"goodram|wilk"),
    ("Kingmax", r"kingmax"), ("Gigabyte/Aorus", r"gigabyte|aorus"), ("MSI", r"\bmsi\b"), ("Zadak", r"zadak"),
    ("Klevv", r"klevv"), ("OLOy", r"oloy"), ("V-Color", r"v-?color"), ("Biwin", r"biwin"), ("Acer/Predator", r"predator|\bacer\b"),
    ("Asgard", r"asgard"), ("Colorful", r"colorful"), ("Galax/KFA2", r"galax|kfa2"), ("Thermaltake", r"thermaltake|toughram"),
    ("Hikvision", r"hikvision|hiksemi"), ("Gloway", r"gloway"), ("Ramaxel", r"ramaxel"), ("Longsys/Lexar", r"longsys"),
    ("Geil", r"\bgeil\b"), ("OCZ", r"\bocz\b"), ("Verbatim", r"verbatim"), ("Intenso", r"intenso"), ("Timetec", r"timetec"),
    ("Kllisre", r"kllisre"), ("Vaseky", r"vaseky"), ("Dell", r"\bdell\b"), ("HP", r"\bhp\b|hewlett"), ("Lenovo", r"lenovo"),
    ("Apple", r"\bapple\b"), ("Supermicro", r"supermicro"), ("ASUS", r"\basus\b"), ("Elixir", r"elixir"), ("Medion", r"medion"),
    ("QNAP/Synology", r"qnap|synology"), ("Xum", r"\bxum\b"),
    ("Samsung", r"samsung|\bm3[78]\d[a-z0-9]{6,}"), ("SK Hynix", r"hynix|\bhm[a-z]{1,2}\d[a-z0-9]{6,}"),
    ("Micron", r"micron|\bmt[a-z0-9]{2,3}[a-z]{2,4}\d"), ("Nanya", r"nanya"), ("Elpida", r"elpida"), ("Qimonda", r"qimonda"),
    ("Infineon", r"infineon"), ("SMART", r"smart modular|\bsmart\b"),
]
_BRANDS = [(n, re.compile(rx, re.I)) for n, rx in BRANDS]
CHIP_OEM = {"Samsung", "SK Hynix", "Micron", "Nanya", "Elpida", "Qimonda", "Infineon", "Dell", "HP", "Lenovo", "Apple", "Supermicro", "SMART"}

_KIT1 = re.compile(r"(?<![\d.])(\d)\s*x\s*(\d{1,3})\s?-?\s?(?:gb|g\b)", re.I)     # 2x16GB, 2 x 16 GB, 3x  32GB
_KIT2 = re.compile(r"(?<![\d.])(\d{1,3})\s?-?\s?gb\s?x\s?(\d)\b", re.I)          # 16GB x2
_KIT3 = re.compile(r"\((\d)\s?x\s?(\d{1,3})\)", re.I)                              # (2x16)
_SINGLE = re.compile(r"(?<![\d.x])(\d{1,3})\s?-?\s?gb\b(?!\s?/\s?s)", re.I)  # «64-GB-Kit»; «GB/s» — швидкість
# «2x Corsair … 16GB … insg. 32GB», «2 Stk 16GB», «2er-Set»: кількість окремо від ємності
_COUNT = re.compile(r"(?:^|[\s(])([2-4])\s?(?:x|stk\.?|stueck|st[uü]ck|er[- ]?(?:set|kit|pack))(?=[\s)]|$)", re.I)
_CAP_COUNT = re.compile(r"(\d{1,3})\s?gb\s*\(?\s*([2-4])\s?(?:stk\.?|stueck|st[uü]ck)(?=[\s).,]|$)", re.I)
# «nicht 32GB 64GB» — SEO-хвіст у назві (28.09: «48GB DDR5 … nicht 32GB 64GB» не розпізнавався)
_SEO_NOT = re.compile(r"\bnicht\s+(?:\d{1,3}\s?gb[\s,/+&]*(?:und|oder)?\s*)+", re.I)
_DDR = re.compile(r"ddr\s?-?\s?([2345])l?\b|\bpc([2345])l?\s?-", re.I)


def snap(gen: str, v: float):
    opts = SPEEDS.get(gen, ())
    if not opts:
        return None
    best = min(opts, key=lambda o: abs(o - v))
    return best if abs(best - v) <= 0.06 * best else None


def parse_speed(t: str, gen: str):
    m = re.search(r"pc([2345])l?-?\s?(\d{4,6})", t)
    if m:
        n = int(m.group(2))
        v = n / 8 if n >= 8000 else n                      # PC4-25600 (МБ/с) → 3200; PC4-2666V → 2666
        s = snap(gen, v)
        if s:
            return s
    m = re.search(r"ddr\s?[2345]\s?-?\s?(\d{3,4})\b", t)
    if m:
        s = snap(gen, int(m.group(1)))
        if s:
            return s
    m = re.search(r"(?<!\d)(\d{4})\s?(?:mhz|mt/s|mts)", t)
    if m:
        return snap(gen, int(m.group(1)))
    return None


def parse_capacity(t: str):
    """→ (total_gb, modules, None) або (None, None, причина)."""
    kits = [(int(a), int(b)) for a, b in _KIT1.findall(t)] + [(int(b), int(a)) for a, b in _KIT2.findall(t)]         + [(int(a), int(b)) for a, b in _KIT3.findall(t)]
    kit_set = {k for k in kits if 2 <= k[0] <= 8}
    if len(kit_set) > 1:
        return None, None, "кілька різних кітів"
    singles = {int(x) for x in _SINGLE.findall(t)}
    explicit_one = {m for n, m in kits if n == 1}     # «(1x64GB)» — саме одна планка, не кіт
    if not kit_set and len(explicit_one) == 1 and singles <= explicit_one:
        v = next(iter(explicit_one))
        return (v, 1, None) if v in SANE_GB else (None, None, f"нереальна ємність {v}")
    ca = _CAP_COUNT.search(t)          # «32GB (2 Stk)» — 32 разом у двох планках
    if not kit_set and ca and int(ca.group(1)) % int(ca.group(2)) == 0:
        kit_set = {(int(ca.group(2)), int(ca.group(1)) // int(ca.group(2)))}
    cm = _COUNT.search(t)
    if not kit_set and cm and singles:
        n = int(cm.group(1))
        per = [m for m in singles if n * m in singles]          # «2x … 16GB … insg. 32GB» — однозначно 2×16
        if len(per) == 1 and len(singles) == 2:
            kit_set = {(n, per[0])}
        elif len(singles) == 1:
            # «2x Corsair … 32GB»: 2×16 чи 2×32 — невідомо; беремо дешевший варіант (разом 32), щоб не переоцінити
            m = next(iter(singles))
            if m % n == 0 and m // n in SANE_GB:
                kit_set = {(n, m // n)}
    if kit_set:
        n, m = next(iter(kit_set))
        total = n * m
        extra = singles - {total, m}
        if extra:
            return None, None, f"суперечливі ємності {sorted(extra)} vs кіт {n}x{m}"
        if total not in SANE_GB:
            return None, None, f"нереальна ємність {total}"
        return total, n, None
    if len(singles) == 1:
        v = next(iter(singles))
        if v not in SANE_GB:
            return None, None, f"нереальна ємність {v}"
        return v, 1, None
    if len(singles) > 1:
        return None, None, "кілька ємностей у назві (варіації)"
    return None, None, "ємність не вказана"


def parse_title(title: str):
    """→ dict(gen, form, ecc, total, modules, kit, speed, brand, oem) або (None, причина)."""
    t = (title or "").lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("×", "x")
    t = _SEO_NOT.sub(" ", t)
    if DEFECT.search(t):
        return None, "дефект/запчастина"
    first_ddr = _DDR.search(t)
    if JUNK.search(t) or JUNK_BEFORE_DDR.search(t[:first_ddr.start()] if first_ddr else t):
        return None, "не модуль (аксесуар/комплект/ПК)"
    if LOTS.search(t):
        return None, "лот/пакет"
    if re.search(r"\blpddr|soldered|onboard|\bl?camm2?\b", t):   # CAMM — окремий ноутбучний формат, не SO-DIMM
        return None, "LPDDR/впаяна"
    m = _DDR.search(t)
    if not m:
        return None, "покоління не вказано"
    gen = "ddr" + (m.group(1) or m.group(2))
    total, mods, why = parse_capacity(t)
    if total is None:
        return None, why
    ecc = bool(re.search(r"\becc\b", t))
    if SERVER.search(t):
        form = "server"
    elif SODIMM.search(t):
        form = "sodimm"
    else:
        form = "udimm"
    brand = "other"
    head = re.split(r"\b(?:passend fuer|fuer|for|kompatibel mit|compatible with|geeignet fuer)\b", t)[0]   # «passend für HP…» — не виробник модуля
    for name, rx in _BRANDS:
        if rx.search(head):
            brand = name
            break
    # «2x 16GB (G.Skill Aegis & Crucial Ballistix)» — дві різні планки, не заводський кіт: продається дешевше (28.09)
    mixed = False
    if mods > 1 and re.search(r"&|\+|\bund\b|/", head):
        found = {n for n, rx in _BRANDS if n not in CHIP_OEM and rx.search(head)} - {"HyperX"}
        mixed = len(found) >= 2
    # «32GB Kit» без «2x16»: кількість планок невідома, але їх більше однієї
    kit_word = mods == 1 and bool(re.search(r"\bkit\b|\bset\b|dual[- ]?(?:kit|channel)", t))
    explicit_single = mods == 1 and bool(re.search(r"(?<![\d.])1\s*x\s*\d{1,3}\s?gb|\bein(?:e|en|zelne[rn]?)?\s+(?:riegel|modul)", t))
    return dict(gen=gen, form=form, ecc=ecc, total=total, modules=mods, kit=mods > 1, speed=parse_speed(t, gen),
                brand=brand, oem=brand in CHIP_OEM, mixed=mixed, kit_word=kit_word, explicit_single=explicit_single), None
