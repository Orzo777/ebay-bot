"""Межі ціни («Preis bis») підписок Kleinanzeigen (10.10).

Ціни ринку оновлюються щодня (price_refresh), а межі в підписках KA користувач ставить вручну — і вони відстають:
дорожчі вигідні оголошення в пошту не приходять, або навпаки йде зайве. Тут — рекомендована межа для кожної підписки
(найвища ціна, за якої бот ще скаже «торгуйся», тим самим оцінювачем, що й картки) і порівняння з поточною.
Тижневий звіт показує розбіжності > 5%; користувач міняє межі в KA і тисне «✅ Межі оновив» → ka_bounds.yml →
`--accept` записує рекомендовані як поточні в research/ka_bounds.json (ram_mail_check бере межі звідти).

    python research/ka_bounds.py            # показати розбіжності
    python research/ka_bounds.py --accept   # записати рекомендовані як поточні (+ повідомлення в бот)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import console_alert as ca
import ram_alert as ra
import ram_mail_check as rmc

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ka_bounds.json")
DRIFT = 0.05
RAM_TITLES = {   # підписка → типове оголошення, за яким рахуємо межу
    "arbeitsspeicher ddr5": "DDR5 16GB 6000 RAM", "ddr5 16gb": "DDR5 16GB 6000 RAM",
    "ddr5 sodimm 16gb": "DDR5 SO-DIMM 16GB 5600 Laptop RAM", "ddr5 sodimm 32gb": "DDR5 SO-DIMM 32GB (2x16GB) 5600 Laptop",
    "ddr5 2x16gb": "Corsair Vengeance DDR5 32GB (2x16GB) 6000", "ddr5 32gb": "Corsair Vengeance DDR5 32GB (2x16GB) 6000",
    "ddr5 2x32gb": "Corsair Vengeance DDR5 64GB (2x32GB) 6000",
    "ddr5 64gb": ["Corsair Vengeance DDR5 64GB (2x32GB) 6000", "Corsair Vengeance DDR5 64GB (4x16GB) 6000"],
    "ddr4 2x16gb": "Corsair Vengeance DDR4 32GB (2x16GB) 3200", "ddr4 32gb": "Corsair Vengeance DDR4 32GB (2x16GB) 3200",
    "ddr4 sodimm 32gb": "DDR4 SO-DIMM 32GB (2x16GB) 3200 Laptop",
    "ddr4 64gb": ["DDR4 64GB (2x32GB) 3200 RAM", "Corsair Vengeance DDR4 64GB (4x16GB) 3200"],   # 10.10: і набори 4×16
    "ddr5 48gb": "DDR5 48GB (2x24GB) 6000 RAM"}
CONSOLE_TITLES = {"xbox series x": "Xbox Series X 1TB Konsole", "xbox series": "Xbox Series X 1TB Konsole",
                  "ps5": "Sony PS5 Disc Edition Konsole", "playstation 5": "Sony PS5 Disc Edition Konsole",
                  "switch 2": "Nintendo Switch 2 Konsole"}


def _top(fn, lo: int, hi: int) -> int | None:
    best = None
    for p in range(lo, hi):
        r = fn(p)
        if r and r.get("verdict") not in ("SKIP", "UNKNOWN", None):
            best = p
    return best


def _pickup(med: float, costs: float) -> int:   # стеля «торгуйся» без пересилки (10.10: від медіани, ra.caps_from)
    return int(ra.caps_from(med - costs)[0] * ra.NEGOTIATE_UP - ra.PICKUP_COST)


def recommended() -> dict:
    cur = current()
    ram = {k: max((_top(lambda p, t=t: ra.evaluate(t, p, vb=True), 10, int(cur["ram"].get(k, 300) * 1.6) + 60) or 0)
                  for t in (ts if isinstance(ts, list) else [ts])) or None for k, ts in RAM_TITLES.items()}
    con = {k: _top(lambda p, t=t: ca.evaluate_console(t, p, vb=True), 150, int(cur["console"].get(k, 400) * 1.6) + 60)
           for k, t in CONSOLE_TITLES.items()}
    def med(k, v):   # фірмовий сегмент (вищий) — межа підписки не має відрізати фірмові набори
        sg = ra.SEGMENT.get(k)
        return v["med"] * (sg["brand"][1] if sg else 1)
    ham = {g: max(_pickup(med(k, v), ra.costs(med(k, v))) for k, v in ra.REAL.items() if k[0] == g) for g in ("ddr5", "ddr4")}
    ham.update(xbox=_pickup(ca.XBOX_SERIES_X["med"], ca.costs(ca.XBOX_SERIES_X["med"])),
               ps5=_pickup(ca.PS5_DISC["med"], ca.costs(ca.PS5_DISC["med"])),
               switch2=_pickup(ca.SWITCH2["med"], ca.costs(ca.SWITCH2["med"], ca.SHIP_SWITCH)))
    return {"ram": {k: v for k, v in ram.items() if v}, "console": {k: v for k, v in con.items() if v}, "hamburg": ham}


def current() -> dict:
    return {"ram": {k: hi for k, (lo, hi) in rmc._RAM_SEARCH.items()}, "console": dict(rmc.CONSOLE_MAX), "hamburg": dict(rmc.HAMBURG_MAX)}


LABEL = {"ram": "{}", "console": "{}", "hamburg": "{} in Hamburg"}
HAM_NAME = {"ddr5": "ddr5", "ddr4": "ddr4", "xbox": "xbox series x / xbox series", "ps5": "ps5 / playstation 5", "switch2": "switch 2"}


def drift(rec: dict | None = None, cur: dict | None = None, th: float = DRIFT) -> list[tuple]:
    rec, cur = rec or recommended(), cur or current()
    out = []
    for g in ("ram", "console", "hamburg"):
        for k, v in rec[g].items():
            c = cur[g].get(k)
            if c and abs(v - c) / c > th:
                out.append((g, k, c, v))
    return out


def lines(rec: dict | None = None, cur: dict | None = None) -> list[str]:
    d = drift(rec, cur)
    if not d:
        return []
    return (["🔎 <b>Межі підписок KA</b> — ціни ринку змінились більш ніж на 5%, зміни «Preis bis» у KA:"]
            + [f"• {LABEL[g].format(HAM_NAME.get(k, k) if g == 'hamburg' else k)}: {c} → <b>{v} €</b>" for g, k, c, v in d]
            + ["Змінив у KA — натисни «✅ Межі оновив» (бот запам'ятає нові)."])


def show_text(rec: dict | None = None, cur: dict | None = None) -> tuple[str, dict | None]:
    """Команда «межі»: усі підписки — що змінити (→) і що лишити (✓); кнопка — якщо є що міняти."""
    rec, cur = rec or recommended(), cur or current()
    d = {(g, k) for g, k, _, _ in drift(rec, cur)}
    rows = [f"• {LABEL[g].format(HAM_NAME.get(k, k) if g == 'hamburg' else k)}: "
            + (f"{cur[g].get(k)} → <b>{v} €</b>" if (g, k) in d else f"{cur[g].get(k) or v} € ✓")
            for g in ("ram", "console", "hamburg") for k, v in rec[g].items()]
    text = "🔎 <b>«Preis bis» для підписок KA</b> за сьогоднішніми цінами (→ змінити, ✓ як є):\n" + "\n".join(rows)
    return text, ({"inline_keyboard": [[{"text": "✅ Межі в KA оновив", "callback_data": "c|межі|1"}]]} if d else None)


def accept(rec: dict | None = None) -> dict:
    rec = rec or recommended()
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1, sort_keys=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accept", action="store_true")
    ap.add_argument("--show", action="store_true", help="усі межі за сьогоднішніми цінами — у бот (команда «межі»)")
    a = ap.parse_args()
    if a.show:
        import sell
        text, kb = show_text()
        sell.office_send_html(text, kb)
        return
    if not a.accept:
        print("\n".join(lines()) or "межі в порядку")
        return
    before = current()
    rec = accept()
    changed = drift(rec, before, th=0)
    import sell
    sell.office_send_html("✅ Межі підписок KA записав" + (f" ({len(changed)} змінено)" if changed else " (без змін)") +
                          ". Наступний тижневий звіт порівнюватиме з ними.", None)
    print(f"межі записано: {len(changed)} змінено")


if __name__ == "__main__":
    main()
