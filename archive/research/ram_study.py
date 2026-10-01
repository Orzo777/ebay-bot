"""Вичерпне дослідження ринку оперативної пам'яті на eBay.de (+ інші eBay-маркетплейси через офіційне API).

Етапи (кожен кешує результат у <datadir>, можна перезапускати):
  harvest  — збір лотів по сітці: покоління × форм-фактор × ємність × кіт × бренд × стан (фікс-ціна, DE)
  velocity — швидкість продажів: getItem.estimatedSoldQuantity для вибірки лотів у найбільших клітинках
  cross    — ціни на eBay AT/FR/IT/ES/NL (EUR) та GB/US (з ПДВ 19% і курсом) для тих самих клітинок
  report   — розбір назв, клітинки, медіани, бренди, швидкість, крос-ціни → CSV + текстовий підсумок

Запуск: python research/ram_study.py <stage> <datadir>
Квота eBay спільна з ботом; кожен етап друкує кількість викликів. Дані — ціни ОГОЛОШЕНЬ (не проданих).
"""
import csv
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, ".")
import config
import check
from main import _request_with_backoff, _to_float
from ram_parse import parse_title

NOW = datetime.now(timezone.utc)
NEW_COND = "1000|1500|1750"
USED_COND = "2750|3000|4000|5000"
FORMS = {"udimm": "Desktop DIMM", "sodimm": "SODIMM Laptop", "server": "RDIMM ECC Server"}
CAPS = {  # (gen, form) -> списки загальних ємностей для запитів
    ("ddr5", "udimm"): [8, 16, 24, 32, 48, 64, 96, 128], ("ddr5", "sodimm"): [8, 16, 32, 48, 64], ("ddr5", "server"): [16, 32, 64, 128],
    ("ddr4", "udimm"): [4, 8, 16, 32, 64], ("ddr4", "sodimm"): [4, 8, 16, 32, 64], ("ddr4", "server"): [8, 16, 32, 64, 128],
    ("ddr3", "udimm"): [2, 4, 8, 16], ("ddr3", "sodimm"): [2, 4, 8, 16], ("ddr3", "server"): [4, 8, 16, 32],
}
KITS = {"ddr5": ["2x8", "2x16", "2x24", "2x32", "2x48", "4x16", "4x32"], "ddr4": ["2x4", "2x8", "2x16", "2x32", "4x8", "4x16", "4x32"],
        "ddr3": ["2x4", "2x8", "4x4", "4x8"]}
BRAND_Q = ["Corsair", "G.Skill", "Kingston", "Kingston Fury", "Crucial", "TeamGroup", "Patriot", "ADATA", "XPG", "Lexar", "PNY", "Transcend",
           "Apacer", "Mushkin", "Netac", "Fanxiang", "Silicon Power", "Goodram", "Samsung", "SK Hynix", "Micron", "Nanya", "HyperX", "Klevv",
           "Gigabyte", "MSI", "Acer Predator", "Hikvision", "Kingmax", "Zadak", "Dell", "HP", "Lenovo", "Apple"]
BRAND_GROUPS = [("ddr5", "udimm"), ("ddr4", "udimm"), ("ddr5", "sodimm"), ("ddr4", "sodimm"), ("ddr3", "udimm")]
CALLS = {"n": 0}


def fetch_search(q, cond, *, sort=None, mk="EBAY_DE", cc="DE", cur="EUR", extra_filter="", limit=200):
    flt = f"buyingOptions:{{FIXED_PRICE}},conditionIds:{{{cond}}},price:[3..],priceCurrency:{cur}"
    if mk == "EBAY_DE":
        flt += f",itemLocationCountry:{cc}"
    else:
        flt += ",deliveryCountry:DE"
    if extra_filter:
        flt += "," + extra_filter
    params = {"q": q, "filter": flt, "limit": limit}
    if sort:
        params["sort"] = sort
    hdr = F.client._headers()
    hdr["X-EBAY-C-MARKETPLACE-ID"] = mk
    if mk != "EBAY_DE":
        hdr["X-EBAY-C-ENDUSERCTX"] = "contextualLocation=country=DE,zip=10115"
    CALLS["n"] += 1
    for attempt in range(3):
        try:
            d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=hdr, params=params)
            return d.get("itemSummaries") or [], int(d.get("total") or 0)
        except Exception:
            time.sleep(2)
    return [], 0


