"""Відповіді на НАШЕ оголошення «Suche Arbeitsspeicher» на KA (01.11, користувач: «ось моє оголошення»).

Людина пише «маю 2×16 DDR5 Corsair, хочу 150 €» → Apps Script (tools/kafilter.gs) робить переклад і витягує з
повідомлення товар і ціну (Gemini), шифрує і запускає ka_reply.yml (mode=wanted) → тут оцінка тим самим
ram_alert.evaluate, що й для карток: тип, за скільки продається, заробіток при ціні людини, межа «вигідно до X €».
У бот угод — картка з готовою відповіддю німецькою (кнопка «скопіювати»). Сам бот НІЧОГО не надсилає на KA
(правило 31.10: жодних повідомлень без згоди користувача), відповідаєш ти в чаті KA.

Шахрайські відповіді (PayPal Freunde, WhatsApp…) сюди не доходять — kafilter.gs переносить їх у Кошик.

    python research/ka_wanted.py --blob … --mac … --nonce …
"""
import argparse
import json
import os
import sys
from html import escape

sys.path.insert(0, ".")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ram_alert import (SEND_VERDICTS, SHIP_IN_RAM, evaluate, item_for_cost, offer_price,  # noqa: E402
                       refine_by_desc, total_for)

CHATS = "https://www.kleinanzeigen.de/m-nachrichten.html"
LABEL = "Schick mir bitte noch ein Foto vom Aufkleber (Modell/Teilenummer)."   # перевірка, що це саме той товар
# Telegram: copy_text — до 256 символів; довші тексти — у самій картці
COPY_MAX = 256


