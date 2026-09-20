"""Зонд «техніка 7%»: для ~36 моделей — потік нових лотів/тиж, оцінність, скільки лотів пройшло б
fee-aware поріг (ціна <= 85% медіани, економія >= €20) з тими ж перевірками quality.assess.
READ-ONLY, ~150 викликів, лог у файл."""
import sys, json, re, time, types
from collections import defaultdict
from datetime import datetime, timezone
sys.path.insert(0, r"D:\ebay-bot")
import config, identity, quality
from main import EbayClient, _request_with_backoff, total_price
SP = sys.argv[1]
c = EbayClient()
NOW = datetime.now(timezone.utc)

# (запит, must-групи, мін.ціна)
SKUS = [
    ("Sony WH-1000XM4", [("sony",), ("1000xm4", "xm4")], 90),
    ("Sony WH-1000XM5", [("sony",), ("1000xm5", "xm5")], 130),
    ("Sony WF-1000XM5", [("sony",), ("wf1000xm5", "xm5")], 100),
    ("Bose QuietComfort Ultra Headphones", [("bose",), ("ultra",), ("headphones", "kopfhoerer")], 200),
    ("Bose QuietComfort 45", [("bose",), ("45", "qc45")], 130),
    ("Sennheiser Momentum 4", [("sennheiser",), ("momentum",), ("4",)], 130),
    ("Sonos Era 100", [("sonos",), ("era",), ("100",)], 100),
    ("Sonos Beam Gen 2", [("sonos",), ("beam",)], 200),
    ("Sonos Roam 2", [("sonos",), ("roam",)], 90),
    ("Sonos Ace", [("sonos",), ("ace",)], 200),
    ("Jabra Elite 10", [("jabra",), ("elite",), ("10",)], 90),
    ("JBL Charge 6", [("jbl",), ("charge",), ("6",)], 100),
    ("Steam Deck OLED 512GB", [("steam",), ("deck",), ("oled",), ("512gb", "512")], 380),
    ("Nintendo Switch OLED", [("nintendo",), ("oled",), ("switch",)], 190),
    ("Nintendo Switch 2", [("nintendo",), ("switch",), ("2",)], 380),
    ("PlayStation 5 Slim Disc", [("playstation", "ps5"), ("slim",)], 300),
    ("PlayStation Portal", [("playstation", "ps"), ("portal",)], 150),
    ("Xbox Series X", [("xbox",), ("series",), ("x",)], 350),
    ("Meta Quest 3S", [("meta", "oculus"), ("quest",), ("3s",)], 220),
    ("AVM FRITZ!Box 7590 AX", [("fritz", "avm"), ("7590",)], 100),
    ("AVM FRITZ!Box 7690", [("fritz", "avm"), ("7690",)], 150),
    ("AVM FRITZ!Box 6690 Cable", [("fritz", "avm"), ("6690",)], 150),
    ("Synology DiskStation DS224+", [("synology",), ("ds224",)], 200),
    ("Ubiquiti UniFi U6 Pro", [("unifi", "ubiquiti"), ("u6",), ("pro",)], 100),
    ("Raspberry Pi 5 8GB", [("raspberry",), ("5",), ("8gb", "8")], 60),
    ("Logitech MX Master 3S", [("logitech",), ("master",), ("3s",)], 55),
    ("Logitech MX Keys S", [("logitech",), ("mx",), ("keys",)], 60),
    ("Apple iPad 10. Generation 64GB", [("ipad",), ("10",)], 250),
    ("Apple iPad Air M2", [("ipad",), ("air",), ("m2",)], 450),
    ("Garmin Forerunner 265", [("garmin",), ("forerunner",), ("265",)], 200),
    ("Garmin Forerunner 965", [("garmin",), ("forerunner",), ("965",)], 300),
    ("Kindle Paperwhite 16GB 12. Generation", [("kindle",), ("paperwhite",)], 110),
    ("Kindle Scribe", [("kindle",), ("scribe",)], 250),
    ("GoPro HERO13 Black", [("gopro",), ("hero13", "hero", "13")], 250),
    ("DJI Osmo Action 5 Pro", [("dji",), ("osmo",), ("action",), ("5",)], 230),
    ("Insta360 X4", [("insta360",), ("x4",)], 350),
]
EXTRA_DENY = ["folie", "schutz", "tasche", "huelle", "displayschutz", "netzteil", "ladegeraet", "ladekabel",
              "fernbedienung", "controller", "gamepad", "dock", "docking", "spiel", "game", "games",
              "bundle", "set", "kit", "zubehoer", "akku", "batterie", "ersatzteil", "reparatur", "defekt",
              "box only", "ohne", "ersatz", "bastler"]