def row_of(it, group):
    so = (it.get("shippingOptions") or [{}])[0]
    ship = _to_float((so.get("shippingCost") or {}).get("value"))
    sl = it.get("seller") or {}
    return dict(id=it["itemId"], title=it["title"], price=_to_float((it.get("price") or {}).get("value")), ship=ship,
                cur=(it.get("price") or {}).get("currency"), cond=str(it.get("conditionId") or ""), group=group,
                seller=sl.get("username"), fb=sl.get("feedbackScore") or 0, pct=sl.get("feedbackPercentage"),
                created=it.get("itemCreationDate"), cat="|".join(c["categoryName"] for c in (it.get("categories") or [])[:2]),
                country=(it.get("itemLocation") or {}).get("country"))


def load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)


# ----------------------------------------------------------------------------- harvest
def build_queries():
    qs = []
    for (gen, form), caps in CAPS.items():
        G, fk = gen.upper(), FORMS[form]
        qs.append((f"{G} {fk}", ("base", gen, form)))
        for c in caps:
            qs.append((f"{G} {c}GB {fk}", ("cap", gen, form)))
    for gen, kits in KITS.items():
        for k in kits:
            qs.append((f"{gen.upper()} {k}GB Kit", ("kit", gen, None)))
    for gen, form in BRAND_GROUPS:
        for b in BRAND_Q:
            qs.append((f"{b} {gen.upper()} {FORMS[form].split()[0]}", ("brand", gen, form)))
    return qs


def stage_harvest(datadir):
    rows = load(f"{datadir}/ram_rows.json", {})
    meta = load(f"{datadir}/ram_meta.json", {"totals": {}, "done": []})
    done = set(meta["done"])
    qs = build_queries()
    plan = []
    for q, kind in qs:
        sorts = [None, "newlyListed", "price"] if kind[0] in ("cap", "base", "kit") else [None]
        for cname, cond in (("new", NEW_COND), ("used", USED_COND)):
            for s in sorts:
                plan.append((q, kind, cname, cond, s))
    print(f"план: {len(plan)} викликів, вже виконано {len(done)}, лотів у кеші {len(rows)}", flush=True)
    for i, (q, kind, cname, cond, s) in enumerate(plan):
        key = f"{q}|{cname}|{s}"
        if key in done:
            continue
        its, total = fetch_search(q, cond, sort=s)
        new_rows = 0
        for it in its:
            if it["itemId"] not in rows:
                rows[it["itemId"]] = row_of(it, cname)
                new_rows += 1
        meta["totals"][key] = total
        meta["done"].append(key)
        if i % 25 == 0:
            save(f"{datadir}/ram_rows.json", rows)
            save(f"{datadir}/ram_meta.json", meta)
            print(f"{i}/{len(plan)} calls={CALLS['n']} rows={len(rows)} | {q} [{cname},{s}] total={total} +{new_rows}", flush=True)
        time.sleep(0.1)
    save(f"{datadir}/ram_rows.json", rows)
    save(f"{datadir}/ram_meta.json", meta)
    print("HARVEST DONE calls", CALLS["n"], "rows", len(rows), flush=True)


# ----------------------------------------------------------------------------- розбір і клітинки
def total_price(r):
    if r["price"] is None:
        return None
    return round(r["price"] + (r["ship"] or 0.0), 2)


def cond_group(r):
    return "new" if r["cond"] in ("1000", "1500", "1750") else "used"


def parsed_rows(rows):
    out, rej = [], Counter()
    for r in rows.values():
        p, why = parse_title(r["title"])
        if not p:
            rej[why.split(" vs")[0] if why else "?"] += 1
            continue
        tp = total_price(r)
        if tp is None or r["cur"] not in (None, "EUR"):
            rej["ціна/валюта"] += 1
            continue
        if r.get("country") and r["country"] != "DE" and r["group"] and "cross" not in r:
            pass
        out.append({**r, **p, "tp": tp, "cg": cond_group(r)})
    return out, rej


def cell_key(p, with_kit=True):
    form = p["form"] + ("-ecc" if p["ecc"] and p["form"] != "server" else "")
    return (p["gen"], form, p["total"], ("kit" if p["kit"] else "1x") if with_kit else "*")


