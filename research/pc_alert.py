"""Оцінка ПК з підписок «PCs in Hamburg (+20 km)» для окремого ПК-бота.

Купівля — самовивіз у Гамбурзі (готівка на місці, ~€5 на дорогу). Продаж на eBay.de — або цілим ПК, або на
запчастини, що вигідніше. Ціни цілих ПК і старих відеокарт — Terapeak, продані вживані, 30 днів до 26.09.2026
(p25 = «швидкий продаж»). Лише виміряні конфігурації: невідомий процесор → оцінки немає (картка тихо, «глянь сам»).

03.10: розбір — з усіх деталей (відеокарта, процесор, RAM, SSD, решта корпусу), а не лише «відеокарта + RAM»:
HP i7-11700F / RTX 3060 Ti / 64 ГБ DDR4 за 549 € — сама пам'ять ~260 €, а бот бачив лише «ігровий ПК». Для дорогих
ПК — межа торгу («вигідно до ~X €»). Ціни деталей без Terapeak — 65% від p25 оголошень eBay.de (вживані, DE,
03.10): на відомих моделях продажі Terapeak ≈ 0.56–0.79 × p25 оголошень.

Ручна перевірка:
    python research/pc_alert.py "Dell Optiplex 7060 i5-8500 16GB 256GB SSD" 50
"""
import json
import os
import re

# Цілий офісний ПК без відеокарти: p25 за процесором (Terapeak 26.09, «PC i5-8500» тощо; мініпк і ноутбуки відкинуто)
PC_BY_CPU = {
    ("i3", 8): 90, ("i5", 6): 56, ("i5", 7): 85, ("i5", 8): 117, ("i5", 9): 135, ("i5", 10): 200,
    ("i7", 6): 139, ("i7", 7): 180, ("i7", 8): 190,
    # 09.10: Ryzen без відеокарти (APU 3400G / 5600G). Було 280 / 400 — ціни з ігрових ПК (з RTX), а без відеокарти
    # такі продають у рази дешевше: 3400G — 19 оголошень eBay.de, p25 129 € → продаж ~110; 5600G — лише 2 оголошення,
    # обережна оцінка ~220. Переміряти на реальних продажах (Verkaufte Artikel), коли трапиться кілька таких карток.
    ("r5", 3): 110, ("r5", 5): 220,
}
# Ігровий ПК з відеокартою (цілий) і та сама відеокарта окремо (p25)
GAMING_PC_BY_GPU = {"gtx 1060": 200, "gtx 1650": 200, "gtx 1070": 200, "rx 580": 190, "rtx 2060": 250, "rtx 3060": 459}
# Відеокарта окремо: Terapeak 26.09 (перші шість), решта — 0.65 × p25 оголошень eBay.de 03.10
GPU_PART_BASE = {"gtx 1060": 49, "gtx 1650": 70, "gtx 1070": 72, "rx 580": 55, "rtx 2060": 126, "rtx 3060": 247,
            "gtx 1080": 89, "gtx 1080 ti": 123, "gtx 1660": 75, "gtx 1660 super": 89, "gtx 1660 ti": 89,
            "rtx 2060 super": 129, "rtx 2070": 122, "rtx 2070 super": 121, "rtx 2080": 135, "rtx 2080 super": 166,
            "rtx 2080 ti": 214, "rtx 3050": 134, "rtx 3060 ti": 167, "rtx 3070": 200, "rtx 3070 ti": 238, "rtx 3080": 297,
            "rtx 4060": 232, "rtx 4060 ti": 273, "rtx 4070": 394, "rtx 4070 ti": 520, "rx 5700 xt": 114, "rx 6600": 128,
            "rx 6600 xt": 160, "rx 6700 xt": 232, "rx 6800 xt": 295,
            # 09.10: 0.65 × p25 вживаних на eBay.de (1050 Ti 4 ГБ: 131 оголошення, p25 75 €; 1050 2 ГБ: 56, p25 50 €)
            "gtx 1050 ti": 49, "gtx 1050": 32}
