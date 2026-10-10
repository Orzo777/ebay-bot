"""Сторож дефіциту (10.10): раз на тиждень — ціни оголошень eBay.de на ~40 «підозрюваних» поза нашими категоріями.

Закономірність із дослідження 24–26.09: заробіток на перепродажі з'являється лише після нещодавнього різкого стрибка
ціни (Xbox Series X після підвищення Microsoft), поки приватні продавці на KA ще не переписали ціни. Тут — та сама
перевірка постійно: p25 і медіана вживаних оголошень (Browse API, 1 запит на товар), історія в ram_prices.json
(«shortage»), а тижневий звіт показує, що подорожчало більш ніж на 15% за ~2 тижні. Це ціни ОГОЛОШЕНЬ — сигнал
«глянути»; рішення — після реальних продажів (Terapeak) і вибірки KA.
"""
import re
from datetime import date
from statistics import median

# (назва, запит, категорія eBay або None, ціна від, до, обов'язкові слова в назві, виключити)
_ACC = r"defekt|hülle|cover|case\b|tasche|ständer|halterung|skin|folie|kabel|netzteil only|suche|tausch|nur\s+controller"
WATCH = [
    ("PS5 Pro", "ps5 pro konsole", "139971", 450, 2000, ["pro"], _ACC),
    ("Xbox Series S", "xbox series s konsole", "139971", 120, 600, ["series s"], _ACC),
    ("Switch OLED", "nintendo switch oled konsole", "139971", 120, 450, ["oled"], _ACC + r"|switch 2"),
    ("Steam Deck OLED", "steam deck oled", None, 250, 900, ["steam deck", "oled"], _ACC),
    ("Steam Deck LCD", "steam deck", None, 150, 600, ["steam deck"], _ACC + r"|oled"),
    ("ROG Ally", "asus rog ally", None, 250, 900, ["ally"], _ACC),
    ("PlayStation Portal", "playstation portal", None, 120, 450, ["portal"], _ACC),
    ("Meta Quest 3", "meta quest 3", None, 200, 800, ["quest 3"], _ACC + r"|quest 3s"),
    ("RTX 3060 12GB", "rtx 3060 12gb", "27386", 120, 500, ["3060"], r"defekt|ti\b|laptop|pc\b|rechner"),
    ("RTX 4060", "rtx 4060", "27386", 150, 600, ["4060"], r"defekt|\bti\b|laptop|pc\b|rechner"),
    ("RTX 4060 Ti", "rtx 4060 ti", "27386", 200, 800, ["4060", "ti"], r"defekt|laptop|pc\b|rechner"),
    ("RTX 4070", "rtx 4070", "27386", 300, 1000, ["4070"], r"defekt|\bti\b|super|laptop|pc\b|rechner"),
    ("RTX 4070 Super", "rtx 4070 super", "27386", 350, 1100, ["4070", "super"], r"defekt|\bti\b|laptop|pc\b|rechner"),
    ("RTX 3080", "rtx 3080", "27386", 250, 1000, ["3080"], r"defekt|\bti\b|laptop|pc\b|rechner"),
    ("RTX 3090", "rtx 3090", "27386", 500, 2000, ["3090"], r"defekt|\bti\b|laptop|pc\b|rechner"),
    ("RTX 5060 Ti 16GB", "rtx 5060 ti 16gb", "27386", 300, 1000, ["5060", "16"], r"defekt|laptop|pc\b|rechner"),
    ("RTX 5070", "rtx 5070", "27386", 400, 1200, ["5070"], r"defekt|\bti\b|laptop|pc\b|rechner"),
    ("RTX 5070 Ti", "rtx 5070 ti", "27386", 600, 1600, ["5070", "ti"], r"defekt|laptop|pc\b|rechner"),
    ("RX 6700 XT", "rx 6700 xt", "27386", 150, 600, ["6700"], r"defekt|laptop|pc\b|rechner"),
    ("RX 7800 XT", "rx 7800 xt", "27386", 300, 900, ["7800"], r"defekt|laptop|pc\b|rechner"),
    ("RX 9070 XT", "rx 9070 xt", "27386", 450, 1400, ["9070"], r"defekt|laptop|pc\b|rechner"),
    ("Ryzen 7 7800X3D", "ryzen 7 7800x3d", None, 200, 700, ["7800x3d"], r"defekt|pc\b|rechner|bundle|mainboard"),
    ("Ryzen 7 5800X3D", "ryzen 7 5800x3d", None, 150, 600, ["5800x3d"], r"defekt|pc\b|rechner|bundle|mainboard"),
    ("Samsung 990 Pro 2TB", "samsung 990 pro 2tb", None, 80, 450, ["990"], r"defekt|1\s?tb|4\s?tb|500\s?gb|heatsink only"),
    ("WD SN850X 2TB", "wd black sn850x 2tb", None, 80, 450, ["sn850x"], r"defekt|1\s?tb|4\s?tb|500\s?gb"),
    ("NVMe 4TB", "nvme ssd 4tb", None, 150, 700, ["4tb"], r"defekt|hdd|externe?\b|portable"),
    ("iPad 10", "ipad 10. generation 64gb", None, 150, 600, ["ipad"], r"defekt|hülle|case|cover|pro\b|air|mini"),
    ("iPad Air M2", "ipad air m2", None, 300, 1000, ["air"], r"defekt|hülle|case|cover|macbook"),
    ("MacBook Air M1", "macbook air m1", None, 300, 1000, ["m1"], r"defekt|hülle|case|cover|m2|m3"),
    ("MacBook Air M2", "macbook air m2", None, 450, 1300, ["m2"], r"defekt|hülle|case|cover|m1\b|m3"),
    ("Mac mini M4", "mac mini m4", None, 400, 1600, ["mini", "m4"], r"defekt|hülle|case|cover"),
    ("Raspberry Pi 5 8GB", "raspberry pi 5 8gb", None, 50, 250, ["pi 5"], r"defekt|gehäuse only|case only"),
    ("Synology DS224+", "synology ds224+", None, 200, 700, ["ds224"], r"defekt"),
    ("AirPods Pro 2", "airpods pro 2", None, 80, 300, ["airpods pro"], r"defekt|case only|nur case|einzeln|links|rechts|ersatz"),
    ("DJI Mini 4 Pro", "dji mini 4 pro", None, 350, 1300, ["mini 4"], r"defekt|akku only|propeller"),
    ("GoPro Hero 12", "gopro hero 12 black", None, 150, 600, ["gopro"], r"defekt|halterung|akku\b|case|gehäuse"),
    ("Toniebox", "toniebox", None, 30, 150, ["toniebox"], r"defekt|tonie figur|nur figuren"),
    ("Dyson V15", "dyson v15", None, 150, 700, ["v15"], r"defekt|akku only|zubehör|aufsatz"),
]
EVERY_DAYS, KEEP, RISE, MIN_N = 6, 26, 0.15, 6