def qstats(prices):
    v = sorted(prices)
    if not v:
        return None
    d = dict(n=len(v), min=v[0], med=statistics.median(v), fast=statistics.median(v[:5]))
    if len(v) >= 4:
        q = statistics.quantiles(v, n=4)
        d["p25"], d["p75"] = q[0], q[2]
    else:
        d["p25"] = d["p75"] = None
    return d


def seller_prices(rows, lo=0.3, hi=3.0):
    """Одна (мінімальна) ціна на продавця; викиди <30% і >300% медіани відкидаємо (помилки розбору/шахрайство)."""
    by = {}
    for r in rows:
        s = r["seller"] or "?"
        by[s] = min(r["tp"], by.get(s, 1e9))
    v = list(by.values())
    if len(v) >= 5:
        m = statistics.median(v)
        v = [x for x in v if lo * m <= x <= hi * m]
    return v


def aggregate(rows):
    pr, rej = parsed_rows(rows)
    cells = defaultdict(lambda: {"new": [], "used": []})
    for p in pr:
        cells[cell_key(p)][p["cg"]].append(p)
    return pr, rej, cells


# ----------------------------------------------------------------------------- velocity
def stage_velocity(datadir, per_cell=8, top_cells=40):
    rows = load(f"{datadir}/ram_rows.json", {})
    vel = load(f"{datadir}/ram_velocity.json", {})
    pr, rej, cells = aggregate(rows)
    ranked = sorted(cells.items(), key=lambda kv: -(len({p['seller'] for p in kv[1]['new']}) + len({p['seller'] for p in kv[1]['used']})))
    todo = 0
    for key, c in ranked[:top_cells]:
        allr = [p for cg in ("new", "used") for p in c[cg] if p["fb"] >= 30]
        # пріоритет — лоти з високою репутацією (дилери мають запас і дають лічильник продажів), різні продавці
        allr.sort(key=lambda p: -p["fb"])
        seen, pick = set(), []
        for p in allr:
            if p["seller"] not in seen and p["id"] not in vel:
                seen.add(p["seller"])
                pick.append(p)
            if len(pick) >= per_cell:
                break
        for p in pick:
            try:
                CALLS["n"] += 1
                d = _request_with_backoff("GET", "https://api.ebay.com/buy/browse/v1/item/" + p["id"], headers=F.client._headers(), params={})
            except Exception:
                continue
            ea = (d.get("estimatedAvailabilities") or [{}])[0]
            try:
                age = max(1.0, (NOW - datetime.fromisoformat(d["itemCreationDate"].replace("Z", "+00:00"))).total_seconds() / 86400)
            except Exception:
                age = None
            vel[p["id"]] = dict(sold=ea.get("estimatedSoldQuantity"), avail=ea.get("estimatedAvailableQuantity"), age=age, cell="|".join(map(str, key)),
                                cg=p["cg"], price=p["tp"], seller=p["seller"], title=p["title"][:70])
            todo += 1
            time.sleep(0.08)
        save(f"{datadir}/ram_velocity.json", vel)
        print(f"{key} +{len(pick)} calls={CALLS['n']}", flush=True)
    print("VELOCITY DONE calls", CALLS["n"], "listings", len(vel), flush=True)


# ----------------------------------------------------------------------------- cross-market
RATE = {"USD": 0.87032, "GBP": 1.1658}
XMARKETS = [("EBAY_AT", "EUR", 1.0), ("EBAY_FR", "EUR", 1.0), ("EBAY_IT", "EUR", 1.0), ("EBAY_ES", "EUR", 1.0), ("EBAY_NL", "EUR", 1.0),
            ("EBAY_GB", "GBP", 1.19), ("EBAY_US", "USD", 1.19)]


