"""Бренди в розрізі вибраних типів RAM: скільки лотів, медіана ціни нового/вживаного (з доставкою, 1 ціна на продавця).
Запуск: python research/ram_brands_by_type.py <datadir>"""
import json
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, ".")
from ram_parse import parse_title
from ram_recheck import TYPES, NOISE_BRANDS

rows = json.load(open(f"{sys.argv[1]}/ram_rows.json", encoding="utf-8"))
data = defaultdict(lambda: defaultdict(lambda: {"new": {}, "used": {}}))
for r in rows.values():
    p, _ = parse_title(r["title"])
    if not p or p["brand"] in NOISE_BRANDS or p["ecc"] or r["price"] is None:
        continue
    key = (p["gen"], p["form"], p["total"], p["kit"])
    if key not in TYPES:
        continue
    cg = "new" if r["cond"] in ("1000", "1500", "1750") else "used"
    tp = r["price"] + (r["ship"] or 0)
    d = data[key][p["brand"]][cg]
    s = r["seller"] or "?"
    d[s] = min(tp, d.get(s, 1e9))


def med(d):
    v = sorted(d.values())
    return statistics.median(v) if len(v) >= 2 else (v[0] if v else None)


for key, name in TYPES.items():
    print(f"\n### {name}")
    lines = []
    for b, d in data[key].items():
        n = len(d["new"]) + len(d["used"])
        lines.append((n, b, med(d["new"]), med(d["used"]), len(d["new"]), len(d["used"])))
    lines.sort(reverse=True)
    print("| Бренд | Лотів (н/в) | Нове, медіана | Вживане, медіана |")
    print("|---|--:|--:|--:|")
    for n, b, mn, mu, nn, nu in lines[:7]:
        print(f"| {b} | {nn}/{nu} | {('€%.0f' % mn) if mn else '—'} | {('€%.0f' % mu) if mu else '—'} |")
