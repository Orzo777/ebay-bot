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
    r"\b(rgb[- ]?(strip|licht)|light\s*(?:enhancement\s*)?kit|beleuchtungs?-?kit|halter|blende|leer|dummy|attrappe|festplatte|ssd|hdd|nvme|mainboard|motherboard|"
    r"grafikkarte|gpu|rtx|gtx|radeon|smartphone|handy|komplett|barebone|gehaeuse|netzteil|tester|testkarte|"
    r"box only|sticker)\b|\bnur\s+(?:die\s+)?(?:ovp|verpackung|karton)|\bleere?\s+(?:ovp|verpackung|karton)", re.I)
# Не модуль, лише якщо стоїть ДО першого «DDR»: «Gaming PC 7800X3D 64GB DDR5» — ПК, а «32 GB DDR4 … – Gaming PC»,
# «DDR5 … für Ryzen», «… + UDIMM Adapter» — сама пам'ять (28.09: такі оголошення бот відкидав).
JUNK_BEFORE_DDR = re.compile(
    r"\b(k[uü]e?hler|heatsink|heat ?spreader|cooler|kabel|adapter|cpu|prozessor|core (?:i\d|ultra)|ryzen|xeon e?\d|"
    r"windows|win ?1[01]|display|bildschirm|tablet|gaming[- ]?pc|pc[- ]?bundle|aufr[uü]e?st)\b", re.I)
DEFECT = re.compile(r"\b(defekt|bastler|ersatzteil|kaputt|nicht funktionsf|for parts|not working|funktioniert nicht|teildefekt)\b", re.I)
# «2 Stk», «2 Stück» — це кіт, а не лот (28.09); лот — від 5 штук або слова «Konvolut», «Posten»…
LOTS = re.compile(r"\b(lot|konvolut|posten|sammlung|gemischt|mixed|verschiedene|diverse|paket|bundle)\b|(?<![a-z] )(?<![a-z])\b\d{2,3}\s?x\b|"
                  r"\b(?:[5-9]|\d{2,})\s?(?:stk|stueck|st[uü]ck)\b", re.I)

SERVER = re.compile(r"\b(rdimm|lrdimm|r-dimm|registered|reg\.?\s?ecc|ecc\s?reg\.?|fb-?dimm|proliant|poweredge|supermicro|xeon|server)\b|"
                    r"\bm39[13][ab]|\b\d\s?rx4\b|-rb\d\b|\bhmaa?\w{2,4}r7|\bksm\d{2,3}r|\bm321r", re.I)
SODIMM = re.compile(r"so-?\s?dimm|\bs[o0]-?ram\b|\bso[\s-]ram\b(?!\s+(?:ist|war|läuft|laeuft))|\bkcp\d{3}s[sd]\d|\b(zephyrus|legion\s*(?:\d|pro|slim)|ideapad|zenbook|vivobook|thinkbook|xps\s?1[3-7]|omen\s?1[5-7]|helios|nitro\s?5|(?:aero|aorus)\s?1[5-7]x?|tuf\s+(?:gaming\s+)?[af]1[5-7]|galaxy\s?book|laptop\w*|notebook\w*|imac|macbook|mac ?mini|thinkpad|elitebook|latitude|probook|nuc|mini[- ]?pc)\b|"
                    # номери ноутбучних модулів: Samsung M425R/M471A/M474A, Crucial …S5/…SFRA, Kingston KF…S…/KVR…S…
                    r"\bm4(?:25|71|74)[a-z]|\bct\d+g\d+c\d+s5\b|\bct\d+g4sf|\bkf\d{3}s\d{2}|\bkvr\d{2}s\d{2}|"
                    # Kingston Fury Impact / HyperX Impact — тільки ноутбучні (29.09: «Kingston Fury Impact» вважався настільною)
                    r"\bimpact\b|\bcms[xo]\d|\bowc\b|"   # OWC — пам'ять для Mac (iMac — SO-DIMM)
                    # 29.09 (eBay): «260-pin», «HMAA4GS6AJR8N», «HMA82GS6», «HMCG78AGBSA», «SO DDR5», «S0Dimm»
                    r"\b26[02]\s?-?\s?pin|\bhmaa?\d{1,3}gs6|\bhmcg\d{2}[a-z]{3}s|\bso[\s-]*ddr|\bs0-?\s?dimm", re.I)

