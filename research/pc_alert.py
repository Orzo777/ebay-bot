"""Оцінка дешевих ПК з підписки «PCs in Hamburg (+20 km)» для окремого ПК-бота.

Купівля — самовивіз у Гамбурзі (готівка на місці, ~€5 на дорогу). Продаж на eBay.de — або цілим ПК, або на
запчастини (відеокарта + оперативка), що вигідніше. Ціни — Terapeak, продані вживані, 30 днів до 26.09.2026
(p25 = «швидкий продаж»). Лише виміряні конфігурації: невідомий процесор → оцінки немає (картка тихо, «глянь сам»).

Ручна перевірка:
    python research/pc_alert.py "Dell Optiplex 7060 i5-8500 16GB 256GB SSD" 50
"""
import re

# Цілий офісний ПК без відеокарти: p25 за процесором (Terapeak 26.09, «PC i5-8500» тощо; мініпк і ноутбуки відкинуто)
PC_BY_CPU = {
    ("i3", 8): 90, ("i5", 6): 56, ("i5", 7): 85, ("i5", 8): 117, ("i5", 9): 135, ("i5", 10): 200,
    ("i7", 6): 139, ("i7", 7): 180, ("i7", 8): 190, ("r5", 3): 280, ("r5", 5): 400,
}
# Ігровий ПК з відеокартою (цілий) і та сама відеокарта окремо (p25)
GAMING_PC_BY_GPU = {"gtx 1060": 200, "gtx 1650": 200, "gtx 1070": 200, "rx 580": 190, "rtx 2060": 250, "rtx 3060": 459}
GPU_PART = {"gtx 1060": 49, "gtx 1650": 70, "gtx 1070": 72, "rx 580": 55, "rtx 2060": 126, "rtx 3060": 247}
RAM_PART = {8: 27, 16: 51, 32: 134}   # DDR4 (8 ГБ планка; 2×8; 2×16)

FEE, ORDER_FEE, PACK = 0.065, 0.45, 4.0
SHIP_OFFICE, SHIP_GAMING, SHIP_PART = 10.49, 19.99, 6.19   # DHL до 10 кг / до 31,5 кг / 2 кг
PICKUP = 5.0   # дорога на самовивіз (HVV)

_INTEL = re.compile(r"\bi([357])[\s-]?(\d{4,5})[a-z]{0,2}\b", re.I)
_INTEL_GEN = re.compile(r"\bi([357])\b[^,/|]{0,20}?\b(\d{1,2})\.?\s?(?:gen|generation|th)\b", re.I)
_RYZEN = re.compile(r"ryzen\s?([57])\s?(\d{4})", re.I)
_GPU = re.compile(r"(gtx|rtx|rx)\s?-?(\d{3,4})", re.I)
_RAM = re.compile(r"(\d{1,2})\s?gb\s?(?:ddr\d\s?)?(?:ram|arbeitsspeicher|speicher)?", re.I)


def parse_pc(title: str) -> dict:
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
        gpu = f"{g.group(1).lower()} {g.group(2)}"
    ram = None
    for x in _RAM.finditer(title):
        gb = int(x.group(1))
        if gb in (4, 8, 16, 32, 64) and not re.match(r"\s?gb\s?(?:ssd|hdd|nvme)", title[x.end(1):x.end(1) + 8], re.I):
            ram = gb
            break
    return dict(cpu=cpu, gpu=gpu, ram=ram)


def _net(sale: float, ship: float) -> float:
    return sale - (FEE * sale + ORDER_FEE + ship + PACK + 0.03 * (2 * ship + ORDER_FEE))


def evaluate_pc(title: str, price: float) -> dict:
    p = parse_pc(title)
    options = []   # (опис, чистими після продажу)
    if p["gpu"] in GAMING_PC_BY_GPU:
        options.append((f"ігровий ПК з {p['gpu'].upper()} цілим ~€{GAMING_PC_BY_GPU[p['gpu']]}",
                        _net(GAMING_PC_BY_GPU[p["gpu"]], SHIP_GAMING)))
    if p["cpu"] in PC_BY_CPU:
        options.append((f"ПК з {p['cpu'][0].upper()} {p['cpu'][1]}-го покоління цілим ~€{PC_BY_CPU[p['cpu']]}",
                        _net(PC_BY_CPU[p["cpu"]], SHIP_OFFICE)))
    parts = []
    if p["gpu"] in GPU_PART:
        parts.append((f"{p['gpu'].upper()} €{GPU_PART[p['gpu']]}", _net(GPU_PART[p["gpu"]], SHIP_PART)))
    if p["ram"] in RAM_PART:
        parts.append((f"RAM {p['ram']} ГБ €{RAM_PART[p['ram']]}", _net(RAM_PART[p["ram"]], SHIP_PART)))
    if parts and any(x[0].startswith(("GTX", "RTX", "RX")) for x in parts):
        options.append(("на запчастини: " + " + ".join(x[0] for x in parts), sum(x[1] for x in parts)))
    cost = price + PICKUP
    if not options:
        return dict(verdict="UNKNOWN", parsed=p, cost=cost,
                    reason="з назви не видно процесора/відеокарти, які ми міряли — глянь фото й опис")
    how, net = max(options, key=lambda o: o[1])
    profit = net - cost
    roi = profit / cost if cost else 0
    verdict = "BUY-GOOD" if profit >= 40 and roi >= 0.5 else "BUY" if profit >= 20 and roi >= 0.3 else "SKIP"
    return dict(verdict=verdict, parsed=p, cost=cost, how=how, net=net, profit=profit, roi=roi)


def pc_card_lines(r: dict) -> list[str]:
    if r["verdict"] == "UNKNOWN":
        return ["❔ Оцінити не вдалося: " + r["reason"]]
    tag = "🟢 <b>БЕРИ</b>" if r["verdict"] == "BUY-GOOD" else "🟡 <b>МОЖНА</b>" if r["verdict"] == "BUY" else "⏭ не вигідно"
    return [f"{tag} · заробіток ≈ <b>{r['profit']:.0f} €</b> (з дорогою ≈ {r['cost']:.0f} €)",
            f"💶 Найвигідніше: {r['how']} → після комісії й пересилки ≈ {r['net']:.0f} €"]


if __name__ == "__main__":
    import sys

    print(evaluate_pc(sys.argv[1], float(sys.argv[2])))
