"""Швидка перевірка ключів перед запуском розвідки.

    python selftest.py            # тільки eBay (без side-effects)
    python selftest.py --telegram # + надіслати одне тестове повідомлення в чат
"""

import argparse
import sys

import config
import main as bot


def check_ebay() -> bool:
    if not config.EBAY_CREDS_OK:
        print("✗ eBay: EBAY_APP_ID / EBAY_CERT_ID не задані в .env")
        return False
    try:
        client = bot.EbayClient()
        token = client._auth_header()
        print(f"✓ eBay OAuth: токен отримано ({token[:14]}…)")
    except Exception as exc:  # noqa: BLE001
        print(f"✗ eBay OAuth не вдався: {exc}")
        return False
    try:
        cat = config.CATEGORIES[0]
        a = client.search(cat["query"], cat["min_price"], sort=None)
        b = client.search(cat["query"], cat["min_price"], sort=config.SEARCH_SORT_B)
        merged = {i.get("itemId"): i for i in list(a) + list(b)}
        only_b = sum(1 for iid in merged if iid not in {i.get("itemId") for i in a})
        ep = sum(1 for i in merged.values() if i.get("epid"))
        print(f"✓ eBay Browse: «{cat['query']}» ({config.EBAY_MARKETPLACE_ID}, "
              f"price:[{cat['min_price']}..]) → A={len(a)} B={len(b)} "
              f"merged={len(merged)} (B додав {only_b}); лотів з epid: {ep}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"✗ eBay Browse пошук не вдався: {exc}")
        return False


def check_telegram() -> bool:
    if not config.TELEGRAM_CREDS_OK:
        print("✗ Telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не задані")
        return False
    try:
        bot.send_telegram("✅ eBay-bot selftest: зв'язок із чатом працює.")
        print(f"✓ Telegram: тестове повідомлення надіслано в чат "
              f"{config.TELEGRAM_CHAT_ID}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"✗ Telegram надсилання не вдалося: {exc}")
        return False


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--telegram", action="store_true",
                   help="надіслати одне тестове повідомлення в Telegram-чат")
    args = p.parse_args()

    ok = check_ebay()
    if args.telegram:
        ok = check_telegram() and ok
    else:
        print("· Telegram: пропущено (додайте --telegram, щоб перевірити "
              "надсиланням одного повідомлення)")
    sys.exit(0 if ok else 1)
