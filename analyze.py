"""Аудит стану та shadow-вердиктів (READ-ONLY): нічого не пише в стан.

    python analyze.py [--days N]

A. Стан: скільки ключів «готові до оцінки» (достатньо еталона) і покриття по категоріях.
B. Shadow-вердикти за днями / категоріями / причинами HOLD.
C. Усі ALERT (те, що бот надіслав би в Telegram) — з ціною, еталоном, продавцем, посиланням.
D. Найближчі «майже-знахідки» (HOLD із причиною ліквідності/продавця/spec), щоб бачити,
   чи фільтри не надто суворі.
Використовується workflow analyze.yml (кеш → цей скрипт → артефакт).
"""
import argparse
import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import config
from main import HistoryStore


def load_shadow(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "shadow":
                rows.append(r)
    return rows


def part_a(store, now):
    W = config.HISTORY_WINDOW_DAYS
    need_pts, need_sel = config.QUALITY_MIN_COMPS + 1, config.QUALITY_MIN_SELLERS + 1
    by_q = defaultdict(list)
    for k in store.history:
        by_q[store.key_query.get(k, "?")].append(k)
    print("═══ A. СТАН ═══")
    print(f"схема: {config.STATE_SCHEMA} | ключів: {len(store.history)} | "
          f"точок: {sum(len(v) for v in store.history.values())} | "
          f"лістингів: {sum(len(b) for b in store.listings.values())} | "
          f"legacy_v1: {'є' if store.legacy_v1 else 'нема'} | seeded: {len(store.seeded)}")
    print(f"ринок покупця: {'+'.join(config.MARKET_COUNTRIES)} | вікно історії {W} дн. | "
          f"еталон ≥{config.QUALITY_MIN_COMPS} лот./≥{config.QUALITY_MIN_SELLERS} прод.")
    print(f"\n  {'категорія':<46}{'лістинг':>8}{'ключів':>7}{'готові':>7}{'у готових':>10}{'active':>7}{'gone':>6}")
    tot_l = tot_r = 0
    for cat in config.CATEGORIES:
        q = cat["query"]
        keys = by_q.get(q, [])
        ready_keys, in_ready = 0, 0
        n_list = 0
        for k in keys:
            pts = store.points_in_window(k, now, W)
            n_list += len(pts)
            if len(pts) >= need_pts and len({p.get("seller") or "?" for p in pts}) >= need_sel:
                ready_keys += 1
                in_ready += len(pts)
        act = gone = 0
        for k in keys:
            for e in store.listings.get(k, {}).values():
                act += e["status"] == "active"
                gone += e["status"] == "gone"
        tot_l += n_list
        tot_r += in_ready
        print(f"  {q:<46}{n_list:>8}{len(keys):>7}{ready_keys:>7}{in_ready:>10}{act:>7}{gone:>6}")
    share = (tot_r / tot_l * 100) if tot_l else 0
    print(f"\n  частка лістингів у ключах, для яких можлива оцінка: {tot_r}/{tot_l} = {share:.0f}%")


def part_b(rows, days):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    rows = [r for r in rows if r.get("date", "") >= since]
    print(f"\n═══ B. SHADOW-ВЕРДИКТИ (за {days} дн., рядків {len(rows)}) ═══")
    if not rows:
        print("  ще немає рядків (жоден новий лот не пройшов до порогу «майже-знахідка» або їх ще не було).")
        return rows
    byday = defaultdict(Counter)
    byc = defaultdict(Counter)
    reasons = Counter()
    for r in rows:
        byday[r["date"]][r["verdict"]] += 1
        byc[r["category"]][r["verdict"]] += 1
        for x in r.get("reasons") or []:
            reasons[x] += 1
    print("  за днями:", {d: dict(c) for d, c in sorted(byday.items())})
    print("  за категоріями:", {c: dict(v) for c, v in sorted(byc.items())})
    print("  причини HOLD:", dict(reasons.most_common()))
    return rows


def part_c(rows):
    alerts = [r for r in rows if r["verdict"] == "ALERT"]
    print(f"\n═══ C. ALERT — що надіслав би бот ({len(alerts)}) ═══")
    for r in sorted(alerts, key=lambda r: r["ts"]):
        print(f"  {r['ts'][:16]} {r['category'][:26]:<26} {r['price_total']:>7.2f} vs {r['median']:>7.2f} "
              f"(-{(1 - r['ratio']) * 100:.0f}%, еталон {r['n_comps']}/{r['n_sellers']}п, розкид {r['dispersion']}, "
              f"ліквід. {r['liquidity_tier']}) {r['seller']} ({r['seller_score']}) {r['country']}")
        print(f"      {r['title']}\n      {r['url']}")


def part_d(rows):
    miss = [r for r in rows if r["verdict"] == "HOLD" and r.get("median")
            and not (set(r.get("reasons") or []) & {"thin-ref", "blended-ref", "weak-ref", "not-cheap"})]
    print(f"\n═══ D. МАЙЖЕ-ЗНАХІДКИ (якість ок, але HOLD) ({len(miss)}) ═══")
    for r in sorted(miss, key=lambda r: -(r.get("saving") or 0))[:25]:
        print(f"  {r['date']} {r['category'][:24]:<24} {r['price_total']:>7.2f} vs {r['median']:>7.2f} "
              f"еталон {r['n_comps']}/{r['n_sellers']} | {','.join(r['reasons'])} | {r['title'][:60]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    store = HistoryStore(config.PRICE_HISTORY_FILE)
    print(f"Аналіз на {now.strftime('%Y-%m-%d %H:%M')} UTC | ALERT_MODE={config.ALERT_MODE}\n")
    part_a(store, now)
    rows = part_b(load_shadow(config.DISCOVERY_LOG_FILE), args.days)
    part_c(rows)
    part_d(rows)


if __name__ == "__main__":
    main()