def _cap_item(res: dict) -> int:
    """Межа «вигідно до» — ціна товару (без пересилки), округлена вниз до 5 €."""
    return int(item_for_cost(res["cap"], SHIP_IN_RAM) // 5 * 5) if res.get("cap") else 0


def assess(d: dict) -> dict:
    """d — з Apps Script: msg (німецькою), uk, item (товар з повідомлення), price, who, link.
    → {"verdict": buy|haggle|expensive|ask|unknown|wrong, "text": html, "reply": німецька відповідь, "res": оцінка}."""
    msg = (d.get("msg") or "").strip()
    title = (d.get("item") or "").strip() or msg[:300]
    try:
        price = float(d.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    res = evaluate(title, price if price > 0 else 9999)
    if res.get("verdict") != "UNKNOWN" and msg:   # «Preis pro Riegel», «4x 8GB», «Laptop» — уточнення з тексту (28.10)
        res = refine_by_desc(res, title, price if price > 0 else 9999, False, msg,
                             lambda t, p, vb=False: evaluate(t, p, vb=vb))
    cap = _cap_item(res)
    asked = price
    if price > 0 and res.get("price"):   # «ціна за планку» → refine_by_desc перерахував на весь набір
        price = float(res["price"])
    if res.get("verdict") == "UNKNOWN":
        kind, head = "unknown", "❔ <b>Не зрозумів, яка саме пам'ять</b> — спитай деталі"
        reply = ("Hallo, danke für deine Nachricht! Welche Module genau – DDR4 oder DDR5, wie viel GB, PC oder Laptop? "
                 + LABEL + " Und was stellst du dir preislich vor?")
    elif not cap:   # DDR3, сервер, тип, що не купуємо
        kind, head = "wrong", f"⏭ <b>Не беремо</b> — {escape(res.get('reason') or 'цей тип не купуємо')}"
        reply = "Hallo, danke für dein Angebot! Das suche ich leider nicht – viel Erfolg beim Verkauf."
    elif price <= 0:
        kind, head = "ask", f"💬 <b>Ціну не назвав</b> — вигідно до <b>{cap} €</b> (+ пересилка)"
        reply = "Hallo, danke für dein Angebot! Was stellst du dir preislich vor? " + LABEL
    elif res["verdict"] in SEND_VERDICTS[:3]:
        kind, head = "buy", f"✅ <b>Бери</b> — {price:.0f} € вигідно (межа {cap} €)"
        reply = (f"Hallo, danke für dein Angebot! {price:.0f} € passt für mich. {LABEL} "
                 "Abholung in Hamburg oder Versand – was ist dir lieber? Versand zahle ich per PayPal Waren & Dienstleistungen.")
    elif res["verdict"] == "NEGOTIATE":
        offer = offer_price(res) or cap
        kind, head = "haggle", f"🤝 <b>Торгуйся</b> — просить {price:.0f} €, пропонуй <b>{offer} €</b> (межа {cap} €)"
        reply = (f"Hallo, danke für dein Angebot! Ich könnte dir {offer} € anbieten (Versand zahle ich extra). " + LABEL)
    else:
        kind, head = "expensive", f"⏭ <b>Дорого</b> — просить {price:.0f} €, вигідно лише до <b>{cap} €</b>"
        reply = (f"Hallo, danke für dein Angebot! Das ist leider über meinem Budget – mehr als {cap} € (zzgl. Versand) "
                 "kann ich nicht zahlen. Wenn das für dich passt, melde dich gern.")
    lines = ["📥 <b>Пропонують пам'ять</b>" + (f" · {escape(d['who'])}" if d.get("who") else ""),
             "<i>на твоє «Suche Arbeitsspeicher»</i>", "", head]
    if res.get("type"):
        lines.append(f"🧩 {escape(res['type'])} · продається {res['quick_sale']}–{res['median_sale']} €")
    if price != asked and res.get("reason"):
        lines.append(f"📝 {escape(res['reason'])}")
    if price > 0 and res.get("net_q") and kind in ("buy", "haggle", "expensive"):
        cost = total_for(res, price)
        lines.append(f"💶 За {price:.0f} € заробіток ≈ {res['net_q'] - cost:.0f} € ({100 * (res['net_q'] - cost) / cost:.0f}%)")
    if res.get("single_module_warning") and kind != "wrong":
        lines.append("⚠️ Одна планка чи набір — з тексту не видно; фото наклейки покаже.")
    if d.get("uk"):
        lines.append("🇺🇦 " + escape(d["uk"][:600]))
    if msg:
        lines.append("🇩🇪 " + escape(msg[:600]))
    if kind in ("buy", "haggle", "ask"):
        lines.append("Платиш лише PayPal «Waren & Dienstleistungen» (ти захищений як покупець) або готівкою при самовивозі "
                     "після перевірки — ніяких переказів наперед.")
    if len(reply) > COPY_MAX:
        lines.append("📋 <code>" + escape(reply) + "</code>")
    return {"verdict": kind, "text": "\n".join(lines), "reply": reply, "res": res}


def keyboard(a: dict, link: str | None) -> dict:
    rows = [[{"text": "💬 Відкрити чат на Kleinanzeigen", "url": link or CHATS}]]
    if len(a["reply"]) <= COPY_MAX:
        rows.append([{"text": "📋 Скопіювати відповідь", "copy_text": {"text": a["reply"]}}])
    return {"inline_keyboard": rows}


def main():
    import ka_reply
    import sell
    ap = argparse.ArgumentParser()
    ap.add_argument("--blob")
    ap.add_argument("--mac")
    ap.add_argument("--nonce")
    a = ap.parse_args()
    import config
    key = os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN or ""
    d = sell.unseal(a.blob, a.mac, key, a.nonce)
    out = assess(d)
    link = d.get("link") if str(d.get("link") or "").startswith("https://www.kleinanzeigen.de/") else None
    code, _ = ka_reply.send(out["text"], json.dumps(keyboard(out, link), ensure_ascii=False), None)
    print(f"пропозиція на Suche: {out['verdict']}, Telegram {code}")   # лог публічний — без тексту і імен


if __name__ == "__main__":
    main()
