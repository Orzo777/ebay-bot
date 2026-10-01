"""Реальні продажі RAM без Terapeak: повторна перевірка тих самих лотів через ≥12–24 год.

  baseline — обирає до N лотів у кожному з вибраних типів, читає getItem (стан, лічильник проданого) і запам'ятовує час
  measure  — знову getItem: лот зник (404/OUT_OF_STOCK) = продано або знято; приріст estimatedSoldQuantity = продажі дилера.
             Рахує «зникло за тиждень» для типу: частка зниклих / діб × 7 × кількість активних лотів типу.
Обмеження: зник ≠ обов'язково продано (продавець міг зняти лот); лічильник проданого є лише в лотів із кількістю >1.

Запуск: python research/ram_recheck.py baseline <datadir>   ...через добу...   python research/ram_recheck.py measure <datadir>
"""
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, ".")
import check
from main import _request_with_backoff
from ram_parse import parse_title

TYPES = {
    ("ddr5", "udimm", 32, True): "DDR5 UDIMM 32 ГБ (2×16) кіт",
    ("ddr5", "udimm", 64, True): "DDR5 UDIMM 64 ГБ (2×32) кіт",
    ("ddr5", "sodimm", 32, False): "DDR5 SO-DIMM 32 ГБ",
    ("ddr5", "sodimm", 16, False): "DDR5 SO-DIMM 16 ГБ",
    ("ddr4", "udimm", 32, True): "DDR4 UDIMM 32 ГБ (2×16) кіт",
    ("ddr4", "udimm", 64, True): "DDR4 UDIMM 64 ГБ (2×32) кіт",
    ("ddr4", "sodimm", 32, False): "DDR4 SO-DIMM 32 ГБ",
    ("ddr4", "sodimm", 64, True): "DDR4 SO-DIMM 64 ГБ (2×32) кіт",
}
NOISE_BRANDS = {"other", "HP", "Dell", "Lenovo", "Apple", "Supermicro", "Medion", "ASUS", "QNAP/Synology"}
F = check.Fetcher()


def get_item(iid):
    try:
        d = _request_with_backoff("GET", "https://api.ebay.com/buy/browse/v1/item/" + iid, headers=F.client._headers(), params={})
    except RuntimeError as exc:
        return "ended" if "404" in str(exc) else "error"
    ea = (d.get("estimatedAvailabilities") or [{}])[0]
    if ea.get("estimatedAvailabilityStatus") == "OUT_OF_STOCK":
        return "ended"
    return dict(sold=ea.get("estimatedSoldQuantity") or 0, avail=ea.get("estimatedAvailableQuantity"))


def baseline(datadir, per_type=30):
    rows = json.load(open(f"{datadir}/ram_rows.json", encoding="utf-8"))
    by = defaultdict(list)
    active = defaultdict(int)
    for r in rows.values():
        p, _ = parse_title(r["title"])
        if not p or p["brand"] in NOISE_BRANDS or r["price"] is None:
            continue
        key = (p["gen"], p["form"], p["total"], p["kit"])
        if key in TYPES and not p["ecc"]:
            active[key] += 1
            by[key].append(r)
    random.seed(11)
    base = {}
    for key, lst in by.items():
        for r in random.sample(lst, min(per_type, len(lst))):
            st = get_item(r["id"])
            if st in ("ended", "error"):
                continue
            base[r["id"]] = dict(type=list(key), ts=datetime.now(timezone.utc).isoformat(), sold=st["sold"], avail=st["avail"],
                                 price=r["price"], group=r["group"], title=r["title"][:70])
            time.sleep(0.08)
        print(TYPES[key], "лотів у вибірці типу:", len(lst), "взято:", min(per_type, len(lst)), flush=True)
    json.dump(dict(base=base, active={"|".join(map(str, k)): v for k, v in active.items()}), open(f"{datadir}/ram_recheck.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("BASELINE DONE лотів:", len(base), "викликів ≈", len(base) + len(by) * 0, flush=True)


def measure(datadir):
    data = json.load(open(f"{datadir}/ram_recheck.json", encoding="utf-8"))
    base, active = data["base"], data["active"]
    now = datetime.now(timezone.utc)
    per = defaultdict(lambda: dict(n=0, ended=0, sold_delta=0, multi=0, days=[]))
    for iid, b in base.items():
        t0 = datetime.fromisoformat(b["ts"])
        days = (now - t0).total_seconds() / 86400
        st = get_item(iid)
        key = tuple(b["type"])
        d = per[key]
        if st == "error":
            continue
        d["n"] += 1
        d["days"].append(days)
        if st == "ended":
            d["ended"] += 1
        else:
            delta = max(0, st["sold"] - (b["sold"] or 0))
            d["sold_delta"] += delta
            d["multi"] += 1 if (b["avail"] or 0) > 1 else 0
        time.sleep(0.08)
    print(f"{'тип':34}{'лотів':>6}{'діб':>6}{'зникло':>8}{'частка/добу':>12}{'активних':>9}{'≈зникає/тиж':>13}{'приріст продано (дилери)':>26}")
    for key, name in TYPES.items():
        d = per.get(key)
        if not d or not d["n"]:
            continue
        days = statistics.median(d["days"])
        share_day = d["ended"] / d["n"] / days if days else 0
        act = active.get("|".join(map(str, key)), 0)
        print(f"{name:34}{d['n']:>6}{days:>6.1f}{d['ended']:>8}{share_day * 100:>11.1f}%{act:>9}{share_day * 7 * act:>13.1f}{d['sold_delta']:>26}")
    print("\n«зникає/тиж» = частка зниклих за добу × 7 × число активних лотів типу у вибірці (зникнення = продано АБО знято).")


if __name__ == "__main__":
    {"baseline": lambda: baseline(sys.argv[2]), "measure": lambda: measure(sys.argv[2])}[sys.argv[1]]()
