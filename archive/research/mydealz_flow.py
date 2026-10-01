"""Потік пропозицій mydealz (RSS): скільки за добу, які категорії, які ціни. Без eBay API."""
import re, sys, json, time, html
from collections import Counter
from datetime import datetime
from email.utils import parsedate_to_datetime
import requests
H = {"User-Agent": "Mozilla/5.0 (personal price research)"}
items = []
for page in range(1, int(sys.argv[1]) + 1 if len(sys.argv) > 1 else 9):
    r = requests.get(f"https://www.mydealz.de/rss/new?page={page}", headers=H, timeout=25)
    if r.status_code != 200:
        print("page", page, r.status_code); break
    for raw in re.findall(r"<item>(.*?)</item>", r.text, flags=re.S):
        g = lambda rx: (re.search(rx, raw, flags=re.S) or [None, None])[1]
        items.append(dict(
            cat=g(r"<category><!\[CDATA\[(.*?)\]\]></category>"),
            title=html.unescape(g(r"<title><!\[CDATA\[(.*?)\]\]></title>") or ""),
            merchant=g(r'<pepper:merchant name="(.*?)"'),
            price=g(r'<pepper:merchant[^>]*price="(.*?)"'),
            link=g(r"<link>(.*?)</link>"),
            pub=g(r"<pubDate>(.*?)</pubDate>")))
    time.sleep(1.0)
json.dump(items, open("research/mydealz_new.json", "w", encoding="utf-8"), ensure_ascii=False)
ts = [parsedate_to_datetime(i["pub"]) for i in items if i["pub"]]
print("пропозицій:", len(items), "  період:", min(ts).isoformat()[:16], "→", max(ts).isoformat()[:16],
      f"  ≈ {len(items) / max(0.01, (max(ts) - min(ts)).total_seconds() / 86400):.0f} за добу")
print("з ціною:", sum(1 for i in items if i["price"]))
for c, n in Counter(i["cat"] for i in items).most_common(25):
    print(f"  {n:>4}  {c}")
