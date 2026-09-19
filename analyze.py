"""Аналіз стану (READ-ONLY): що з ключами «готові» і звіт розвідки лише за
«зрілий» період. Нічого не пише в price_history.json / discovery_log.jsonl.

    python analyze.py [--since YYYY-MM-DD]

Частина A — перевірка «готових» ключів (>= MIN_HISTORY_POINTS точок у вікні
HISTORY_WINDOW_DAYS): відновлення показника за минулі дати з дат точок
(точки не видаляються, вікно рахується фільтром по даті), розбивка на
чинні/прибрані категорії, хто «випав» із готових, гістограма точок за датами.

Частина B — звіт розвідки без «застарілих UNKNOWN»: тир ліквідності
записується в рядок логу в момент створення й далі не оновлюється, тому
ранні UNK-рядки назавжди тягнуть частку OK+MED донизу. Тут кандидати
беруться лише після «дозрівання» категорії (старт + MIN_LIQUIDITY_TRACK_DAYS)
і, окремо, лише після фіксованої дати --since.
"""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

import config
from main import HistoryStore, _load_discovery_rows


def _d(s):
    return date.fromisoformat(s[:10])


def ready_asof(store, asof, days, minpts):
    """{key: n_points_in_window} для ключів, що існували на дату asof."""
    lo = (asof - timedelta(days=days)).isoformat()
    hi = asof.isoformat()
    out = {}
    for key, pts in store.history.items():
        if not pts or min(p["date"] for p in pts) > hi:
            continue                              # ключа ще не існувало
        out[key] = sum(1 for p in pts if lo <= p["date"] <= hi)
    return out


