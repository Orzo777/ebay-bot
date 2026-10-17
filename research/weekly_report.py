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
from datetime import date, datetime, timedelta, timezone

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
    out = ["💰 <b>Гроші</b>",   # 14.10: коротко; «на руках» — у «Головне»
           f"• Тиждень: продано {len(sold_w)} на {_eur(sum(r['sprice'] or 0 for r in sold_w))} → "
           f"<b>+{_eur(sum(r['profit'] or 0 for r in sold_w))}</b> · куплено {len(bought_w)} на "
           f"{_eur(sum(r['spent'] or 0 for r in bought_w))}",
           f"• Усього: продано {len(sold)} → <b>+{_eur(profit_all)}</b>"
           + (f" (+{100 * profit_all / spent_sold:.0f}% на вкладене)" if spent_sold else "")]
    del open_
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
    return [f"• 30 днів: продано {len(m)} → <b>+{_eur(sum(r['profit'] or 0 for r in m))}</b>"
            + (f" · лежав у середньому {sum(days) / len(days):.0f} дн." if days else "")
            + " · " + ", ".join(f"{c} +{_eur(v)}" for c, v in sorted(cats.items(), key=lambda kv: -kv[1]))]


# 09.10: податкові пороги — пасивний лічильник, нічого не вирішує. DAC7: eBay повідомляє податковій про продавця від
# 30 продажів АБО 2 000 € обороту за календарний рік; приватні продажі (§ 23 EStG) — прибуток понад 1 000 €/рік оподатковується.
DAC7_SALES, DAC7_TURNOVER, PRIVATE_PROFIT = 30, 2000.0, 1000.0


def tax_lines(sold: list[dict], today: date) -> list[str]:
    year = [r for r in sold if (_d(r["sdate"]) or date.min).year == today.year]
    n, turnover, profit = len(year), sum(r["sprice"] or 0 for r in year), sum(r["profit"] or 0 for r in year)
    line = (f"• Податки {today.year}: продажів {n}/{DAC7_SALES} · оборот {_eur(turnover)} / {_eur(DAC7_TURNOVER)} (DAC7) · "
            f"прибуток {_eur(profit)} / {_eur(PRIVATE_PROFIT)}")
    near = n >= 0.8 * DAC7_SALES or turnover >= 0.8 * DAC7_TURNOVER or profit >= 0.8 * PRIVATE_PROFIT
    return [line] + (["  ⚠️ Близько до порогу — варто порадитися зі Steuerberater (це не податкова порада)."] if near else [])


def accuracy(rows: list[dict], today: date) -> tuple[list[str], float | None]:
    """Продано за 90 днів: фактична ціна проти медіани типу (з 10.10 стелі купівлі рахуються від неї, з урахуванням
    сегмента фірмовий / звичайні). Розкид у межах типу великий — тому дивимось і на кожен продаж, і на середнє."""
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
            ratio = r["sprice"] / ident["median_sale"]
            ratios.append(ratio)
            if len(lines) < 5:
                lines.append(f"• №{r['n']} {html.escape(r['title'][:32])}: продано {_eur(r['sprice'])}, медіана бота "
                             f"{ident['median_sale']} € ({100 * (ratio - 1):+.0f}%)" + (f", за {days} дн." if days is not None else ""))
    out = ["🎯 <b>Точність прогнозу</b> (продане за 90 днів)"]
    if not recent:
        return out + ["• Продажів ще не було — порівнювати нема з чим."], None
    out += lines
    mean = sum(ratios) / len(ratios) if ratios else None
    if mean is not None:
        out.append(f"• У середньому продаємо за {100 * mean:.0f}% від медіани бота ({len(ratios)} продажів)")
        if len(ratios) >= 3 and mean < 0.93:
            out.append(f"⚠️ Продаємо дешевше, ніж бот рахує, — стелі купівлі завищені приблизно на {100 * (1 - mean):.0f}%. "
                       "Скажи Claude — підкоригує.")
        elif len(ratios) >= 3 and mean > 1.08:
            out.append("✅ Продаємо дорожче прогнозу — стелі консервативні, можна купувати сміливіше.")
    for name, c in sorted(cats.items(), key=lambda kv: -kv[1]["n"]):
        out.append(f"• {html.escape(str(name))}: {c['n']} шт, сер. прибуток {_eur(c['profit'] / c['n'])}"
                   + (f", маржа {100 * c['profit'] / c['spent']:.0f}%" if c["spent"] else "")
                   + (f", продається за ~{sum(c['days']) / len(c['days']):.0f} дн." if c["days"] else ""))
    return out, mean