for q, must, mp in SKUS:
    strip = identity._fs(*[t for t in identity.norm(q).split() if len(t) > 1])
    identity.PROFILES[q] = identity._mk(identity._slug(q) or "gen", identity._DEFAULT, must=must,
        deny=identity._ELEC_DENY | identity._fs(*EXTRA_DENY), allow_variants=[], strip=strip, plus_policy="exclude")

calls = 0


def fetch(q, mp, cc, sort=None):
    global calls
    flt = f"buyingOptions:{{FIXED_PRICE}},conditionIds:{{2750|3000|4000|5000}},price:[{mp}..],priceCurrency:EUR,itemLocationCountry:{cc}"
    p = {"q": q, "filter": flt, "limit": 200}
    if sort:
        p["sort"] = sort
    calls += 1
    for a in range(3):
        try:
            d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=c._headers(), params=p)
            return d.get("itemSummaries") or [], int(d.get("total") or 0)
        except Exception:
            time.sleep(2)
    return [], 0


def created(it):
    try:
        return datetime.fromisoformat(it["itemCreationDate"].replace("Z", "+00:00"))
    except Exception:
        return None


def cfg_with(thr, ms):
    d = {k: getattr(config, k) for k in dir(config) if k.isupper()}
    d["ANOMALY_THRESHOLD"] = thr
    d["QUALITY_MIN_SAVING"] = ms
    return types.SimpleNamespace(**d)


CF = {"strict": cfg_with(0.60, 25), "mid": cfg_with(0.75, 20), "fee": cfg_with(0.85, 20)}
out = []
for q, must, mp in SKUS:
    mp = int(mp * 0.45)
    cat = {"query": q, "min_price": mp}
    pool = {}
    tot_de = 0
    arrivals = None
    for cc in ("DE", "AT"):
        a, total = fetch(q, mp, cc)
        b, _ = fetch(q, mp, cc, "newlyListed")
        if cc == "DE":
            tot_de = total
            cs = [created(i) for i in b if created(i)]
            if len(b) >= 200 and cs:
                arrivals = 200 / max(0.1, (NOW - min(cs)).total_seconds() / 86400) * 7
            else:
                arrivals = sum(1 for x in cs if (NOW - x).days < 7)
        for it in list(a) + list(b):
            pool.setdefault(it["itemId"], it)
        time.sleep(0.1)
    rows = []
    for it in pool.values():
        if [f for f in quality.listing_flags(it) if not f.startswith("cond:")]:
            continue
        r = identity.describe(it["title"], cat)
        tp = total_price(it)
        if r.exclude or tp is None or tp < mp:
            continue
        rows.append((r.key, tp, (it.get("seller") or {}).get("username") or "?", it, r))
    by = defaultdict(list)
    for row in rows:
        by[row[0]].append(row)
    ev = 0
    hits = {k: [] for k in CF}
    for k, rr in by.items():
        for (_k, tp, s, it, r) in rr:
            comps = [(p, ss, i["itemId"]) for (_kk, p, ss, i, _r) in rr if i["itemId"] != it["itemId"]]
            v0 = quality.assess(tp, comps, s, cfg=CF["strict"])
            if "thin-ref" not in v0.reasons:
                ev += 1
            cr = created(it)
            age = (NOW - cr).days if cr else 9999
            for name, cfg in CF.items():
                v = quality.assess(tp, comps, s, cfg=cfg)
                if v.ok:
                    hits[name].append((tp, v.median, age, it["title"][:60]))
    n = len(rows)
    row = dict(q=q, total_de=tot_de, arrivals=arrivals, elig=n, ev=ev, evshare=ev / n * 100 if n else 0,
               hits={k: v for k, v in hits.items()})
    out.append(row)
    print(f"{q[:36]:36} DE={tot_de:>5} нові/тиж={arrivals or 0:>5.0f} придатн={n:>4} оцінн={row['evshare']:>3.0f}% "
          f"ok(0.60)={len(hits['strict'])} ok(0.75)={len(hits['mid'])} ok(0.85)={len(hits['fee'])} calls={calls}", flush=True)
    json.dump(out, open(SP + "/tech_probe_used.json", "w", encoding="utf-8"), ensure_ascii=False)
print("DONE", calls, flush=True)
