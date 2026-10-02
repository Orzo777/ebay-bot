"""Відповідь продавця KA → реплаєм на картку, з якої користувач писав продавцю (02.10).

Apps Script (tools/kafilter.gs) уже зробив текст (суть, переклад, кнопка чату) і передає його зашифрованим разом із назвою
оголошення й номерами оголошень з листа. Тут лише шукаємо картку в карті карток (research/cardmap.py; стани ram_mail і
«поділитися» з кешу GitHub) і надсилаємо як відповідь на неї. Не знайшли — окреме повідомлення, як раніше.
У лог (репозиторій публічний) — лише «реплай так/ні».

    python research/ka_reply.py --blob … --mac … --nonce … --ka ram_mail_state.json --share ka_share_state.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cardmap
import config
import sell


def send(text: str, markup, reply_to: int | None) -> int:
    import requests
    data = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if markup:
        data["reply_markup"] = markup if isinstance(markup, str) else json.dumps(markup, ensure_ascii=False)
    if reply_to:   # картку видалили — Telegram усе одно надішле, просто без цитати
        data["reply_parameters"] = json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})
    r = requests.post(f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage", data=data, timeout=15)
    return r.status_code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blob", required=True)
    ap.add_argument("--mac", required=True)
    ap.add_argument("--nonce", required=True)
    ap.add_argument("--ka")
    ap.add_argument("--share")
    a = ap.parse_args()
    d = sell.unseal(a.blob, a.mac, os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN or "", a.nonce)
    cards = {**(cardmap.load(a.share) if a.share else {}), **(cardmap.load(a.ka) if a.ka else {})}
    mid = cardmap.find(cards, d.get("ids") or [], d.get("listing") or "")
    code = send(d.get("text") or "", d.get("markup"), mid)
    print(f"карток у пам'яті: {len(cards)}, реплай: {'так' if mid else 'ні'}, Telegram: {code}")
    if code != 200:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