# Процесор окремо: 0.7 × p25 оголошень eBay.de 03.10 за типовою моделлю покоління (i5-10400, i7-11700, Ryzen 5 5600…)
CPU_PART_BASE = {("i5", 8): 24, ("i5", 9): 34, ("i5", 10): 54, ("i5", 11): 67, ("i5", 12): 92, ("i5", 13): 120,
            ("i7", 8): 52, ("i7", 9): 73, ("i7", 10): 98, ("i7", 11): 130, ("i7", 12): 140, ("i7", 13): 173,
            ("i9", 9): 151, ("i9", 10): 202, ("i9", 12): 165, ("i9", 13): 214,
            ("r5", 2): 35, ("r5", 3): 55, ("r5", 5): 88, ("r5", 7): 97, ("r7", 2): 43, ("r7", 3): 70, ("r7", 5): 107,
            ("r7", 7): 109, ("r9", 5): 173, ("r9", 7): 175}


def _parts_refresh(gpu: dict, cpu: dict) -> tuple[dict, dict]:
    """09.10: щомісячний замір деталей (price_refresh.pc_parts → ram_prices.json) поверх базових цін; RAM_PRICES_OFF=1 — лише база."""
    gpu, cpu = dict(gpu), dict(cpu)
    if os.getenv("RAM_PRICES_OFF") == "1":
        return gpu, cpu
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ram_prices.json"), encoding="utf-8") as fh:
            pp = json.load(fh).get("pc_parts") or {}
    except (OSError, ValueError):
        return gpu, cpu
    gpu.update({k: v for k, v in (pp.get("gpu") or {}).items() if k in gpu})
    for k, v in (pp.get("cpu") or {}).items():
        fam, _, gen = k.partition("|")
        if (fam, int(gen or 0)) in cpu:
            cpu[(fam, int(gen))] = v
    return gpu, cpu


GPU_PART, CPU_PART = _parts_refresh(GPU_PART_BASE, CPU_PART_BASE)
RAM_PART = {8: 27, 16: 51, 32: 134}   # DDR4 (8 ГБ планка; 2×8; 2×16) — запасні, якщо немає щоденних цін сторожа
SSD_PART = {256: 15, 500: 30, 1000: 60, 2000: 110}   # NVMe/SATA вживані, ~0.6 × оголошень (SSD подорожчали за рік)
CPU_IN_GAMING_PC = 35   # процесор, що зазвичай стоїть у ПК з GTX 1060/1650/RX 580 (i5 6–9-го, Ryzen 5 1–2-го) — у ціну ПК вже входить
REST = 40   # плата + корпус + блок живлення + HDD — одним лотом на KA, самовивозом (обережно: фірмові HP/Dell дешевші)

FEE, ORDER_FEE, PACK = 0.065, 0.45, 4.0
SHIP_OFFICE, SHIP_GAMING, SHIP_PART = 10.49, 19.99, 6.19   # DHL до 10 кг / до 31,5 кг / 2 кг
PICKUP = 5.0   # дорога на самовивіз (HVV)
NEGOTIATE_FROM = 150   # дорожчі ПК: якщо вигідно після торгу до −25% — картка «торгуйся до X €»

_INTEL = re.compile(r"\bi([3579])[\s-]?(\d{4,5})[a-z]{0,2}\b", re.I)
_INTEL_GEN = re.compile(r"\bi([3579])\b[^,/|]{0,20}?\b(\d{1,2})\.?\s?(?:gen|generation|th)\b", re.I)
_RYZEN = re.compile(r"ryzen\s?([579])\s?(\d{4})", re.I)
_GPU = re.compile(r"\b(gtx|rtx|rx)\s?-?(\d{3,4})(?:\s?-?(ti|super|xt)\b)?", re.I)
_RAM = re.compile(r"(\d{1,2})\s?gb\s?(?:ddr\d\s?)?(?:ram|arbeitsspeicher|speicher)?", re.I)
# Об'єм пам'яті: «64 GB DDR4», «RAM: 32GB», «16GB Arbeitsspeicher», «2x 32 GB» — а не «RTX 3060 Ti (8 GB)» чи «1 TB SSD»
_RAM_TAGGED = re.compile(r"(?:(\d)\s?x\s?)?(\d{1,3})\s?gb(?=[^|,;\n]{0,25}?(?:ddr\d|\bram\b|arbeitsspeicher|speicher\b))|"
                         r"(?:\bram|arbeitsspeicher)\s*:?\s*(?:(\d)\s?x\s?)?(\d{1,3})\s?gb", re.I)
