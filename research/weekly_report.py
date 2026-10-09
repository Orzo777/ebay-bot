"""Тижневий звіт (01.10) → бот «Облік і продаж»: гроші, точність прогнозів, залежаний товар, що дають підписки.

Запускає Apps Script (tools/ledger.gs): щопонеділка з 9:00 або командою «звіт». Рядки обліку приходять зашифрованими
(ключ — токен бота «Облік і продаж», як у «продати»); лічильники пошуку — з кешів станів ботів (research/health.py).
Навчання: продана ціна порівнюється з «швидкою» ціною продажу, на якій бот будує стелі купівлі. Якщо продаємо стабільно
дешевше — стелі завищені, звіт про це скаже. У лог GitHub (репозиторій публічний) нічого з обліку не пишемо.

    python research/weekly_report.py --blob … --mac … --nonce … --ebay ebay_watch_state.json --ka ram_mail_state.json
"""
import argparse
import html
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import health
import sell

CLOSED = ("Продано", "Повернено", "Скасовано")
STALE_DAYS, NOT_LISTED_DAYS, NOT_ARRIVED_DAYS = 10, 5, 10
MAX_MARKET_CALLS = 8


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _eur(x: float | None) -> str:
    return "—" if x is None else f"{x:.0f} €"


def money(rows: list[dict], today: date) -> list[str]:
    week = today - timedelta(days=6)
    sold = [r for r in rows if r["status"] == "Продано"]
    sold_w = [r for r in sold if (_d(r["sdate"]) or date.min) >= week]
    bought_w = [r for r in rows if (_d(r["date"]) or date.min) >= week]
    open_ = [r for r in rows if r["status"] not in CLOSED]
    profit_all = sum(r["profit"] or 0 for r in sold)
    spent_sold = sum(r["spent"] or 0 for r in sold)
    out = ["💰 <b>Гроші</b>",
           f"• За тиждень продано: {len(sold_w)} шт на {_eur(sum(r['sprice'] or 0 for r in sold_w))}, "
           f"прибуток <b>{_eur(sum(r['profit'] or 0 for r in sold_w))}</b>",
           f"• За тиждень куплено: {len(bought_w)} шт на {_eur(sum(r['spent'] or 0 for r in bought_w))}",
           f"• Усього продано: {len(sold)} шт, прибуток <b>{_eur(profit_all)}</b>"
           + (f" (віддача {100 * profit_all / spent_sold:.0f}% на вкладене)" if spent_sold else ""),
           f"• На руках: {len(open_)} шт, у них {_eur(sum(r['spent'] or 0 for r in open_))}"]
    out += month_lines(sold, today)
    out += tax_lines(sold, today)
    return out


def month_lines(sold: list[dict], today: date) -> list[str]:
    """09.10: підсумок за 30 днів — прибуток, скільки днів товар лежав, які категорії приносять гроші."""
    m = [r for r in sold if (_d(r["sdate"]) or date.min) >= today - timedelta(days=29)]
    if not m:
        return []
    days = [(_d(r["sdate"]) - _d(r["date"])).days for r in m if _d(r["date"]) and _d(r["sdate"])]
    cats: dict = {}
    for r in m:
        cats[r.get("cat") or "Інше"] = cats.get(r.get("cat") or "Інше", 0) + (r["profit"] or 0)
    return [f"• За 30 днів: продано {len(m)} шт, прибуток <b>{_eur(sum(r['profit'] or 0 for r in m))}</b>"
            + (f", товар лежав у середньому {sum(days) / len(days):.0f} дн." if days else ""),
            "  по категоріях: " + ", ".join(f"{c} {_eur(v)}" for c, v in sorted(cats.items(), key=lambda kv: -kv[1]))]


# 09.10: податкові пороги — пасивний лічильник, нічого не вирішує. DAC7: eBay повідомляє податковій про продавця від
# 30 продажів АБО 2 000 € обороту за календарний рік; приватні продажі (§ 23 EStG) — прибуток понад 1 000 €/рік оподатковується.
DAC7_SALES, DAC7_TURNOVER, PRIVATE_PROFIT = 30, 2000.0, 1000.0


def tax_lines(sold: list[dict], today: date) -> list[str]:
    year = [r for r in sold if (_d(r["sdate"]) or date.min).year == today.year]
    n, turnover, profit = len(year), sum(r["sprice"] or 0 for r in year), sum(r["profit"] or 0 for r in year)
    line = (f"• Податкові пороги {today.year}: продажів {n}/{DAC7_SALES}, оборот {_eur(turnover)} / {_eur(DAC7_TURNOVER)} (DAC7), "
            f"прибуток {_eur(profit)} / {_eur(PRIVATE_PROFIT)}")
    near = n >= 0.8 * DAC7_SALES or turnover >= 0.8 * DAC7_TURNOVER or profit >= 0.8 * PRIVATE_PROFIT
    return [line] + (["  ⚠️ Близько до порогу — варто порадитися зі Steuerberater (це не податкова порада)."] if near else [])


