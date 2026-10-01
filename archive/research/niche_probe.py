"""Проб ніш новим рушієм (READ-ONLY, DE): потік свіжих лотів, оціночність, товщина «хвоста знижок»."""
import json, re, statistics, sys, time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
sys.path.insert(0, r"D:\ebay-bot")
import config, identity, quality
from main import EbayClient, total_price, _request_with_backoff

NICHES = [
    # (назва, запит, min_price, режим)
    ("LEGO Star Wars", "LEGO Star Wars", 40, "lego"),
    ("LEGO Technic", "LEGO Technic", 40, "lego"),
    ("LEGO Icons", "LEGO Icons", 40, "lego"),
    ("LEGO Harry Potter", "LEGO Harry Potter", 30, "lego"),
    ("LEGO Ideas", "LEGO Ideas", 40, "lego"),
    ("LEGO Speed Champions", "LEGO Speed Champions", 20, "lego"),
    ("LEGO Marvel/Disney", "LEGO Marvel", 30, "lego"),
    ("Tonies (фігурки)", "Tonies Figur", 12, "generic"),
    ("Toniebox 2 Starterset", "Toniebox 2 Starterset", 60, "generic"),
    ("Pokemon Top-Trainer-Box DE", "Pokemon Top Trainer Box Deutsch", 35, "pkm_etb"),
    ("Pokemon Booster Bundle DE", "Pokemon Booster Bundle Deutsch", 20, "pkm_bundle"),
    ("Braun Series 9 бритва", "Braun Series 9 Rasierer", 80, "generic"),
    ("Braun Silk-expert IPL", "Braun Silk-expert Pro IPL", 100, "generic"),
    ("Philips Lumea IPL", "Philips Lumea IPL", 80, "generic"),
    ("Oral-B iO", "Oral-B iO Zahnbürste", 50, "generic"),
    ("Philips Sonicare", "Philips Sonicare DiamondClean", 50, "generic"),
    ("Sonos Roam/One/Era", "Sonos Roam", 80, "generic"),
    ("Sony WH-1000XM4", "Sony WH-1000XM4", 100, "generic"),
    ("Philips Hue Starter", "Philips Hue Starter Set", 40, "generic"),
    ("Kindle Paperwhite", "Kindle Paperwhite", 70, "generic"),
    ("Garmin Forerunner", "Garmin Forerunner", 120, "generic"),
    ("Logitech MX Master", "Logitech MX Master 3S", 60, "generic"),
    ("Nintendo Switch OLED", "Nintendo Switch OLED Konsole", 200, "generic"),
    ("Instax Mini 12", "Fujifilm Instax Mini 12", 50, "generic"),
    ("Catan Grundspiel", "Catan Grundspiel", 20, "generic"),
    ("Ticket to Ride Europa", "Ticket to Ride Europa", 25, "generic"),
    ("DualSense Edge", "DualSense Edge", 130, "generic"),
    ("Nextbase Dashcam", "Nextbase Dashcam", 55, "generic"),
]

EBAY = EbayClient()
NOW = datetime.now(timezone.utc)
LEGO_RX = re.compile(r"\b(\d{5})\b")


def fetch(q, mp, sort=None):
    p = {"q": q, "filter": config.search_filter(mp) + ",itemLocationCountry:DE", "limit": 200}
    if sort:
        p["sort"] = sort
    d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=EBAY._headers(), params=p)
    return d.get("itemSummaries") or [], int(d.get("total") or 0)


def created(it):
    try:
        return datetime.fromisoformat(it["itemCreationDate"].replace("Z", "+00:00"))
    except Exception:
        return None


def profile_for(niche, query, mode):
    brand = identity.norm(query).split()[0]
    base = dict(identity._DEFAULT)
    if mode in ("pkm_etb", "pkm_bundle"):
        allow = {"etb"} if mode == "pkm_etb" else {"bundle"}
        return identity._mk("pkm", identity._TCG, allow_ptypes=allow, plus_policy="exclude",
                            must=[("pokemon",)], strip=identity._fs("pokemon", "nintendo", "tcg", "top", "trainer", "box", "deutsch", "booster", "bundle", "elite"))
    strip = identity._fs(*[t for t in identity.norm(query).split() if len(t) > 1])
    return identity._mk(identity._slug(query) or "gen", base, must=[(brand,)], strip=strip)


