"""Відповідь продавця KA → реплаєм на картку, з якої користувач писав продавцю (02.10); прибирання переписки (06.10).

Apps Script (tools/kafilter.gs) уже зробив текст (суть, переклад, кнопка чату) і передає його зашифрованим разом із назвою
оголошення й номерами оголошень з листа. Тут лише шукаємо картку в карті карток (research/cardmap.py; стани ram_mail і
«поділитися» з кешу GitHub) і надсилаємо як відповідь на неї. Не знайшли — окреме повідомлення, як раніше.

06.10 (прохання користувача — не захаращувати чат): id надісланих відповідей запам'ятовуємо за номером оголошення
(ka_replies_state.json, кеш GitHub). Прибираємо їх, коли:
  • угоду закрито — облік (tools/ledger.gs) записав покупку з KA і передав номер оголошення (--close-*);
  • оголошення зняте / видалене (продано комусь іншому, продавець видалив) — щогодинна перевірка сторінки (--sweep).
Telegram дає видаляти лише повідомлення, молодші за 48 год; старші згортаємо в один рядок «🗑 переписку закрито».
У лог (репозиторій публічний) — лише кількості.

    python research/ka_reply.py --blob … --mac … --nonce … --ka ram_mail_state.json --share ka_share_state.json --replies ka_replies_state.json
    python research/ka_reply.py --close-blob … --mac … --nonce … --replies ka_replies_state.json
    python research/ka_reply.py --sweep --replies ka_replies_state.json
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cardmap
import config
import sell

KEEP_DAYS = 14        # далі відповіді вже давно згорнуті або нікому не заважають
SWEEP_MAX = 30        # сторінок KA за одну перевірку (одна сторінка на оголошення, як і для карток)
KA_AD = "https://www.kleinanzeigen.de/s-anzeige/"


def _tg(method: str, data: dict):
    import requests
    return requests.post(f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/{method}", data=data, timeout=15)


def send(text: str, markup, reply_to: int | None) -> tuple[int, int | None]:
    data = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if markup:
        data["reply_markup"] = markup if isinstance(markup, str) else json.dumps(markup, ensure_ascii=False)
    if reply_to:   # картку видалили — Telegram усе одно надішле, просто без цитати
        data["reply_parameters"] = json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})
    r = _tg("sendMessage", data)
    try:
        mid = (r.json().get("result") or {}).get("message_id")
    except Exception:
        mid = None
    return r.status_code, mid


def load_replies(path: str | None) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("replies") or {}
    except (OSError, ValueError):
        return {}


def save_replies(path: str | None, replies: dict, now: datetime):
    if not path:
        return
    cut = (now - timedelta(days=KEEP_DAYS)).isoformat()
    replies = {k: v for k, v in replies.items() if v.get("ts", "") >= cut}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"replies": replies}, fh, ensure_ascii=False)


def remember(replies: dict, ids: list, message_id: int | None, now: datetime):
    if not message_id:
        return
    for aid in {str(i) for i in ids or [] if str(i).isdigit()}:
        rec = replies.setdefault(aid, {"m": [], "ts": now.isoformat()})
        if message_id not in rec["m"]:
            rec["m"].append(int(message_id))
        rec["ts"] = now.isoformat()


def close(replies: dict, ad_ids: list, why: str) -> int:
    """Прибирає відповіді по цих оголошеннях: видалити (до 48 год) або згорнути в один рядок. → скільки прибрано."""
    n, done = 0, set()
    for aid in [str(a) for a in ad_ids or []]:
        rec = replies.pop(aid, None)
        for mid in (rec or {}).get("m", []):
            if mid in done:   # одна відповідь могла бути записана під кількома номерами з листа
                continue
            done.add(mid)
            r = _tg("deleteMessage", {"chat_id": config.TELEGRAM_CHAT_ID, "message_id": mid})
            if r.status_code != 200:   # старше 48 год — Telegram не дає видалити; згортаємо
                _tg("editMessageText", {"chat_id": config.TELEGRAM_CHAT_ID, "message_id": mid, "parse_mode": "HTML",
                                        "text": f"🗑 <i>Переписку закрито: {why}</i>"})
            n += 1
    for k in [k for k, v in replies.items() if set(v.get("m", [])) & done]:   # ті самі повідомлення під іншими номерами
        replies.pop(k, None)
    return n


def sweep(replies: dict, check=None) -> int:
    """Оголошення зняте / видалене → прибрати відповіді. Одна сторінка KA на оголошення, не більше SWEEP_MAX за раз."""
    if check is None:
        from ka_listing_check import check_listing as check
    gone = []
    for aid in sorted(replies, key=lambda k: replies[k].get("ts", ""))[:SWEEP_MAX]:
        res = check(KA_AD + aid, 0, 0)
        if res and res.get("level") == "gone":
            gone.append(aid)
    return close(replies, gone, "оголошення зняте") if gone else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blob")
    ap.add_argument("--close-blob")
    ap.add_argument("--mac")
    ap.add_argument("--nonce")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--ka")
    ap.add_argument("--share")
    ap.add_argument("--replies")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    key = os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN or ""
    replies = load_replies(a.replies)
    if a.sweep:
        n = sweep(replies)
        print(f"відповідей у пам'яті: {len(replies)}, прибрано (оголошення зняте): {n}")
    elif a.close_blob:
        d = sell.unseal(a.close_blob, a.mac, key, a.nonce)
        n = close(replies, d.get("close") or [], d.get("why") or "угоду закрито")
        print(f"прибрано відповідей: {n}")
    else:
        d = sell.unseal(a.blob, a.mac, key, a.nonce)
        cards = {**(cardmap.load(a.share) if a.share else {}), **(cardmap.load(a.ka) if a.ka else {})}
        mid = cardmap.find(cards, d.get("ids") or [], d.get("listing") or "")
        code, sent_id = send(d.get("text") or "", d.get("markup"), mid)
        print(f"карток у пам'яті: {len(cards)}, реплай: {'так' if mid else 'ні'}, Telegram: {code}")
        if code != 200:
            raise SystemExit(1)
        remember(replies, d.get("ids") or [], sent_id, now)
    save_replies(a.replies, replies, now)


if __name__ == "__main__":
    main()