# пам'ять відеокарти — одразу після її назви, без іншого «… GB» і роздільника між ними («RTX 3060 Ti (8 GB) * 64 GB DDR4»)
_VRAM_BEFORE = re.compile(r"(?:gtx|rtx|\brx|radeon|geforce|grafik\w*|gpu|vram)(?:(?!gb)[^|,;\n*•\-–])"r"{0,25}$", re.I)
_VRAM_AFTER = re.compile(r"^\s?(?:gddr|vram|grafik)", re.I)
_SSD = re.compile(r"(\d+(?:[.,]\d)?)\s?(tb|gb)\s?(?:(?:m\.?2|nvme|sata|pcie)\s?)*ssd|ssd\s?:?\s?(\d+(?:[.,]\d)?)\s?(tb|gb)", re.I)


# Старе залізо (28.09, 260 живих оголошень ПК-бота): продається за копійки, розбирати нема на що
OLD_HW = re.compile(r"core\s?2|\bduo\b|\bquad\b|phenom|athlon|\bamd\s*a\d{1,2}\b|\ba(?:4|6|8|10|12)-\d{4}|pentium|celeron|"
                    r"\bi[357][\s-]?[2-5]\d{3}\b|\bi[357]\s*[2-5]\.?\s?gen|xeon\s*e[35]-?\d{4}\b(?!\s*v[3-9])|\bddr[23]\b|"
                    r"windows\s?(?:xp|vista|7)\b|"
                    # моделі з процесорами 2–4-го покоління: EliteDesk/ProDesk G1, Compaq, старі OptiPlex, Esprimo P5x0/P7x0
                    r"(?:elite|pro)\s?desk\s*\d{3}\s*g1\b|compaq|optiplex\s*(?:3010|7010|9010|3020|7020|9020|[3-9]90)\b|"
                    r"esprimo\s*p\s?-?(?:5[0-2]0|7[0-2]0)\b|\bfx[\s-]?\d{4}\b", re.I)


# «max. 64 GB», «bis zu 64 GB», «erweiterbar auf 32 GB» — скільки плата підтримує, а не скільки стоїть (09.10:
# «20 GB DDR4-2666 (4 Slots belegt, max. 64 GB)» пішло як 64 ГБ за 269 €)
_RAM_MAX_BEFORE = re.compile(r"(?:max(?:imal)?\.?|bis\s+(?:zu\s+)?|erweiterbar\s+(?:auf|bis)?\s*|aufrüstbar\s+(?:auf|bis)?\s*|"
                             r"unterstützt\s*|support(?:s|ed)?\s*|up\s+to\s*)$", re.I)
_RAM_TOTALS = (4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 96, 128)


def _parse_ram(text: str) -> int | None:
    def is_max(start: int) -> bool:
        return bool(_RAM_MAX_BEFORE.search(text[max(0, start - 22):start].rstrip(" :(")))

    def vram(start: int, end: int, total: int) -> bool:   # «RTX 3060 Ti 64GB DDR4» — RAM: одразу DDR/RAM або >24 ГБ
        if total > 24 or re.match(r"\s?(?:ddr\d|ram\b|arbeitsspeicher)", text[end:end + 8], re.I):
            return False
        return bool(_VRAM_BEFORE.search(text[max(0, start - 30):start]) or _VRAM_AFTER.search(text[end:end + 8]))

    for m in _RAM_TAGGED.finditer(text):
        n, gb = (m.group(1), m.group(2)) if m.group(2) else (m.group(3), m.group(4))
        total = int(n) * int(gb) if n else int(gb)
        if total in _RAM_TOTALS and not is_max(m.start()) and (m.group(4) or not vram(m.start(), m.end(), total)):
            return total
    for x in _RAM.finditer(text):   # «i5 16GB 256GB SSD» — без слова RAM
        gb = int(x.group(1))
        if gb in (4, 8, 16, 32, 64) and not re.match(r"\s?gb\s?(?:ssd|hdd|nvme)", text[x.end(1):x.end(1) + 8], re.I) \
                and not is_max(x.start()) and not vram(x.start(), x.end(), gb):
            return gb
    return None


