"""Миттєва і автоматична оцінка оголошень RAM: парсить назву, визначає тип,
порівнює ціну зі стелями купівлі з реальних продажів (Terapeak, 21.09.2026,
research/ram_terapeak.py) і формує вердикт BUY/CHECK/SKIP.

Ручна перевірка (пишете самі, коли скинули оголошення в чат):
    python research/ram_alert.py "SK Hynix 16GB DDR5 SODIMM RAM 5600MHz PC5-5600B" 50

Надіслати вердикт у Telegram (той самий бот/чат, що й основний бот, потрібні
секрети TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID в оточенні):
    python research/ram_alert.py "..." 50 --tg
"""
import sys

sys.path.insert(0, ".")
from ram_parse import parse_title

# (gen, form, ecc, total, kit) -> реальні продажі за 90 днів (Terapeak, 21.09.2026)
REAL = {
    ("ddr5", "udimm", False, 32, True): dict(p25=299, med=335, st=46, name="DDR5 UDIMM 32 ГБ (2×16) кіт"),
    ("ddr5", "udimm", False, 64, True): dict(p25=516, med=602, st=19, name="DDR5 UDIMM 64 ГБ (2×32) кіт"),
    ("ddr5", "sodimm", False, 32, False): dict(p25=210, med=249, st=16, name="DDR5 SO-DIMM 32 ГБ"),
    ("ddr5", "sodimm", False, 16, False): dict(p25=120, med=149, st=18, name="DDR5 SO-DIMM 16 ГБ"),
    ("ddr4", "udimm", False, 32, True): dict(p25=107, med=137, st=21, name="DDR4 UDIMM 32 ГБ (2×16) кіт"),
    ("ddr4", "udimm", False, 64, True): dict(p25=241, med=293, st=6, name="DDR4 UDIMM 64 ГБ (2×32) кіт"),
    ("ddr4", "sodimm", False, 32, False): dict(p25=122, med=149, st=15, name="DDR4 SO-DIMM 32 ГБ"),
    ("ddr4", "sodimm", False, 64, True): dict(p25=264, med=298, st=2.5, name="DDR4 SO-DIMM 64 ГБ (2×32) кіт"),
}
NOISE_BRANDS = {"other", "HP", "Dell", "Lenovo", "Apple", "Supermicro", "Medion", "ASUS", "QNAP/Synology", "2-Power"}


def costs(sale_price: float) -> float:
    """5% комісія (вживане) + €0.45 + €4.19 пересилка (консервативно, як безкоштовна) + 3% резерв повернень."""
    return 0.05 * sale_price + 0.45 + 4.19 + 0.03 * (2 * 4.19 + 0.45)


def evaluate(title: str, price: float, shipping: float = 0.0) -> dict:
    total_price = price + shipping
    p, reason = parse_title(title)
    if not p:
        return dict(verdict="UNKNOWN", reason=f"парсер не розпізнав назву: {reason}", title=title, price=total_price)
    if p["ecc"]:
        return dict(verdict="SKIP", reason="ECC/серверна пам'ять — поза нашими прибутковими типами", title=title, price=total_price)
    key = (p["gen"], p["form"], p["ecc"], p["total"], p["kit"])
    real = REAL.get(key)
    if not real:
        return dict(verdict="SKIP", reason=f"тип {p['gen']} {p['form']} {p['total']}ГБ не входить у список прибуткових", title=title, price=total_price)
    net_q = real["p25"] - costs(real["p25"])
    cap, good, excellent = net_q / 1.3, net_q / 1.6, net_q / 2.0
    profit_est = net_q - total_price
    if total_price <= excellent:
        verdict = "BUY-EXCELLENT"
    elif total_price <= good:
        verdict = "BUY-GOOD"
    elif total_price <= cap:
        verdict = "BUY"
    else:
        verdict = "SKIP"
    brand_flag = "невідомий/сумнівний бренд" if (p["brand"] in NOISE_BRANDS or not p["brand"]) else p["brand"]
    return dict(verdict=verdict, type=real["name"], price=total_price, cap=cap, good=good, excellent=excellent,
                quick_sale=real["p25"], median_sale=real["med"], sell_through=real["st"], profit_est=profit_est,
                brand=brand_flag, title=title)


def format_message(r: dict) -> str:
    if r["verdict"] in ("UNKNOWN", "SKIP"):
        return f"⏭ {r['title'][:70]}\n{r.get('reason', '')} (ціна {r['price']:.0f}€)"
    tag = {"BUY-EXCELLENT": "🟢🟢 ВІДМІННО, БЕРИ", "BUY-GOOD": "🟢 ДОБРЕ, БЕРИ", "BUY": "🟡 CHECK (тонка маржа)"}[r["verdict"]]
    return (f"{tag}: {r['type']}\n"
            f"«{r['title'][:80]}»\n"
            f"Ціна: {r['price']:.0f}€ | стеля ROI30%: {r['cap']:.0f}€ | добра: {r['good']:.0f}€ | відмінна: {r['excellent']:.0f}€\n"
            f"Реальний продаж (90 дн.): медіана {r['median_sale']}€, швидкий {r['quick_sale']}€, sell-through {r['sell_through']}%\n"
            f"Бренд: {r['brand']}\n"
            f"Орієнтовно чистими (продаж за {r['quick_sale']}€): ≈{r['profit_est']:.0f}€")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("title")
    ap.add_argument("price", type=float)
    ap.add_argument("--shipping", type=float, default=0.0)
    ap.add_argument("--tg", action="store_true", help="надіслати вердикт у Telegram (ті самі секрети, що й основний бот)")
    args = ap.parse_args()
    res = evaluate(args.title, args.price, args.shipping)
    msg = format_message(res)
    print(msg)
    if args.tg:
        import config
        import main as botmain

        if config.TELEGRAM_CREDS_OK:
            botmain.send_telegram("[RAM] " + msg)
        else:
            print("[DRY RUN] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не задані в оточенні")