def accuracy(rows: list[dict], today: date) -> tuple[list[str], float | None]:
    """Продано за 90 днів: фактична ціна проти «швидкої» ціни продажу (p25), на якій стоять стелі купівлі."""
    recent = [r for r in rows if r["status"] == "Продано" and r.get("sprice") and (_d(r["sdate"]) or date.min) >= today - timedelta(days=90)]
    lines, ratios, cats = [], [], {}
    for r in sorted(recent, key=lambda r: r["sdate"], reverse=True):
        ident = sell.identify(r["title"])
        days = (_d(r["sdate"]) - _d(r["date"])).days if _d(r["sdate"]) and _d(r["date"]) else None
        c = cats.setdefault(r.get("cat") or "Інше", {"n": 0, "profit": 0.0, "spent": 0.0, "days": []})
        c["n"] += 1
        c["profit"] += r["profit"] or 0
        c["spent"] += r["spent"] or 0
        if days is not None:
            c["days"].append(days)
        if ident:
            ratio = r["sprice"] / ident["quick_sale"]
            ratios.append(ratio)
            if len(lines) < 5:
                lines.append(f"• №{r['n']} {html.escape(r['title'][:32])}: продано {_eur(r['sprice'])}, бот рахував "
                             f"{ident['quick_sale']} € ({100 * (ratio - 1):+.0f}%)" + (f", за {days} дн." if days is not None else ""))
    out = ["🎯 <b>Точність прогнозу</b> (продане за 90 днів)"]
    if not recent:
        return out + ["• Продажів ще не було — порівнювати нема з чим."], None
    out += lines
    mean = sum(ratios) / len(ratios) if ratios else None
    if mean is not None:
        out.append(f"• У середньому продаємо за {100 * mean:.0f}% від «швидкої» ціни бота ({len(ratios)} продажів)")
        if len(ratios) >= 3 and mean < 0.95:
            out.append(f"⚠️ Продаємо дешевше, ніж бот рахує, — стелі купівлі завищені приблизно на {100 * (1 - mean):.0f}%. "
                       "Скажи Claude — підкоригує.")
        elif len(ratios) >= 3 and mean > 1.08:
            out.append("✅ Продаємо дорожче прогнозу — стелі консервативні, можна купувати сміливіше.")
    for name, c in sorted(cats.items(), key=lambda kv: -kv[1]["n"]):
        out.append(f"• {html.escape(str(name))}: {c['n']} шт, сер. прибуток {_eur(c['profit'] / c['n'])}"
                   + (f", маржа {100 * c['profit'] / c['spent']:.0f}%" if c["spent"] else "")
                   + (f", продається за ~{sum(c['days']) / len(c['days']):.0f} дн." if c["days"] else ""))
    return out, mean


def stale(rows: list[dict], today: date, market=sell.competitors) -> list[str]:
    out, calls = [], 0
    for r in rows:
        if r["status"] in CLOSED:
            continue
        age = (today - _d(r["date"])).days if _d(r["date"]) else 0
        name = f"№{r['n']} {html.escape(r['title'][:34])}"
        if r["status"] in ("Оплачено", "В дорозі") and age >= NOT_ARRIVED_DAYS:
            out.append(f"• {name} — куплено {age} дн. тому, досі «{r['status']}»: перевір доставку, якщо треба — спір на eBay/KA")
        elif r["status"] != "Виставлено" and age >= NOT_LISTED_DAYS:
            out.append(f"• {name} — {age} дн. на руках і ще не виставлено → «продати {r['n']}» "
                       f"(якщо вже на eBay — «виставив {r['n']}»)")
        elif age >= STALE_DAYS:
            ident = sell.identify(r["title"])
            if not ident:
                out.append(f"• {name} — виставлено {age} дн., не продається: глянь ціну конкурентів")
                continue
            comp = []
            if calls < MAX_MARKET_CALLS:
                calls += 1
                try:
                    comp = market(ident)
                except Exception as e:
                    print(f"ринок: {e.__class__.__name__}")
            pr = sell.plan_price(ident, r.get("spent"), comp, undercut=True)   # залежалося — під найдешевших
            out.append(f"• {name} — {age} дн. і не продано → постав <b>{pr['list']} €</b>"
                       + (f" (3 найдешевші зараз {', '.join(f'{c:.0f}' for c in comp[:3])} €)" if comp else "")
                       + (f", прибуток ще ≈ {pr['profit_list']:.0f} €" if pr.get("profit_list") is not None else ""))
    return (["📦 <b>Що зробити з товаром</b>"] + out) if out else ["📦 <b>Товар</b>: залежаного немає ✓"]