def stage_cross(datadir, top_cells=14):
    rows = load(f"{datadir}/ram_rows.json", {})
    cross = load(f"{datadir}/ram_cross.json", {})
    pr, rej, cells = aggregate(rows)
    ranked = sorted(cells.items(), key=lambda kv: -len({p['seller'] for p in kv[1]['new']}))
    for key, c in ranked[:top_cells]:
        gen, form, total, kit = key
        if form.endswith("-ecc"):
            continue
        fk = {"udimm": "Desktop", "sodimm": "SODIMM", "server": "RDIMM ECC"}[form]
        q = f"{gen.upper()} {total}GB {fk}"
        for mk, cur, vat in XMARKETS:
            ck = f"{key}|{mk}"
            if ck in cross:
                continue
            its, tot = fetch_search(q, NEW_COND, mk=mk, cur=cur)
            prices = []
            for it in its:
                r = row_of(it, "new")
                p, why = parse_title(r["title"])
                if not p or (p["gen"], p["form"] + ("-ecc" if p["ecc"] and p["form"] != "server" else ""), p["total"], "kit" if p["kit"] else "1x") != key:
                    continue
                if r["price"] is None or r["ship"] is None or r["cur"] != cur or r["fb"] < 30:
                    continue
                fx = 1.0 if cur == "EUR" else RATE[cur]
                prices.append(round((r["price"] + r["ship"]) * fx * vat, 2))
            cross[ck] = dict(n=len(prices), fast=(statistics.median(sorted(prices)[:5]) if len(prices) >= 3 else None), min=(min(prices) if prices else None), total=tot)
            time.sleep(0.1)
        save(f"{datadir}/ram_cross.json", cross)
        print(key, "calls", CALLS["n"], flush=True)
    print("CROSS DONE calls", CALLS["n"], flush=True)


def stage_cross2(datadir, top_cells=16):
    """Справжній міждержавний тест: лоти, ФІЗИЧНО розташовані в іншій країні (itemLocationCountry), ціна з доставкою до DE.
    (Попередній cross брав deliveryCountry:DE на маркетплейсах AT/IT/ES/NL — це був той самий спільний каталог, що й DE.)"""
    rows = load(f"{datadir}/ram_rows.json", {})
    cross = load(f"{datadir}/ram_cross2.json", {})
    pr, rej, cells = aggregate(rows)
    ranked = sorted(cells.items(), key=lambda kv: -len({p['seller'] for p in kv[1]['new']}))
    for key, c in ranked[:top_cells]:
        gen, form, total, kit = key
        if form.endswith("-ecc"):
            continue
        fk = {"udimm": "Desktop", "sodimm": "SODIMM", "server": "RDIMM ECC"}[form]
        q = f"{gen.upper()} {total}GB {fk}"
        for cc in ("AT", "FR", "IT", "ES", "NL", "PL", "BE", "IE"):
            ck = f"{key}|{cc}"
            if ck in cross:
                continue
            its, tot = fetch_search(q, NEW_COND, cc=cc)
            prices = []
            for it in its:
                r = row_of(it, "new")
                p, why = parse_title(r["title"])
                if not p or (p["gen"], p["form"] + ("-ecc" if p["ecc"] and p["form"] != "server" else ""), p["total"], "kit" if p["kit"] else "1x") != key:
                    continue
                if r["price"] is None or r["ship"] is None or r["fb"] < 30 or (r.get("country") and r["country"] != cc):
                    continue
                prices.append(round(r["price"] + r["ship"], 2))
            cross[ck] = dict(n=len(prices), fast=(statistics.median(sorted(prices)[:5]) if len(prices) >= 3 else None),
                             min=(min(prices) if prices else None))
            time.sleep(0.1)
        save(f"{datadir}/ram_cross2.json", cross)
        print(key, "calls", CALLS["n"], flush=True)
    print("CROSS2 DONE calls", CALLS["n"], flush=True)