def _parse_ssd(text: str) -> int | None:
    m = _SSD.search(text)
    if not m:
        return None
    v, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    gb = float(v.replace(",", ".")) * (1000 if unit.lower() == "tb" else 1)
    return max((k for k in SSD_PART if k <= gb * 1.05), default=None)


def parse_pc(title: str) -> dict:
    title = re.sub(r"[‐-―−]", "-", title or "")   # «i5‑4460» з нерозривним дефісом
    cpu = None
    m = _INTEL.search(title)
    if m:
        model = m.group(2)
        gen = int(model[:2]) if len(model) == 5 else int(model[0])
        cpu = (f"i{m.group(1)}", gen)
    elif _INTEL_GEN.search(title):
        g = _INTEL_GEN.search(title)
        cpu = (f"i{g.group(1)}", int(g.group(2)))
    elif _RYZEN.search(title):
        r = _RYZEN.search(title)
        cpu = (f"r{r.group(1)}", int(r.group(2)[0]))
    gpu = None
    g = _GPU.search(title)
    if g:
        gpu = f"{g.group(1).lower()} {g.group(2)}" + (f" {g.group(3).lower()}" if g.group(3) else "")
    out = dict(cpu=cpu, gpu=gpu, ram=_parse_ram(title))
    ssd = _parse_ssd(title)
    if ssd:
        out["ssd"] = ssd
    if re.search(r"\bddr5\b", title, re.I):
        out["ddr5"] = True
    return out


def _net(sale: float, ship: float) -> float:
    return sale - (FEE * sale + ORDER_FEE + ship + PACK + 0.03 * (2 * ship + ORDER_FEE))


def _ram_sale(gb: int | None, ddr5: bool) -> float | None:
    """Кіт пам'яті з ПК (зазвичай 2 планки): щоденна ціна сторожа (ram_alert.REAL, p25), інакше запасна таблиця."""
    if not gb:
        return None
    if gb not in (8, 16, 32, 64, 128):   # 20 = 16 + 4, 12 = 8 + 4: різні планки — рахуємо як найближчий менший набір
        gb = max((k for k in (8, 16, 32, 64) if k <= gb), default=None)
        if not gb:
            return None
    try:
        from ram_alert import REAL
        real = REAL.get(("ddr5" if ddr5 else "ddr4", "udimm", False, gb, 2 if gb >= 16 else 1))
        if real:
            return real["p25"]
    except Exception:
        pass
    return None if ddr5 else RAM_PART.get(gb)


