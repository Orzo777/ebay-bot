"""Наскільки eBay-ціна «гарячих» запечатаних Pokémon-товарів перевищує роздрібну (RRP)?
READ-ONLY, ~6 викликів. Групуємо лоти за ключовими словами сету/типу і показуємо медіану."""
import re
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, ".")
import check

f = check.Fetcher()
QUERIES = ["Pokemon Top Trainer Box Deutsch", "Pokemon Elite Trainer Box Deutsch", "Pokemon Booster Bundle Deutsch",
           "Pokemon Sammelkartenspiel Kollektion Deutsch"]
GENERIC = set("pokemon pokémon top trainer box deutsch tcg neu ovp versiegelt sealed elite etb karten sammelkarten booster "
              "deutsche version sammelkartenspiel original kollektion bundle display new und mit von der die das in im "
              "fuer für 9 18 pack packs karte kartenspiel spiel edition trading card cards".split())
rows = []
for q in QUERIES:
    its, total = f.search(q=q, min_price=25)
    for it in its:
        t = it["title"]
        p = check._total_price(it)
        if p and 25 <= p <= 400 and not re.search(r"\b(leer|leere|einzel|proxy|custom|graded|psa|bgs|lot|konvolut|2x|3x|4x)\b", t, re.I):
            toks = [w for w in re.split(r"[^A-Za-zÄÖÜäöüß0-9]+", t.lower()) if len(w) >= 4 and w not in GENERIC]
            rows.append((q, t, p, toks))
    print(q, "→ лотів:", len(its), "всього eBay:", total)
cnt = Counter(w for _, _, _, toks in rows for w in set(toks))
common = [w for w, n in cnt.most_common(40) if n >= 4][:14]
print("\nключові слова (найчастіші у назвах):", common)
print(f"\n{'слово(сет/тип)':16}{'лотів':>6}{'мін':>7}{'5 найдешевших':>15}{'медіана':>9}")
for w in common:
    ps = sorted(p for _, _, p, toks in rows if w in toks)
    if len(ps) >= 4:
        print(f"{w:16}{len(ps):>6}{ps[0]:>7.0f}{statistics.median(ps[:5]):>15.0f}{statistics.median(ps):>9.0f}")
