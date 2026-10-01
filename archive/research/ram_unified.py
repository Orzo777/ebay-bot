"""Об'єднана таблиця «на чому заробляється»: тільки вибрані типи, тільки лоти з розпізнаним брендом (без безбрендових і без
OEM-ПК-брендів HP/Dell/Lenovo/… із фраз сумісності). Ціни купівлі/продажу + швидкість. Запуск: python research/ram_unified.py <datadir>"""
import json
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, ".")
from ram_parse import parse_title
from ram_recheck import TYPES, NOISE_BRANDS

datadir = sys.argv[1]
rows = json.load(open(f"{datadir}/ram_rows.json", encoding="utf-8"))
vel = json.load(open(f"{datadir}/ram_velocity.json", encoding="utf-8"))
SHIP, FEE, RET = 4.19, 0.05, 0.03 * (2 * 4.19 + 0.45)


def costs(p):
    return FEE * p + 0.45 + SHIP + RET


def seller_min(rs, lo=0.35, hi=3.0):
    by = {}
    for r in rs:
        by[r["seller"] or "?"] = min(r["tp"], by.get(r["seller"] or "?", 1e9))
    v = sorted(by.values())
    if len(v) >= 5:
        m = statistics.median(v)
        v = [x for x in v if lo * m <= x <= hi * m]
    return v


cells = defaultdict(lambda: {"new": [], "used": []})
for r in rows.values():
    p, _ = parse_title(r["title"])
    if not p or p["brand"] in NOISE_BRANDS or p["ecc"] or r["price"] is None:
        continue
    key = (p["gen"], p["form"], p["total"], p["kit"])
    if key not in TYPES:
        continue
    cg = "new" if r["cond"] in ("1000", "1500", "1750") else "used"
    cells[key][cg].append(dict(tp=r["price"] + (r["ship"] or 0), seller=r["seller"], brand=p["brand"], oem=p["oem"]))

# швидкість по брендових лотах із вибірки velocity
vsum = defaultdict(list)
for iid, v in vel.items():
    if not v["age"] or v["age"] < 7:
        continue
    p, _ = parse_title(v["title"])
    if not p or p["brand"] in NOISE_BRANDS:
        continue
    key = (p["gen"], p["form"], p["total"], p["kit"])
    if key in TYPES and ((v["avail"] or 0) > 1 or (v["sold"] or 0) > 0):
        vsum[key].append((v["sold"] or 0) / v["age"] * 7)

out = []
for key, name in TYPES.items():
    c = cells[key]
    nv, uv = seller_min(c["new"]), seller_min(c["used"])
    nfast = statistics.median(nv[:5]) if len(nv) >= 3 else None
    ufast = statistics.median(uv[:5]) if len(uv) >= 3 else None
    if len(uv) >= 5 and ufast:
        list_price, basis = ufast, "вживане"
    elif nfast:
        list_price, basis = 0.75 * nfast, "нове×0.75*"
    else:
        continue
    if nfast and list_price > 0.9 * nfast:
        list_price = 0.9 * nfast
    real = 0.85 * list_price                                   # реалістичний швидкий продаж
    net_at = lambda buy: real - costs(real) - buy
    cap = (real - costs(real)) / 1.30                          # стеля купівлі: ROI 30%
    good = (real - costs(real)) / 1.60                         # добра ціна: ROI 60%
    top = (real - costs(real)) / 2.00                          # відмінна: ROI 100%
    sold = sum(vsum[key]) if vsum[key] else 0.0
    oem = [x for x in c["new"] + c["used"] if x["oem"]]
    out.append(dict(name=name, nsel=len(nv), usel=len(uv), nfast=nfast, ufast=ufast, list=list_price, real=real, cap=cap, good=good,
                    top=top, net_good=net_at(good), sold=sold, nlots=len(vsum[key]), basis=basis, oem_share=len(oem) / max(1, len(c["new"]) + len(c["used"]))))

print("| Тип | Ціна ОГОЛОШЕННЯ (продаж) | Швидкий продаж | Стеля купівлі (ROI 30%) | Добра ціна купівлі (ROI 60%) | Відмінна (ROI 100%) | Продажів/тиж* | Конкуренція н/в |")
print("|---|--:|--:|--:|--:|--:|--:|--:|")
for o in out:
    print(f"| {o['name']} | €{o['list']:.0f} | €{o['real']:.0f} | ≤ €{o['cap']:.0f} | ≤ €{o['good']:.0f} | ≤ €{o['top']:.0f} | "
          f"{o['sold']:.1f} ({o['nlots']} лотів) | {o['nsel']}/{o['usel']} |")
print()
for o in out:
    print(f"{o['name']}: нове швидка {o['nfast'] and round(o['nfast'])}, вжив. швидка {o['ufast'] and round(o['ufast'])}, основа={o['basis']}, чистими при 'добрій' ціні €{o['net_good']:.0f}, частка OEM {o['oem_share'] * 100:.0f}%")