# ----------------------------------------------------------------------------- report
def stage_report(datadir, outdir):
    rows = load(f"{datadir}/ram_rows.json", {})
    meta = load(f"{datadir}/ram_meta.json", {"totals": {}})
    vel = load(f"{datadir}/ram_velocity.json", {})
    cross = load(f"{datadir}/ram_cross.json", {})
    pr, rej, cells = aggregate(rows)
    print(f"лотів зібрано: {len(rows)}; розпізнано: {len(pr)} ({len(pr) / max(1, len(rows)) * 100:.0f}%)")
    print("причини відсіву:", dict(rej.most_common(12)))
    vcell = defaultdict(list)
    for v in vel.values():
        vcell[v["cell"]].append(v)
    out = []
    for key, c in cells.items():
        row = dict(gen=key[0], form=key[1], total_gb=key[2], kit=key[3])
        for cg in ("new", "used"):
            sp = seller_prices(c[cg])
            st = qstats(sp)
            row[f"{cg}_lots"] = len(c[cg])
            row[f"{cg}_sellers"] = len(sp)
            for k in ("min", "p25", "fast", "med", "p75"):
                row[f"{cg}_{k}"] = round(st[k], 2) if st and st.get(k) is not None else ""
            row[f"{cg}_eur_gb"] = round(st["med"] / key[2], 2) if st else ""
        allp = c["new"] + c["used"]
        bc = Counter(p["brand"] for p in allp)
        row["top_brands"] = "; ".join(f"{b}:{n}" for b, n in bc.most_common(4))
        sc = Counter(p["speed"] for p in allp if p["speed"])
        row["top_speeds"] = "; ".join(f"{s}:{n}" for s, n in sc.most_common(3))
        vl = vcell.get("|".join(map(str, key)), [])
        act = [v for v in vl if v["age"] and ((v["avail"] or 0) > 1 or (v["sold"] or 0) > 0)]
        rates = [(v["sold"] or 0) / v["age"] * 7 for v in act if v["age"] >= 7]
        row["vel_sampled"] = len(vl)
        row["vel_active"] = len(act)
        row["vel_sold_total"] = sum((v["sold"] or 0) for v in act)
        row["vel_median_units_per_week_per_listing"] = round(statistics.median(rates), 2) if rates else ""
        row["vel_sum_units_per_week"] = round(sum(rates), 1) if rates else ""
        xs = {}
        for mk, cur, vat in XMARKETS:
            x = cross.get(f"{key}|{mk}")
            if x and x["fast"]:
                xs[mk[5:]] = x["fast"]
        row["cross_fast_landed"] = "; ".join(f"{k}:{v:.0f}" for k, v in xs.items())
        de = row["new_fast"]
        row["cross_best_vs_de_new"] = (round(min(xs.values()) / de, 2) if xs and de else "")
        out.append(row)
    out.sort(key=lambda r: (r["gen"], r["form"], r["total_gb"], r["kit"]))
    os.makedirs(outdir, exist_ok=True)
    with open(f"{outdir}/ram_cells.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    # бренди
    brand_rows = []
    groups = defaultdict(list)
    for p in pr:
        groups[(p["gen"], p["form"])].append(p)
    for (gen, form), ps in sorted(groups.items()):
        bc = Counter(p["brand"] for p in ps)
        for b, n in bc.most_common():
            if n < 5:
                continue
            row = dict(gen=gen, form=form, brand=b, listings=n, share_pct=round(n / len(ps) * 100, 1))
            for cg in ("new", "used"):
                ppg = [p["tp"] / p["total"] for p in ps if p["brand"] == b and p["cg"] == cg and 1 <= p["total"]]
                row[f"{cg}_median_eur_gb"] = round(statistics.median(ppg), 2) if len(ppg) >= 3 else ""
            brand_rows.append(row)
    with open(f"{outdir}/ram_brands.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(brand_rows[0].keys()))
        w.writeheader()
        w.writerows(brand_rows)
    other = Counter(re.split(r"[^a-z0-9]+", p["title"].lower())[0] for p in pr if p["brand"] == "other")
    print("\nНерозпізнані бренди (перше слово назви), топ:", other.most_common(25))
    print("\nРозмір ринку за eBay total (базові запити):")
    for key, tot in sorted(meta["totals"].items()):
        if key.endswith("|None") and key.split("|")[0] in [f"{g.upper()} {fk}" for g in ("ddr3", "ddr4", "ddr5") for fk in FORMS.values()]:
            print(f"  {key.split('|')[0]:26} {key.split('|')[1]:5} total={tot}")
    print(f"\nКлітинок: {len(out)}; збережено {outdir}/ram_cells.csv, ram_brands.csv")
    return out


F = check.Fetcher()

if __name__ == "__main__":
    stage, datadir = sys.argv[1], sys.argv[2]
    os.makedirs(datadir, exist_ok=True)
    {"harvest": lambda: stage_harvest(datadir), "velocity": lambda: stage_velocity(datadir), "cross": lambda: stage_cross(datadir),
     "cross2": lambda: stage_cross2(datadir),
     "report": lambda: stage_report(datadir, sys.argv[3] if len(sys.argv) > 3 else "research/ram_out")}[stage]()