def part_a(store, today):
    W, M = config.HISTORY_WINDOW_DAYS, config.MIN_HISTORY_POINTS
    active_q = {c["query"] for c in config.CATEGORIES}
    print("═══ A. ПЕРЕВІРКА «ГОТОВИХ» КЛЮЧІВ ═══")
    print(f"готовий = >= {M} точок у вікні {W} дн.; точка = унікальний лістинг "
          f"ключа (дедуп за item_id), датована днем першого бачення\n")

    print("A1. Відновлена динаміка (усі ключі; лог прогону писав 17.09 → 931, "
          "19.09 → 464):")
    print(f"  {'дата':<11}{'ключів':>8}{'готові':>8}{'з них чинні':>13}"
          f"{'з них прибрані':>16}")
    for back in range(11, -1, -1):
        asof = today - timedelta(days=back)
        if asof < date(2026, 9, 6):
            continue
        c = ready_asof(store, asof, W, M)
        rd = [k for k, n in c.items() if n >= M]
        act = sum(1 for k in rd if store.key_query.get(k, "?") in active_q)
        print(f"  {asof.isoformat():<11}{len(c):>8}{len(rd):>8}{act:>13}"
              f"{len(rd) - act:>16}")

    print("\nA2. Точки за датою запису (сплеск першого проходу після скидання "
          "кешу → вихід з вікна):")
    cnt = Counter(p["date"] for pts in store.history.values() for p in pts)
    for d_, n in sorted(cnt.items()):
        if d_ >= "2026-09-03":
            print(f"  {d_}  {n:>6}  {'█' * max(1, n // 250)}")

    print("\nA3. Хто випав із «готових» за останні 2 доби "
          f"({(today - timedelta(days=2)).isoformat()} → {today.isoformat()}):")
    r_then = {k for k, n in ready_asof(store, today - timedelta(days=2), W, M).items()
              if n >= M}
    r_now = {k for k, n in ready_asof(store, today, W, M).items() if n >= M}
    lost, gained = r_then - r_now, r_now - r_then
    lost_act = [k for k in lost if store.key_query.get(k, "?") in active_q]
    print(f"  випало: {len(lost)}  (з них у чинних категоріях: {len(lost_act)}, "
          f"у прибраних: {len(lost) - len(lost_act)});  додалось: {len(gained)}")
    byq = Counter(store.key_query.get(k, "?") for k in lost)
    for q, n in byq.most_common(10):
        tag = "чинна" if q in active_q else "прибрана"
        print(f"    {n:>5}  {q}  [{tag}]")

    print("\nA4. Чинні категорії: ключі та дозрівання")
    print(f"  {'категорія':<44}{'ключів':>7}{'готові':>7}{'накопич.':>9}"
          f"{'2 дні тому':>11}{'ліквід ≥'+str(config.MIN_LIQUIDITY_TRACK_DAYS)+'д':>10}")
    now_c = ready_asof(store, today, W, M)
    then_c = ready_asof(store, today - timedelta(days=2), W, M)
    by_q = defaultdict(list)
    for k in store.history:
        by_q[store.key_query.get(k, "?")].append(k)
    liq_cut = (today - timedelta(days=config.MIN_LIQUIDITY_TRACK_DAYS)).isoformat()
    for cat in config.CATEGORIES:
        q = cat["query"]
        ks = by_q.get(q, [])
        rd = sum(1 for k in ks if now_c.get(k, 0) >= M)
        rd_then = sum(1 for k in ks if then_c.get(k, 0) >= M)
        liq = 0
        for k in ks:
            firsts = [e["first_seen"] for e in store.listings.get(k, {}).values()]
            if firsts and min(firsts) <= liq_cut:
                liq += 1
        print(f"  {q:<44}{len(ks):>7}{rd:>7}{len(ks) - rd:>9}{rd_then:>11}{liq:>10}")
    dead = [k for k in store.history if store.key_query.get(k, "?") not in active_q]
    rd_dead = sum(1 for k in dead if now_c.get(k, 0) >= M)
    print(f"  {'(прибрані категорії, разом)':<44}{len(dead):>7}{rd_dead:>7}"
          f"{len(dead) - rd_dead:>9}")
    print("  * key_query = запит, під яким ключ УПЕРШЕ побачили; ключ, що "
          "перетинається з іншою категорією, рахується за першою.")

    print("\nA5. Чутливість «готових» до вікна історії + прогноз (чинні категорії)")
    print("    W = HISTORY_WINDOW_DAYS. Стовпці 23/24.09 — лише з уже записаних "
          "точок, без НОВИХ лістингів (нижня межа).")
    cols = [("сьогодні W=14", today, 14), ("W=21", today, 21), ("W=30", today, 30),
            ("23.09 W=14", date(2026, 9, 23), 14), ("24.09 W=14", date(2026, 9, 24), 14)]
    snaps = [ready_asof(store, a, w, M) for _, a, w in cols]
    print(f"  {'категорія':<44}" + "".join(f"{n:>15}" for n, _, _ in cols))
    tot = [0] * len(cols)
    for cat in config.CATEGORIES:
        ks = by_q.get(cat["query"], [])
        vals = [sum(1 for k in ks if s.get(k, 0) >= M) for s in snaps]
        tot = [a + b for a, b in zip(tot, vals)]
        print(f"  {cat['query']:<44}" + "".join(f"{v:>15}" for v in vals))
    print(f"  {'РАЗОМ (чинні)':<44}" + "".join(f"{v:>15}" for v in tot))


