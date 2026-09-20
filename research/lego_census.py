"""Перепис LEGO (READ-ONLY): нові лоти по темах -> номери наборів -> для кожного набору
повний зріз DE+AT (кількість лотів, продавців, розкид цін, свіжість)."""
import sys, json, re, time, statistics
from collections import defaultdict
from datetime import datetime, timezone
sys.path.insert(0, r"D:\ebay-bot")
import config
from main import EbayClient, _request_with_backoff, total_price
out_dir = sys.argv[1]
c = EbayClient()
NOW = datetime.now(timezone.utc)
SET_RX = re.compile(r"(?<![\d.,])(\d{5})(?![\d])")
VALID = re.compile(r"^(10|11|21|30|31|40|41|42|43|60|66|71|72|75|76|77|80|85)\d{3}$")

THEMES = ["Icons", "Ideas", "Speed Champions", "Harry Potter", "Star Wars", "Technic", "Botanicals",
          "Creator", "City", "Friends", "Disney", "Marvel", "Architecture", "Jurassic", "Minecraft",
          "Ninjago", "Art", "Modular", "Lord of the Rings", "Super Mario", "DC Batman", "Titanic",
          "Formula 1", "Porsche", "Ferrari", "Bugatti", "Mclaren", "Lamborghini", "Animal Crossing",
          "Sonic", "Wicked", "Avatar", "Zelda", "Pokemon", "Minifiguren Sammelserie", "Dreamzzz",
          "Classic", "Duplo", "Bluey", "Fortnite", "Holiday", "Monkie Kid", "Hidden Side", "Vidiyo",
          "Advent Kalender", "Polybag", "Fahrzeuge", "Haus", "Blumen"]


def search(q, cc, sort=None, mp=15, cat=True):
    flt = config.search_filter(mp) + f",itemLocationCountry:{cc}"
    p = {"q": q, "filter": flt, "limit": 200}
    if cat:
        p["category_ids"] = "19006"
    if sort:
        p["sort"] = sort
    for attempt in range(3):
        try:
            d = _request_with_backoff("GET", config.EBAY_BROWSE_SEARCH_URL, headers=c._headers(), params=p)
            return d.get("itemSummaries") or [], int(d.get("total") or 0)
        except Exception as e:
            time.sleep(2)
    return [], 0


def created(it):
    try:
        return datetime.fromisoformat(it["itemCreationDate"].replace("Z", "+00:00"))
    except Exception:
        return None


# --- 1. виявлення номерів наборів через теми (newlyListed + price) ---
found = defaultdict(int)
p1 = {}
theme_stats = {}
calls = 0
for th in THEMES:
    for sort in ("newlyListed", None):
        its, total = search("LEGO " + th, "DE", sort)
        calls += 1
        if sort == "newlyListed":
            cs = [created(i) for i in its if created(i)]
            span = (NOW - min(cs)).total_seconds() / 86400 if len(its) >= 200 and cs else None
            theme_stats[th] = dict(total=total, span_days=span, n=len(its))
        for it in its:
            for m in set(SET_RX.findall(it["title"])):
                if VALID.match(m):
                    p1.setdefault(m, {})[it["itemId"]] = it["title"]
    time.sleep(0.15)
for m, d_ in p1.items():
    found[m] = len(d_)
print("themes done, calls", calls, "sets found", len(found), flush=True)

# --- 2. повний зріз для кожного набору, що зустрівся >=1 раз ---
cand = [s for s, n in sorted(found.items(), key=lambda x: -x[1])[:300]]
print('deep dive', len(cand), flush=True)
data = {}
for i, s in enumerate(sorted(cand)):
    rows = []
    for cc in ("DE", "AT"):
        its, total = search("LEGO " + s, cc, None, mp=8)
        calls += 1
        for it in its:
            t = it["title"]
            if s not in SET_RX.findall(t):
                continue
            rows.append(dict(id=it["itemId"], title=t, tp=total_price(it), price=float(it["price"]["value"]),
                             seller=(it.get("seller") or {}).get("username"), created=it.get("itemCreationDate"),
                             bo="BEST_OFFER" in (it.get("buyingOptions") or []), cc=cc,
                             fb=(it.get("seller") or {}).get("feedbackScore"),
                             pct=(it.get("seller") or {}).get("feedbackPercentage"),
                             mpn=None))
    data[s] = rows
    if i % 20 == 0:
        print(i, len(cand), "calls", calls, flush=True)
        json.dump(dict(themes=theme_stats, found=found, data=data, when=NOW.isoformat(), partial=True),
                  open(out_dir + "/lego_census_partial.json", "w", encoding="utf-8"), ensure_ascii=False)
    time.sleep(0.1)
json.dump(dict(themes=theme_stats, found=found, data=data, when=NOW.isoformat()),
          open(out_dir + "/lego_census.json", "w", encoding="utf-8"), ensure_ascii=False)
print("DONE calls", calls)