def auctions(ebay: dict, today: date) -> list[str]:
    """09.10: аукціони за тиждень, про які були картки: у скількох фінальна ставка вклалась у наш максимум."""
    week = (today - timedelta(days=6)).isoformat()
    res = [r for r in ebay.get("auction_results") or [] if r.get("d", "") >= week]
    if not res:
        return []
    ok = [r for r in res if r["final"] <= r["max"]]
    over = sorted((r["final"] - r["max"]) / r["max"] * 100 for r in res if r["final"] > r["max"] and r["max"])
    line = f"🔨 <b>Аукціони за тиждень</b>: {len(res)} з картками, у {len(ok)} фінал ≤ нашого максимуму (можна було виграти)"
    if over:
        line += f"; решта пішли дорожче на {over[len(over) // 2]:.0f}% (медіана)"
    return [line]


def search(ebay: dict, ka: dict, rows: list[dict], today: date) -> list[str]:
    e, k = health.totals(ebay.get("stats"), 168), health.totals(ka.get("stats"), 168)
    week = today - timedelta(days=6)
    bought = [r for r in rows if (_d(r["date"]) or date.min) >= week]
    out = ["🔎 <b>Пошук за тиждень</b>",
           f"• Kleinanzeigen: листів {k.get('ka_mails', 0)} → карток {k.get('ka_cards', 0)}",
           f"• eBay: карток {e.get('ebay_cards', 0)}, аукціонів {e.get('ebay_auctions', 0)}",
           f"• Куплено за тиждень: {len(bought)} (KA {sum(1 for r in bought if 'Klein' in str(r.get('src')) or 'Самовивіз' in str(r.get('src')))}, "
           f"eBay {sum(1 for r in bought if 'eBay' in str(r.get('src')))})"]
    subs = {}
    for key, v in k.items():
        if key.startswith("sub_mails|"):
            subs.setdefault(key[10:], [0, 0])[0] += v
        elif key.startswith("sub_cards|"):
            subs.setdefault(key[10:], [0, 0])[1] += v
    if subs:
        good = sorted(((n, m, c) for n, (m, c) in subs.items() if c), key=lambda x: -x[2])
        idle = sorted(((n, m) for n, (m, c) in subs.items() if not c), key=lambda x: -x[1])
        if good:
            out.append("• Підписки з картками: " + "; ".join(f"{html.escape(n)} — {c} з {m} листів" for n, m, c in good[:6]))
        if idle:
            out.append("• Без жодної картки: " + "; ".join(f"{html.escape(n)} ({m} листів)" for n, m in idle[:6])
                       + " — якщо так і далі, варто звузити або прибрати")
    first = min(list((ka.get("stats") or {})) + list((ebay.get("stats") or {})) or ["9999"])
    if first == "9999":
        out.append("ℹ️ Лічильники пошуку ще порожні.")
    elif first > (date.today() - timedelta(days=6)).isoformat():
        out.append(f"ℹ️ Лічильники ведуться з {first[8:10]}.{first[5:7]} — повний тиждень буде пізніше.")
    return out


def build(rows: list[dict], today: date, ebay: dict, ka: dict, market=sell.competitors) -> str:
    acc, _ = accuracy(rows, today)
    parts = [[f"📈 <b>Тижневий звіт</b> · {(today - timedelta(days=6)):%d.%m}–{today:%d.%m}"],
             money(rows, today), acc, stale(rows, today, market), search(ebay, ka, rows, today) + auctions(ebay, today)]
    return "\n\n".join("\n".join(p) for p in parts)


def chunks(text: str, limit: int = 3900) -> list[str]:
    """Telegram — до 4096 символів; ріжемо між розділами, щоб не розірвати HTML-теги."""
    out, cur = [], ""
    for block in text.split("\n\n"):
        if cur and len(cur) + len(block) + 2 > limit:
            out.append(cur)
            cur = ""
        cur = (cur + "\n\n" + block) if cur else block[:limit]
    return out + ([cur] if cur else [])


def _load(path: str | None) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, TypeError):
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blob")
    ap.add_argument("--mac")
    ap.add_argument("--nonce")
    ap.add_argument("--ebay")
    ap.add_argument("--ka")
    a = ap.parse_args()
    import config
    try:
        d = sell.unseal(a.blob or "", a.mac or "", os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN or "", a.nonce or "")
    except Exception as e:
        sell.office_send_html(f"⚠️ Тижневий звіт: не зміг прочитати дані обліку ({html.escape(str(e))})", None)
        raise SystemExit(1)
    today = _d(d.get("today")) or date.today()
    try:
        text = build(d.get("rows") or [], today, _load(a.ebay), _load(a.ka))
    except Exception as e:
        sell.office_send_html(f"⚠️ Тижневий звіт не склався: {html.escape(e.__class__.__name__)} — напиши Claude", None)
        raise
    ok = all(sell.office_send_html(part, None) for part in chunks(text))
    print(f"звіт надіслано: {ok}, рядків обліку: {len(d.get('rows') or [])}")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