def measure(fetch, entry) -> dict | None:
    """→ {p25, med, n} з уживаних оголошень (лише ті, де назва містить усі обов'язкові слова і без виключень)."""
    name, q, cat, lo, hi, must, excl = entry
    rows = fetch(q, "3000", cat)
    ex = re.compile(excl, re.I)
    ps = sorted(r["total"] for r in rows
                if lo <= r["total"] <= hi and all(m in r["title"].lower() for m in must) and not ex.search(r["title"]))
    if len(ps) < MIN_N:
        return None
    return {"p25": round(ps[len(ps) // 4]), "med": round(median(ps)), "n": len(ps)}


def update(data: dict, fetch, today: str) -> int:
    """Раз на EVERY_DAYS днів — новий замір усіх товарів у data["shortage"]. → скільки товарів заміряно (0 — не час)."""
    sh = data.setdefault("shortage", {"last": "", "hist": {}})
    if sh.get("last") and (date.fromisoformat(today) - date.fromisoformat(sh["last"])).days < EVERY_DAYS:
        return 0
    n = 0
    for e in WATCH:
        try:
            m = measure(fetch, e)
        except Exception as ex:   # один товар не валить замір
            print(f"дефіцит {e[0]}: {ex.__class__.__name__}")
            continue
        if not m:
            continue
        h = sh["hist"].setdefault(e[0], [])
        h.append([today, m["p25"], m["med"], m["n"]])
        del h[:-KEEP]
        n += 1
    sh["last"] = today
    return n


def risers(data: dict, today: str, min_days: int = 10, max_days: int = 24, th: float = RISE) -> list[tuple]:
    """→ [(назва, +частка, p25 зараз, p25 тоді, днів)] — p25 оголошень виріс ≥ th за 10–24 дні (найстаріший замір у вікні)."""
    out, t = [], date.fromisoformat(today)
    for name, h in ((data.get("shortage") or {}).get("hist") or {}).items():
        if len(h) < 2:
            continue
        now = h[-1]
        old = [x for x in h[:-1] if min_days <= (t - date.fromisoformat(x[0])).days <= max_days]
        if not old:
            continue
        then = old[0]
        if then[1] and (now[1] - then[1]) / then[1] >= th:
            out.append((name, (now[1] - then[1]) / then[1], now[1], then[1], (t - date.fromisoformat(then[0])).days))
    return sorted(out, key=lambda x: -x[1])


def lines(data: dict, today: str) -> list[str]:
    sh = data.get("shortage") or {}
    if not sh.get("hist"):
        return []
    first = min((h[0][0] for h in sh["hist"].values() if h), default=today)
    if (date.fromisoformat(today) - date.fromisoformat(first)).days < 10:
        return [f"📈 <b>Сторож дефіциту</b>: збирає базу цін ({len(sh['hist'])} товарів, перший замір {first}); порівняння — з ~2 тижнів."]
    r = risers(data, today)
    if not r:
        return ["📈 <b>Сторож дефіциту</b>: різких стрибків цін за 2 тижні немає (стежу за ~40 товарами поза RAM і консолями)."]
    return (["📈 <b>Дорожчає</b> (p25 вживаних оголошень eBay.de за ~2 тижні) — можливе вікно, поки KA не переписав ціни:"]
            + [f"• {n}: <b>+{100 * p:.0f}%</b> ({then} → {now} € за {d} дн.)" for n, p, now, then, d in r[:6]]
            + ["Перевірити реальні продажі (Terapeak) і KA — скажи Claude."])