def stale(rows: list[dict], today: date, market=sell.competitors, cuts: list | None = None, ns: list | None = None) -> list[str]:
    """cuts — сюди (№, нова ціна товару) для кнопок «⬇️ Знизити» (10.10, якщо eBay підключено); ns — номери з порадою
    (17.10: для аркуша з фото)."""
    out, calls = [], 0
    for r in rows:
        if r["status"] in CLOSED:
            continue
        age = (today - _d(r["date"])).days if _d(r["date"]) else 0
        name = f"№{r['n']} {html.escape(r['title'][:34])}"
        before = len(out)
        if r["status"] in ("Оплачено", "В дорозі") and age >= NOT_ARRIVED_DAYS:
            out.append(f"• {name} — куплено {age} дн. тому, досі «{r['status']}» → перевір доставку / спір на eBay чи KA")
        elif r["status"] != "Виставлено" and age >= NOT_LISTED_DAYS:
            out.append(f"• {name} — {age} дн. на руках і ще не виставлено → «продати {r['n']}»")
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
            # 10.10: ціна товару + доставка окремо (як в оголошенні з 09.10), а не «разом»
            ship = sell.SHIP_CHARGE_RAM if ident["kind"] == "ram" else (ident.get("ship_out") or sell.console_alert.SHIP)
            item = sell.item_prices(pr, ship)["item"]
            if cuts is not None and r["status"] == "Виставлено":
                cuts.append((r["n"], item))
            out.append(f"• {name} — {age} дн. і не продано → постав <b>{item} €</b> + доставка {sell.money_de(ship)} €"
                       + (f" · ще +{pr['profit_list']:.0f} €" if pr.get("profit_list") is not None else "")
                       + (f" (найдешевші {', '.join(f'{c:.0f}' for c in comp[:3])} €)" if comp else ""))
        if ns is not None and len(out) > before:
            ns.append((r["n"], age))
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
    end = datetime.combine(today, datetime.max.time(), timezone.utc)   # тиждень звіту, а не «зараз» (тест 12.10 падав від дати)
    e, k = health.totals(ebay.get("stats"), 168, end), health.totals(ka.get("stats"), 168, end)
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


def headline(rows: list[dict], today: date, todo: list[str], bl: list[str]) -> list[str]:
    """10.10 (прохання користувача): «Головне» — 3 рядки зверху; деталі нижче."""
    week = today - timedelta(days=6)
    sold_w = [r for r in rows if r["status"] == "Продано" and (_d(r["sdate"]) or date.min) >= week]
    open_ = [r for r in rows if r["status"] not in CLOSED]
    acts = [x for x in todo[1:] if x.startswith("•")]
    out = ["⭐ <b>Головне</b>",
           f"• Тиждень: продано {len(sold_w)} шт, прибуток <b>{_eur(sum(r['profit'] or 0 for r in sold_w))}</b> · "
           f"на руках {len(open_)} шт на {_eur(sum(r['spent'] or 0 for r in open_))}",
           f"• Зробити: {len(acts)} " + ("— нижче" if acts else "— нічого, усе в русі ✓")]
    if bl:
        out.append(f"• Підписки KA: змінити межі в {sum(1 for x in bl if x.startswith('•'))} — нижче")
    import ram_alert   # 11.10: ціни ростуть — 30-денна медіана відстає; замір Terapeak — раз на 2 тижні
    age = (today - date.fromisoformat(ram_alert.TERAPEAK_DATE)).days
    if age >= ram_alert.TERAPEAK_EVERY:
        out.append(f"• Ціни продажу заміряні {age} дн. тому — увійди в eBay у вбудованому браузері й напиши Claude «терапік»")
    return out


def shortage() -> list[str]:
    """10.10: що подорожчало за ~2 тижні поза нашими категоріями (research/shortage_watch.py, дані — у ram_prices.json)."""
    try:
        import shortage_watch
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ram_prices.json"), encoding="utf-8") as fh:
            return shortage_watch.lines(json.load(fh), date.today().isoformat())
    except Exception as e:
        print(f"дефіцит: {e.__class__.__name__}")
        return []