# (канонічна назва, regex) — порядок = пріоритет; бренди модулів раніше за виробників чипів
BRANDS = [
    ("Corsair", r"corsair|vengeance|dominator"), ("G.Skill", r"g\.?\s?skill|trident z|ripjaws|flare x|aegis"),
    ("Kingston", r"kingston|fury (beast|renegade|impact)|value ?ram|kvr"), ("HyperX", r"hyperx"),
    ("Crucial", r"crucial|ballistix"), ("TeamGroup", r"team ?group|t-?force|teamgroup|elite plus"),
    ("Patriot", r"patriot|viper"), ("ADATA/XPG", r"a-?data|xpg|lancer"), ("Lexar", r"lexar"), ("PNY", r"\bpny\b"),
    ("Transcend", r"transcend"), ("Apacer", r"apacer"), ("Mushkin", r"mushkin"), ("Netac", r"netac"),
    ("Fanxiang", r"fanxiang"), ("Silicon Power", r"silicon[- ]?power"), ("Goodram", r"goodram|wilk"),
    ("Kingmax", r"kingmax"), ("Gigabyte/Aorus", r"gigabyte|aorus"), ("MSI", r"\bmsi\b"), ("Zadak", r"zadak"),
    ("Klevv", r"klevv"), ("OLOy", r"oloy"), ("V-Color", r"v-?color"), ("Biwin", r"biwin"), ("Acer/Predator", r"(?<!hyperx )predator|\bacer\b"),
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

_KIT1 = re.compile(r"(?<![\d.])(\d)\s*(?:x|mal)\s*(\d{1,3})\s?-?\s?(?:gb|g\b)", re.I)     # 2x16GB, 2 x 16 GB, 3x  32GB
_KIT2 = re.compile(r"(?<![\d.])(\d{1,3})\s?-?\s?gb\s?x\s?(\d)\b", re.I)          # 16GB x2
_KIT3 = re.compile(r"\((\d)\s?x\s?(\d{1,3})\)", re.I)                              # (2x16)
_KIT4 = re.compile(r"(?<![\d.])([2-4])\s*x\s*(\d{1,2})(?![\d.])(?!\s*(?:mhz|mt|cl|gb))", re.I)   # «16GB 2x8 5600MHz»
_SINGLE = re.compile(r"(?<![\d.x])(\d{1,3})\s?-?\s?gb\b(?!\s?/\s?s)", re.I)  # «64-GB-Kit»; «GB/s» — швидкість
# «2x Corsair … 16GB … insg. 32GB», «2 Stk 16GB», «2er-Set»: кількість окремо від ємності
_COUNT = re.compile(r"(?:^|[\s(])([2-4])\s?(?:x|stk\.?|stueck|st[uü]ck|er[- ]?(?:set|kit|pack))(?=[\s)]|$)", re.I)
_CAP_COUNT = re.compile(r"(\d{1,3})\s?gb\s*\(?\s*([2-4])\s?(?:stk\.?|stueck|st[uü]ck)(?=[\s).,]|$)", re.I)
# «nicht 32GB 64GB» — SEO-хвіст у назві (28.09: «48GB DDR5 … nicht 32GB 64GB» не розпізнавався)
_SEO_NOT = re.compile(r"\bnicht\s+(?:\d{1,3}\s?gb[\s,/+&]*(?:und|oder)?\s*)+", re.I)
_DDR = re.compile(r"ddr\s?-?\s?([2345])l?(?:\b|(?=m\b))|\bpc([2345])l?\s?-", re.I)
# «DDR4 4 8 16 32 gb», «8/16/32GB» — варіації (продавець виставив кілька ємностей в одному оголошенні); 29.09
_VARIANTS = re.compile(r"(?<![\w.])(\d{1,3})(?!\d)\s*[,/|;]?\s*(?:(\d{1,3})(?!\d)\s*[,/|;]?\s*)+(?:gb|g\b)", re.I)
# «⚠️ ACHTUNG Betrüger» — попередження, а не продаж
WARNING = re.compile(r"\b(?:achtung|warnung|vorsicht|betrueger|betrug|scammer|scam)\b", re.I)


def is_variants(t: str) -> bool:
    if _KIT1.search(t) or _KIT3.search(t):
        return False
    t = re.sub(r"ddr\s?-?\s?[2345]l?\b|\bpc[2345]l?\b", " ", t)
    for m in _VARIANTS.finditer(t):
        nums = [int(x) for x in re.findall(r"\d{1,3}", m.group(0))]
        if len(nums) >= 2 and all(n in SANE_GB and n >= 4 for n in nums) and nums == sorted(set(nums)):
            return True
    return False


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


# Назва без «DDR4/DDR5» (28.09: ~9% оголошень, «Corsair Vengeance pro 32gb 3600mhz»): покоління з номера моделі
# або частоти. Лише однозначні ознаки; DDR3 (до 2133 МГц) не вгадуємо.
_GEN5_HINT = re.compile(r"\bf5-\d|\bkf5\d|\bcm[khtwp]\d+gx5|\bct\d+g5|\bct2k\d+g5|trident\s*z5|ripjaws\s*[sm]5|"
                        r"flare\s*x5|\bm32[35]r|\bm42[05]r|\bpc5\b", re.I)
_GEN4_HINT = re.compile(r"\bf4-\d|\bkf4\d|\bcm[khtwp]\d+gx4|\bct\d+g4|\bct2k\d+g4|\bm378a|\bm471a|\bpc4\b|"
                        r"ballistix|vengeance\s*lpx|vengeance\s*rgb\s*pro|ripjaws\s*v\b|trident\s*z\s*(?:neo|rgb|royal)|"
                        r"\baegis\b|t-?force\s*vulcan\s*z|viper\s*steel", re.I)


def guess_gen(t: str) -> str | None:
    if _GEN5_HINT.search(t):
        return "ddr5"
    if _GEN4_HINT.search(t):
        return "ddr4"
    m = re.search(r"(?<!\d)(\d{4})\s?(?:mhz|mt/?s)", t)
    if m and re.search(r"\bram\b|arbeitsspeicher|dimm|riegel|speicher|\bkit\b|vengeance|fury|trident|ripjaws|dominator|"
                       r"t-?force|viper|aegis|lancer", t):
        v = int(m.group(1))
        if 4800 <= v <= 8800:
            return "ddr5"
        if 2400 <= v <= 4600:
            return "ddr4"
    return None


# Ємність і кількість планок із номера моделі (eBay 28.09: «Corsair Vengeance RGB Pro CMW32GX4M2Z3600C18» без «32GB»):
# Corsair CM?32GX4M2 = 2 планки, разом 32; Kingston KF432C16BBK2/32; G.Skill F4-3200C16D-32G (S/D/Q = 1/2/4); Crucial CT2K16G4.
_PN = [(re.compile(r"\bcm[a-z]{1,3}(\d{1,3})gx[345]m(\d)"), lambda m: (int(m.group(2)), int(m.group(1)))),
       (re.compile(r"\bk[fv][a-z0-9]*?k(\d)[/-](\d{1,3})\b"), lambda m: (int(m.group(1)), int(m.group(2)))),
       (re.compile(r"\bf5-\d{4}[a-z]\d{4}[a-z](\d{2})gx(\d)"), lambda m: (int(m.group(2)), int(m.group(1)) * int(m.group(2)))),
       (re.compile(r"\bk(?:f|cp|vr|sm|th|cs)\d{2,3}[a-z0-9]*?/(\d{1,3})\b"), lambda m: (1, int(m.group(1)))),
       (re.compile(r"\bc[tp](\d{1,3})g\d{1,2}[a-z]"), lambda m: (1, int(m.group(1)))),
       (re.compile(r"\bf[345]-\d{4}c\d{2}([sdq])-(\d{1,3})g"), lambda m: ({"s": 1, "d": 2, "q": 4}[m.group(1)], int(m.group(2)))),
       (re.compile(r"\bct(\d)k(\d{1,3})g\d"), lambda m: (int(m.group(1)), int(m.group(1)) * int(m.group(2)))),
       (re.compile(r"\bhma(851|81g|82g|84g)u6|\bhmaa(4g|8g)u6"),
        lambda m: (1, {"851": 4, "81g": 8, "82g": 16, "84g": 32, "4g": 32, "8g": 64}[m.group(1) or m.group(2)])),
       (re.compile(r"\bm378a(5244|1k43|2k43|2g43|4g43)"), lambda m: (1, {"5244": 4, "1k43": 8, "2k43": 16, "2g43": 16, "4g43": 32}[m.group(1)])),
       (re.compile(r"\bm323r(1gb4|2ga3|4ga3)"), lambda m: (1, {"1gb4": 8, "2ga3": 16, "4ga3": 32}[m.group(1)])),
       (re.compile(r"\bhmcg(78|88)"), lambda m: (1, {"78": 16, "88": 32}[m.group(1)]))]


def part_capacity(t: str):
    """→ (кількість планок, загальна ємність) з номера моделі або None."""
    for rx, f in _PN:
        m = rx.search(t)
        if m:
            return f(m)
    return None


def parse_capacity(t: str):
    """→ (total_gb, modules, None) або (None, None, причина)."""
    kits = [(int(a), int(b)) for a, b in _KIT1.findall(t)] + [(int(b), int(a)) for a, b in _KIT2.findall(t)]         + [(int(a), int(b)) for a, b in _KIT3.findall(t)]
    kit_set = {k for k in kits if 2 <= k[0] <= 8}
    if len(kit_set) > 1:
        return None, None, "кілька різних кітів"
    singles = {int(x) for x in _SINGLE.findall(t)}
    if not kit_set:   # «2x8» без «GB», якщо сума збігається з указаною ємністю
        k4 = {(int(a), int(b)) for a, b in _KIT4.findall(t) if int(a) * int(b) in singles}
        if len(k4) == 1:
            kit_set = k4
    pn = part_capacity(t)
    if not kit_set and pn and pn[0] > 1 and pn[1] % pn[0] == 0 and (not singles or (pn[1] in singles
                                                                                  and singles <= {pn[1], pn[1] // pn[0]})):
        kit_set = {(pn[0], pn[1] // pn[0])}
    elif not kit_set and not singles and pn and pn[0] == 1:
        singles = {pn[1]}
    elif not kit_set and pn and pn[0] == 1 and len(singles) == 1 and next(iter(singles)) > pn[1]:
        v = next(iter(singles))          # «Crucial 32GB CT16G4DFRA32A» — номер однієї планки 16 ГБ, разом 32 → 2×16
        if v % pn[1] == 0 and 2 <= v // pn[1] <= 4:
            kit_set = {(v // pn[1], pn[1])}
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
    t = re.sub(r"[‐-―−]", "-", t)          # «8‑GB‑DDR4»: нерозривні/довгі дефіси → звичайні (28.09)
    t = re.sub(r"(?<=\d)\s*\*\s*(?=\d)", "x", t)           # «2*8GB» = «2x8GB»
    t = re.sub(r"[®™©️]", "", t)
    t = re.sub(r"(?<=\d)\s?gib\b", "gb", t)                # «32GiB»
    t = re.sub(r"(?:(?<=[\s(])|^)k([2-4])(?=[\s),]|$)|\bkit\s+of\s+([2-4])\b", lambda m: f"{m.group(1) or m.group(2)}x ", t)
    t = _SEO_NOT.sub(" ", t)
    if DEFECT.search(t):
        return None, "дефект/запчастина"
    if WARNING.search(t):
        return None, "попередження, а не продаж"
    if is_variants(t):
        return None, "кілька ємностей у назві (варіації)"
    first_ddr = _DDR.search(t)
    if JUNK.search(t) or JUNK_BEFORE_DDR.search(t[:first_ddr.start()] if first_ddr else t):
        return None, "не модуль (аксесуар/комплект/ПК)"
    if LOTS.search(t):
        return None, "лот/пакет"
    if re.search(r"\blpddr|soldered|onboard|\bl?camm2?\b", t):   # CAMM — окремий ноутбучний формат, не SO-DIMM
        return None, "LPDDR/впаяна"
    m = _DDR.search(t)
    gen = "ddr" + (m.group(1) or m.group(2)) if m else guess_gen(t)
    if not gen:
        return None, "покоління не вказано"
    total, mods, why = parse_capacity(t)
    if total is None:
        return None, why
    ecc = bool(re.search(r"(?<!non[- ])(?<!non)\becc\b", t))
    if SERVER.search(t):
        form = "server"
    elif SODIMM.search(t) and not (re.search(r"(?:nicht|kein\w*|no|not)\s+(?:\w+\s+){0,2}(?:laptop|notebook)", t)
                                   and not re.search(r"so-?\s?dimm", t)):
        form = "sodimm"   # «Desktop – nicht für Laptop», «kein Notebook RAM» — настільна (pass 11)
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
    explicit_single = mods == 1 and bool(re.search(r"(?<![\d.])1\s*x\s*\d{1,3}\s?gb|\bein(?:e|en|zelne[rn]?)?\s+(?:riegel|modul)|einzel(?:modul|riegel)|"
                                                   r"^\W*1\s*x\s+(?!\d)|\b1\s?(?:stk|stueck|st[uü]ck)\b", t)
                                         or part_capacity(t) == (1, total))
    return dict(gen=gen, form=form, ecc=ecc, total=total, modules=mods, kit=mods > 1, speed=parse_speed(t, gen),
                brand=brand, oem=brand in CHIP_OEM, mixed=mixed, kit_word=kit_word, explicit_single=explicit_single), None