def part_b(store, today, since):
    rows = _load_discovery_rows()
    W = config.MIN_LIQUIDITY_TRACK_DAYS
    active = [c["query"] for c in config.CATEGORIES]
    start = {}
    for k, pts in store.history.items():
        q = store.key_query.get(k)
        if q and pts:
            d0 = min(p["date"] for p in pts)
            if q not in start or d0 < start[q]:
                start[q] = d0

    def agg(sel):
        tiers = Counter(r.get("liquidity_tier", "UNKNOWN") for r in sel)
        n = len(sel)
        share = (tiers["OK"] + tiers["MEDIUM"]) / n if n else 0.0
        return n, tiers, share

    print("\n═══ B1. ЗВІТ ЛИШЕ ЗА «ЗРІЛИЙ» ПЕРІОД (кандидати після старт + "
          f"{W} дн. для кожної категорії) ═══")
    print("Тир ліквідності стоїть у рядку з моменту створення, тому UNK-рядки "
          "з періоду до дозрівання виключено.")
    hdr = (f"  {'категорія':<44}{'старт':>11}{'зріла з':>11}{'дн.':>4}"
           f"{'канд':>5}{'OK':>4}{'MED':>4}{'LOW':>4}{'UNK':>4}"
           f"{'OK+MED':>8}{'/добу':>7}{'score*':>8}{'всього':>7}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    out = []
    for q in active:
        s0 = start.get(q)
        if not s0:
            out.append((q, None))
            continue
        mat = _d(s0) + timedelta(days=W)
        sel = [r for r in rows if r.get("category") == q
               and _d(r.get("date", "1970-01-01")) >= mat]
        n, t, share = agg(sel)
        span = max(1, (today - mat).days + 1) if mat <= today else 0
        per_week = n / (max(7, span) / 7) if span else 0.0
        score = per_week * share
        total = sum(1 for r in rows if r.get("category") == q)
        out.append((q, (s0, mat, span, n, t, share, score, total)))
    out.sort(key=lambda x: (x[1][6] if x[1] else -1, x[1][3] if x[1] else 0),
             reverse=True)
    for q, v in out:
        if v is None:
            print(f"  {q:<44}{'— немає ключів':>11}")
            continue
        s0, mat, span, n, t, share, score, total = v
        perday = (n / span) if span else 0.0
        note = "" if mat <= today else "  (ще не дозріла)"
        print(f"  {q:<44}{s0:>11}{mat.isoformat():>11}{span:>4}{n:>5}"
              f"{t['OK']:>4}{t['MEDIUM']:>4}{t['LOW']:>4}{t['UNKNOWN']:>4}"
              f"{share * 100:>7.0f}%{perday:>7.2f}{score:>8.2f}{total:>7}{note}")
    print("  * score* = (кандидатів/тиждень, база >= 7 дн.) × частка OK+MED — "
          "формула основного звіту; дн. = від дозрівання до сьогодні.")

    print(f"\n═══ B2. ФІКСОВАНЕ ВІКНО: кандидати з {since.isoformat()} ═══")
    sel_all = [r for r in rows if _d(r.get("date", "1970-01-01")) >= since]
    print(f"  {'категорія':<44}{'канд':>5}{'OK':>4}{'MED':>4}{'LOW':>4}"
          f"{'UNK':>4}{'OK+MED':>8}")
    for q in active:
        sel = [r for r in sel_all if r.get("category") == q]
        n, t, share = agg(sel)
        if n:
            print(f"  {q:<44}{n:>5}{t['OK']:>4}{t['MEDIUM']:>4}{t['LOW']:>4}"
                  f"{t['UNKNOWN']:>4}{share * 100:>7.0f}%")
    n, t, share = agg([r for r in sel_all if r.get("category") in set(active)])
    print(f"  {'РАЗОМ (чинні)':<44}{n:>5}{t['OK']:>4}{t['MEDIUM']:>4}"
          f"{t['LOW']:>4}{t['UNKNOWN']:>4}{share * 100:>7.0f}%")

    print("\n═══ B3. ТАЙМЛАЙН ТИРІВ ЛІКВІДНОСТІ ПО ДАТАХ (усі чинні категорії) ═══")
    print(f"  {'дата':<11}{'канд':>5}{'OK':>4}{'MED':>4}{'LOW':>4}{'UNK':>4}")
    byd = defaultdict(list)
    for r in rows:
        if r.get("category") in set(active):
            byd[r.get("date", "?")].append(r)
    for d_ in sorted(byd):
        if d_ >= "2026-09-10":
            n, t, _ = agg(byd[d_])
            print(f"  {d_:<11}{n:>5}{t['OK']:>4}{t['MEDIUM']:>4}{t['LOW']:>4}"
                  f"{t['UNKNOWN']:>4}")


def part_c(store, cats, since):
    """Аудит: чи кандидати — порівнювані товари (а не суміш сетів/мов/форматів)."""
    rows = [r for r in _load_discovery_rows()
            if r.get("category") in cats and r.get("date", "") >= since]
    rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
    print(f"\n═══ C. АУДИТ КАНДИДАТІВ (з {since}) ═══")
    for cat in cats:
        sel = [r for r in rows if r["category"] == cat]
        ks = Counter(r["product_key"] for r in sel)
        pcts = sorted(r.get("pct_below", 0) for r in sel)
        med = pcts[len(pcts) // 2] if pcts else 0
        print(f"\n{cat}: кандидатів {len(sel)}, унікальних ключів {len(ks)}, "
              f"медіана «нижче медіани» {med:.0f}%")
        for r in sel[:14]:
            ent = store.listings.get(r["product_key"], {}).get(r["item_id"], {})
            print(f"  {r['date']} {r.get('liquidity_tier', '?')[:3]:<3} "
                  f"{r['price_total']:>7.2f} vs мед {r['hist_median']:>7.2f} "
                  f"(-{r['pct_below']:.0f}%, n={r['hist_points']}) "
                  f"{(ent.get('seller') or '?')[:12]:<12} "
                  f"{(ent.get('title') or '?')[:64]}  [{r['product_key'][:34]}]")
        print("  ключі кандидатів: розкид цін по всій історії ключа + 2 назви")
        for k, n in ks.most_common(5):
            pr = sorted(p["price_total"] for p in store.history.get(k, []))
            if not pr:
                continue
            titles = [(e.get("title") or "?")[:52]
                      for e in list(store.listings.get(k, {}).values())[:2]]
            print(f"    {k[:40]:<40} канд {n:>2} точок {len(pr):>3} "
                  f"мін {pr[0]:>7.2f} мед {pr[len(pr) // 2]:>7.2f} "
                  f"макс {pr[-1]:>7.2f}  | " + " / ".join(titles))


def part_d(store, cats, since):
    """Симуляція «фільтра якості»: скільки кандидатів переживе перевірки.
    R1 кластер: >=2 ІНШИХ лістинги того ж ключа в межах ±12% ціни кандидата
        (за ±5 днів) → це ринкова ціна, а не знахідка.
    R2 реалістичність: знижка 25-65% (глибше -> підозра на змішаний ключ/фейк).
    R3 база: у ключа >=5 точок."""
    rows = [r for r in _load_discovery_rows()
            if r.get("category") in cats and r.get("date", "") >= since]
    print(f"\n═══ D. СИМУЛЯЦІЯ ФІЛЬТРА ЯКОСТІ (кандидати з {since}) ═══")
    keep_all = []
    for cat in cats:
        sel = [r for r in rows if r["category"] == cat]
        c1 = c2 = c3 = 0
        keep = []
        for r in sel:
            k, p0, d0 = r["product_key"], r["price_total"], _d(r["date"])
            near = 0
            for p in store.history.get(k, []):
                if p["item_id"] == r["item_id"]:
                    continue
                if abs((_d(p["date"]) - d0).days) <= 5 and abs(p["price_total"] - p0) / p0 <= 0.12:
                    near += 1
            f1 = near >= 2
            f2 = not (25 <= r["pct_below"] <= 65)
            f3 = r["hist_points"] < 5
            c1 += f1; c2 += f2; c3 += f3
            if not (f1 or f2 or f3):
                keep.append(r)
        keep_all += keep
        print(f"  {cat:<40} кандидатів {len(sel):>3} | відсіє R1 кластер {c1:>3}, "
              f"R2 глибина {c2:>3}, R3 база {c3:>3} | ПЕРЕЖИВАЮТЬ усі: {len(keep)}")
    print("  Що лишилось (для очної перевірки):")
    for r in sorted(keep_all, key=lambda r: r.get("ts", ""), reverse=True)[:20]:
        ent = store.listings.get(r["product_key"], {}).get(r["item_id"], {})
        print(f"    {r['date']} {r.get('liquidity_tier', '?')[:3]:<3} {r['price_total']:>7.2f} vs мед "
              f"{r['hist_median']:>7.2f} (-{r['pct_below']:.0f}%) "
              f"{(ent.get('title') or '?')[:70]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-17",
                    help="фіксована дата для B2 (YYYY-MM-DD)")
    ap.add_argument("--audit-since", default="2026-09-13",
                    help="від якої дати аудит кандидатів (частина C)")
    args = ap.parse_args()
    today = datetime.now(timezone.utc).date()
    store = HistoryStore(config.PRICE_HISTORY_FILE)
    print(f"Аналіз стану на {today.isoformat()} (UTC) | ключів у історії: "
          f"{len(store.history)} | категорій у конфізі: {len(config.CATEGORIES)}\n")
    part_a(store, today)
    part_b(store, today, date.fromisoformat(args.since))
    part_c(store, ["Pokemon Booster Box versiegelt",
                   "Magic The Gathering Booster Box sealed"], args.audit_since)
    part_d(store, ["Pokemon Booster Box versiegelt",
                   "Magic The Gathering Booster Box sealed"], args.audit_since)


if __name__ == "__main__":
    main()