def run(niche, query, mp, mode):
    identity.PROFILES[query] = profile_for(niche, query, mode)
    cat = {"query": query, "min_price": mp}
    a, total = fetch(query, mp)
    n_, _ = fetch(query, mp, "newlyListed")
    pool = {}
    for it in a + n_:
        pool.setdefault(it["itemId"], it)
    # темп надходження: свіжі за 7 днів серед «найновіших 200»
    fresh_new = [it for it in n_ if created(it) and (NOW - created(it)) <= timedelta(days=7)]
    span_days = None
    cs = [created(it) for it in n_ if created(it)]
    if len(n_) >= 200 and cs:
        span_days = max(0.1, (NOW - min(cs)).total_seconds() / 86400)
    arrivals_week = (200 / span_days * 7) if span_days else len(fresh_new)
    rows = []
    for it in pool.values():
        if quality.listing_flags(it):
            continue
        r = identity.describe(it["title"], cat)
        if r.exclude:
            continue
        tp = total_price(it)
        if tp is None or tp < mp:
            continue
        key = r.key
        if mode == "lego":
            m = LEGO_RX.search(it["title"])
            if not m:
                continue
            key = "lego|" + m.group(1)
        rows.append((key, tp, (it.get("seller") or {}).get("username") or "?", it, r))
    by = defaultdict(list)
    for row in rows:
        by[row[0]].append(row)
    ev = fresh = fresh_ev = relaxed = strict = tail = tail_base = 0
    tail_savings, examples = [], []
    for k, rr in by.items():
        for (_k, tp, s, it, r) in rr:
            c = created(it)
            age = (NOW - c).days if c else 9999
            comps = [(p, ss, i["itemId"]) for (_kk, p, ss, i, _r) in rr if i["itemId"] != it["itemId"]]
            v = quality.assess(tp, comps, s)
            evaluable = "thin-ref" not in v.reasons
            ev += evaluable
            if evaluable and age <= 60:
                tail_base += 1
                if v.ratio is not None and v.ratio <= 0.75 and v.saving >= 15:
                    tail += 1
                    tail_savings.append(v.saving)
            if age <= 7:
                fresh += 1
                if evaluable:
                    fresh_ev += 1
                    if v.ratio <= 0.75 and v.saving >= 15 and not (set(v.reasons) & {"blended-ref", "weak-ref"}):
                        relaxed += 1
                        examples.append((tp, v.median, it["title"][:60], age))
                    if v.ok:
                        strict += 1
    n = len(rows)
    med_price = statistics.median([r[1] for r in rows]) if rows else 0
    return dict(name=niche, total=total, pool=len(pool), elig=n, ev=ev, evshare=(ev / n * 100 if n else 0),
                fresh=fresh, fresh_ev=fresh_ev, arrivals=arrivals_week, relaxed=relaxed, strict=strict,
                tail=(tail / tail_base * 100 if tail_base else 0), tail_n=tail, med_price=med_price,
                med_saving=(statistics.median(tail_savings) if tail_savings else 0), ex=examples[:2])


out = []
for niche, q, mp, mode in NICHES:
    try:
        out.append(run(niche, q, mp, mode))
    except Exception as exc:
        print("ERR", niche, exc)
    time.sleep(0.3)

print(f"{'ніша':<28}{'DE всього':>10}{'нові/тиж':>9}{'придатн.':>9}{'оцінн.%':>8}{'свіжі':>6}{'св.оцін':>8}{'хвіст%':>7}{'знах.св':>8}{'мед.ціна':>9}{'мед.економ':>11}")
for o in sorted(out, key=lambda o: -(o['arrivals'] * o['evshare'] / 100 * o['tail'] / 100)):
    print(f"{o['name']:<28}{o['total']:>10}{o['arrivals']:>9.0f}{o['elig']:>9}{o['evshare']:>7.0f}%{o['fresh']:>6}{o['fresh_ev']:>8}"
          f"{o['tail']:>6.0f}%{o['relaxed']:>8}{o['med_price']:>9.0f}{o['med_saving']:>11.0f}")
json.dump(out, open(sys.argv[1] + "/niche_out.json", "w", encoding="utf-8"), ensure_ascii=False, default=str)