def services() -> list[str]:
    """11.10: сторож сервісів (research/service_watch.py) — рядок «🔧 Сервіси»."""
    try:
        import service_watch
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ram_prices.json"), encoding="utf-8") as fh:
            return service_watch.lines(json.load(fh))
    except Exception as e:
        print(f"сервіси: {e.__class__.__name__}")
        return []


def bounds() -> list[str]:
    """10.10: межі «Preis bis» підписок KA, що відстали від ринку (research/ka_bounds.py)."""
    try:
        import ka_bounds
        return ka_bounds.lines()
    except Exception as e:   # звіт важливіший за цей рядок
        print(f"межі KA: {e.__class__.__name__}")
        return []


def section(lines: list[str], fold: bool = False) -> str:
    """14.10 (крок 8): заголовок розділу видно, пункти — у цитаті (сіра смуга); другорядне — згорнуте (fold), розкривається тапом.
    Розділ з одного рядка — як є."""
    if len(lines) < 2:
        return "\n".join(lines)
    return lines[0] + "\n" + ("<blockquote expandable>" if fold else "<blockquote>") + "\n".join(lines[1:]) + "</blockquote>"


def build(rows: list[dict], today: date, ebay: dict, ka: dict, market=sell.competitors, kb: list | None = None,
          ebay_on: bool = False, bounds_fn=bounds, shortage_fn=shortage, services_fn=services, ns: list | None = None) -> str:
    """kb — сюди ряди кнопок: «⬇️ Знизити» (eBay підключено) і «✅ Межі оновив»; ns — (№, днів) з порадою для аркуша з фото."""
    acc, _ = accuracy(rows, today)
    cuts, bl = [], bounds_fn()
    todo = stale(rows, today, market, cuts, ns)
    # 14.10: головне й дії — розгорнуті; точність, пошук, дефіцит і сервіси — згорнуті (деталі на тап)
    parts = [(f"📈 <b>Тижневий звіт</b> · {(today - timedelta(days=6)):%d.%m}–{today:%d.%m}", False),
             *[(x, False) for x in (headline(rows, today, todo, bl), todo, money(rows, today))],
             *([(bl, False)] if bl else []), (acc, True), (search(ebay, ka, rows, today) + auctions(ebay, today), True)]
    for extra in (shortage_fn(), services_fn()):
        if extra:
            parts.append((extra, True))
    if kb is not None:
        if ebay_on:
            kb += [[{"text": f"⬇️ №{n} → {item} €", "callback_data": f"p|{n}|{item}"}] for n, item in cuts[:6]]
        if bl:
            kb.append([{"text": "✅ Межі в KA оновив", "callback_data": "c|межі|1"}])
    return "\n\n".join(p if isinstance(p, str) else section(p, fold) for p, fold in parts)


def stale_sheet(d: dict, ns: list) -> bytes | None:
    """17.10: товар з порадами («знизь ціну», «ще не виставлено»…) — аркушем з фото, як «🗂 На руках»."""
    import onhand
    import requests
    import config
    if not ns:
        return None
    rows = {r["n"]: r for r in d.get("rows") or []}
    photos = d.get("photos") or {}
    items = [{"n": n, "title": (rows.get(n) or {}).get("title", ""), "days": age, "photos": photos.get(str(n)) or photos.get(n) or []}
             for n, age in ns]
    token = os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN or ""
    return onhand.build({"groups": [{"title": "Що зробити з товаром", "kind": "problem", "items": items}]},
                        sell.tg_file, token, None, None, requests.get)


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
        kb, ns = [], []
        text = build(d.get("rows") or [], today, _load(a.ebay), _load(a.ka), kb=kb, ebay_on=bool(d.get("ebay")), ns=ns)
    except Exception as e:
        sell.office_send_html(f"⚠️ Тижневий звіт не склався: {html.escape(e.__class__.__name__)} — напиши Claude", None)
        raise
    parts = chunks(text)
    ok = all(sell.office_send_html(part, {"inline_keyboard": kb} if kb and i == len(parts) - 1 else None) for i, part in enumerate(parts))
    print(f"звіт надіслано: {ok}, рядків обліку: {len(d.get('rows') or [])}")
    try:
        sheet = stale_sheet(d, ns)
        if sheet:
            sell.office_send_photo(sheet, "📦 Що зробити з товаром — фото")
    except Exception as e:
        print(f"аркуш: {e.__class__.__name__}")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