def evaluate_pc(title: str, price: float) -> dict:
    p = parse_pc(title)
    if p["cpu"] and p["cpu"][0] in ("i3", "i5", "i7") and p["cpu"][1] <= 5 and not p["gpu"]:
        return dict(verdict="SKIP", parsed=p, cost=price + PICKUP, reason=f"старе покоління ({p['cpu'][0]} {p['cpu'][1]}-го)")
    if OLD_HW.search(title) and not (p["gpu"] in GAMING_PC_BY_GPU):
        return dict(verdict="SKIP", parsed=p, cost=price + PICKUP, reason="старе залізо — не варте перепродажу")
    options = []   # (опис, чистими після продажу)
    # 09.10: цілий ПК з відеокартою оцінюємо від відеокарти (+ доплата за сильніший процесор, ніж типовий у таких ПК),
    # а не «за процесором»: «Ryzen 5 5600X + GTX 1060» пішов як «ПК з R5 5-го ~€400» (а це ціна ПК з RTX 3060),
    # насправді такі продають за ~300 € → хибне «торгуйся до 270 €» за 350 €.
    if p["gpu"] in GAMING_PC_BY_GPU:
        bonus = max(0, CPU_PART.get(p["cpu"], 0) - CPU_IN_GAMING_PC)
        whole = GAMING_PC_BY_GPU[p["gpu"]] + bonus
        options.append((f"ігровий ПК з {p['gpu'].upper()} цілим ~€{whole:.0f}" + (f" (з них +€{bonus:.0f} за процесор)" if bonus else ""),
                        _net(whole, SHIP_GAMING)))
    # «за процесором» — офісні ПК без відеокарти (так і міряли); з відеокартою — лише Intel-офісні, не Ryzen (ті ціни — з ігрових)
    if p["cpu"] in PC_BY_CPU and not (p["gpu"] and (p["gpu"] in GAMING_PC_BY_GPU or p["cpu"][0].startswith("r"))):
        options.append((f"ПК з {p['cpu'][0].upper()} {p['cpu'][1]}-го покоління цілим ~€{PC_BY_CPU[p['cpu']]}",
                        _net(PC_BY_CPU[p["cpu"]], SHIP_OFFICE)))
    parts = []
    if p["gpu"] in GPU_PART:
        parts.append((f"{p['gpu'].upper()} €{GPU_PART[p['gpu']]}", _net(GPU_PART[p["gpu"]], SHIP_PART)))
    ram = _ram_sale(p["ram"], p.get("ddr5"))
    if ram:
        parts.append((f"RAM {p['ram']} ГБ €{ram:.0f}", _net(ram, SHIP_PART)))
    has_gpu = any(x[0].startswith(("GTX", "RTX", "RX")) for x in parts)
    # Розбирати варто, коли є що продати дорого: відеокарта або від 32 ГБ пам'яті (03.10: 64 ГБ DDR4 ≈ 260 €)
    if parts and (has_gpu or (p["ram"] or 0) >= 32):
        if p["cpu"] in CPU_PART:
            parts.append((f"{p['cpu'][0].upper()} {p['cpu'][1]}-го €{CPU_PART[p['cpu']]}", _net(CPU_PART[p["cpu"]], SHIP_PART)))
        if p.get("ssd") in SSD_PART:
            parts.append((f"SSD {p['ssd'] // 1000 or p['ssd']}{' ТБ' if p['ssd'] >= 1000 else ' ГБ'} €{SSD_PART[p['ssd']]}",
                          _net(SSD_PART[p["ssd"]], SHIP_PART)))
        if has_gpu or p["cpu"] in CPU_PART:
            parts.append((f"решта (плата, корпус, БЖ) ~€{REST} на KA", REST))
        options.append(("на запчастини: " + " + ".join(x[0] for x in parts), sum(x[1] for x in parts)))
    cost = price + PICKUP
    if not options:
        return dict(verdict="UNKNOWN", parsed=p, cost=cost,
                    reason="з назви не видно процесора/відеокарти, які ми міряли — глянь фото й опис")
    how, net = max(options, key=lambda o: o[1])
    profit = net - cost
    roi = profit / cost if cost else 0
    verdict = "BUY-GOOD" if profit >= 40 and roi >= 0.5 else "BUY" if profit >= 20 and roi >= 0.3 else "SKIP"
    out = dict(verdict=verdict, parsed=p, cost=cost, how=how, net=net, profit=profit, roi=roi)
    max_price = int((net / 1.3 - PICKUP) // 5 * 5)   # ціна, за якої заробіток — 30% від вкладеного
    if verdict == "SKIP" and price >= NEGOTIATE_FROM and max_price >= 0.75 * price:
        out.update(verdict="NEGOTIATE", max_price=max_price)
    return out


def pc_card_lines(r: dict) -> list[str]:
    if r["verdict"] == "UNKNOWN":
        return ["❔ Оцінити не вдалося: " + r["reason"]]
    if r["verdict"] == "NEGOTIATE":
        return [f"🤝 <b>ТОРГУЙСЯ</b> · вигідно до <b>~{r['max_price']} €</b> (за поточною ціною заробіток ≈ {r['profit']:.0f} €)",
                f"💶 Найвигідніше: {r['how']} → після комісії й пересилки ≈ {r['net']:.0f} €"]
    tag = "🟢 <b>БЕРИ</b>" if r["verdict"] == "BUY-GOOD" else "🟡 <b>МОЖНА</b>" if r["verdict"] == "BUY" else "⏭ не вигідно"
    return [f"{tag} · заробіток ≈ <b>{r['profit']:.0f} €</b> (з дорогою ≈ {r['cost']:.0f} €)",
            f"💶 Найвигідніше: {r['how']} → після комісії й пересилки ≈ {r['net']:.0f} €"]


if __name__ == "__main__":
    import sys

    print(evaluate_pc(sys.argv[1], float(sys.argv[2])))
