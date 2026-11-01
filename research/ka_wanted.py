"""Відповіді на НАШЕ оголошення «Suche Arbeitsspeicher» на KA (01.11, користувач: «ось моє оголошення»).

Людина пише «маю 2×16 DDR5 Corsair, хочу 150 €» → Apps Script (tools/kafilter.gs) робить переклад і витягує з
повідомлення товар і ціну (Gemini), шифрує і запускає ka_reply.yml (mode=wanted) → тут оцінка тим самим
ram_alert.evaluate, що й для карток: тип, за скільки продається, заробіток при ціні людини, межа «вигідно до X €».
У бот «Облік і продаж» — картка з готовою відповіддю німецькою (кнопка «скопіювати»). Сам бот НІЧОГО не надсилає на KA
(правило 31.10: жодних повідомлень без згоди користувача), відповідаєш ти в чаті KA.

01.11 (перша справжня відповідь — «DDR4 Riegel 9 €» + фото наклейки): фото з чату KA в лист не потрапляє. Відповідай на
картку фото / скріном наклейки — tools/ledger.gs передає його сюди (photos: file_id Telegram), Gemini читає наклейку
(номер, обсяг планки, DDR, частота) разом із текстом повідомлення («ein Riegel» = одна планка), і картка приходить заново.

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


LABEL_PROMPT = """You see photo(s) a private seller sent in a Kleinanzeigen chat, offering computer RAM to buy. Their message:
---
{msg}
---
Read the RAM module label(s) (sticker) and combine with the message. Return JSON:
{{"part_number": exact part number from the label or "", "label_kit": kit size printed on the label like "16GB (2x8GB)" or "",
  "gen": "DDR4"|"DDR5"|"DDR3"|"", "form": "DIMM"|"SO-DIMM"|"", "speed_mhz": number or null, "brand": "",
  "modules_offered": how many sticks are OFFERED (from the message: "ein Riegel"/"einen Riegel" = 1, "beide"/"2x" = 2;
      if the message does not say — number of sticks visible in the photos),
  "module_gb": capacity of ONE stick (a label "16GB (2x8GB)" means each stick is 8 GB),
  "price_eur": the price the seller asks in the message for everything offered, or null,
  "confidence": "high" if the label is clearly readable, else "low"}}"""


def label_item(facts: dict) -> str | None:
    """Відповідь Gemini про наклейку → назва для ram_alert.evaluate («Corsair DDR4 1x8GB 2400 MHz CMK16…»)."""
    try:
        n, gb = int(facts.get("modules_offered") or 0), int(facts.get("module_gb") or 0)
    except (TypeError, ValueError):
        return None
    gen = str(facts.get("gen") or "").upper()
    if not (n and gb and gen.startswith("DDR")):
        return None
    form = " SO-DIMM" if "SO" in str(facts.get("form") or "").upper() else ""
    speed = f" {int(facts['speed_mhz'])} MHz" if str(facts.get("speed_mhz") or "").isdigit() else ""
    # без номера з наклейки: «CMK16GX4M2A2400C16» парсер читає як набір 2×8, а пропонують одну планку (01.11)
    return f"{facts.get('brand') or ''} {gen}{form} {n}x{gb}GB{speed}".strip()


def label_line(facts: dict) -> str:
    bits = [facts.get("part_number") or "", facts.get("label_kit") and f"на наклейці {facts['label_kit']}" or ""]
    try:
        n = int(facts.get("modules_offered") or 0)
    except (TypeError, ValueError):
        n = 0
    if n:
        bits.append(f"пропонує {n} план.")
    return "📷 З наклейки: " + escape(", ".join(b for b in bits if b))


def assess(d: dict) -> dict:
    """d — з Apps Script: msg (німецькою), uk, item (товар з повідомлення), price, who, link; facts — прочитана наклейка;
    pic — у чаті є фото, якого бот не бачить.
    → {"verdict": buy|haggle|expensive|ask|unknown|wrong, "text": html, "reply": німецька відповідь, "res": оцінка}."""
    msg = (d.get("msg") or "").strip()
    facts = d.get("facts") or {}
    title = label_item(facts) or (d.get("item") or "").strip() or msg[:300]
    try:
        price = float(d.get("price") or facts.get("price_eur") or 0)
    except (TypeError, ValueError):
        price = 0.0
    res = evaluate(title, price if price > 0 else 9999)
    if res.get("verdict") != "UNKNOWN" and msg:   # «Preis pro Riegel», «4x 8GB», «Laptop» — уточнення з тексту (28.10)
        res = refine_by_desc(res, title, price if price > 0 else 9999, False, msg,
                             lambda t, p, vb=False: evaluate(t, p, vb=vb))
    cap = _cap_item(res)
    label = "" if facts or d.get("pic") else " " + LABEL   # фото вже є (бачили ми чи лише ти) — не просимо ще раз
    asked = price
    if price > 0 and res.get("price"):   # «ціна за планку» → refine_by_desc перерахував на весь набір
        price = float(res["price"])
    if res.get("verdict") == "UNKNOWN":
        kind, head = "unknown", "❔ <b>Не зрозумів, яка саме пам'ять</b> — спитай деталі"
        reply = ("Hallo, danke für deine Nachricht! Welche Module genau – DDR4 oder DDR5, wie viel GB, PC oder Laptop?"
                 + label + ("" if price > 0 else " Und was stellst du dir preislich vor?"))
    elif not cap:   # DDR3, сервер, тип, що не купуємо
        kind, head = "wrong", f"⏭ <b>Не беремо</b> — {escape(res.get('reason') or 'цей тип не купуємо')}"
        reply = "Hallo, danke für dein Angebot! Das suche ich leider nicht – viel Erfolg beim Verkauf."
    elif price <= 0:
        kind, head = "ask", f"💬 <b>Ціну не назвав</b> — вигідно до <b>{cap} €</b> (+ пересилка)"
        reply = "Hallo, danke für dein Angebot! Was stellst du dir preislich vor?" + label
    elif res["verdict"] in SEND_VERDICTS[:3]:
        kind, head = "buy", f"✅ <b>Бери</b> — {price:.0f} € вигідно (межа {cap} €)"
        reply = (f"Hallo, danke für dein Angebot! {price:.0f} € passt für mich.{label} "
                 "Abholung in Hamburg oder Versand – was ist dir lieber? Versand zahle ich per PayPal Waren & Dienstleistungen.")
    elif res["verdict"] == "NEGOTIATE":
        offer = offer_price(res) or cap
        kind, head = "haggle", f"🤝 <b>Торгуйся</b> — просить {price:.0f} €, пропонуй <b>{offer} €</b> (межа {cap} €)"
        reply = (f"Hallo, danke für dein Angebot! Ich könnte dir {offer} € anbieten (Versand zahle ich extra)." + label)
    else:
        kind, head = "expensive", f"⏭ <b>Дорого</b> — просить {price:.0f} €, вигідно лише до <b>{cap} €</b>"
        reply = (f"Hallo, danke für dein Angebot! Das ist leider über meinem Budget – mehr als {cap} € (zzgl. Versand) "
                 "kann ich nicht zahlen. Wenn das für dich passt, melde dich gern.")
    lines = ["📥 <b>Пропонують пам'ять</b>" + (f" · {escape(d['who'])}" if d.get("who") else ""),
             "<i>на твоє «Suche Arbeitsspeicher»</i>", "", head]
    if facts:
        lines.append(label_line(facts))
    if res.get("type"):
        lines.append(f"🧩 {escape(res['type'])} · продається {res['quick_sale']}–{res['median_sale']} €")
    if price != asked and res.get("reason"):
        lines.append(f"📝 {escape(res['reason'])}")
    if price > 0 and res.get("net_q") and kind in ("buy", "haggle", "expensive"):
        cost = total_for(res, price)
        lines.append(f"💶 За {price:.0f} € заробіток ≈ {res['net_q'] - cost:.0f} € ({100 * (res['net_q'] - cost) / cost:.0f}%)")
    if res.get("single_module_warning") and kind != "wrong" and not facts:
        lines.append("⚠️ Одна планка чи набір — з тексту не видно; фото наклейки покаже.")
    if d.get("uk"):
        lines.append("🇺🇦 " + escape(d["uk"][:600]))
    if msg:
        lines.append("🇩🇪 " + escape(msg[:600]))
    if not facts and (d.get("pic") or kind in ("unknown", "ask", "buy", "haggle")):
        lines.append(("📷 У чаті є фото — бот його не бачить. " if d.get("pic") else "📷 ")
                     + "Відповідай на цю картку фото / скріном наклейки — прочитаю й перерахую.")
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


def tg_photo(file_id: str, token: str) -> bytes | None:
    """Фото з бота «Облік і продаж» (file_id) → байти."""
    import config
    import requests
    try:
        r = requests.get(f"{config.TELEGRAM_API_BASE}/bot{token}/getFile", params={"file_id": file_id}, timeout=15)
        path = (r.json().get("result") or {}).get("file_path")
        if not path:
            return None
        f = requests.get(f"{config.TELEGRAM_API_BASE}/file/bot{token}/{path}", timeout=20)
        return f.content if f.status_code == 200 else None
    except Exception:
        return None


def read_label(images: list[bytes], msg: str, ask=None) -> dict | None:
    if not images:
        return None
    if ask is None:
        from photo_check import ask_gemini as ask
    res = ask(images, LABEL_PROMPT.format(msg=msg[:1000]), budget=60)
    return res if isinstance(res, dict) and label_item(res) else None


def send_office(text: str, markup: dict) -> int:
    import config
    import requests
    token = os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN
    data = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": "true",
            "reply_markup": json.dumps(markup, ensure_ascii=False)}
    r = requests.post(f"{config.TELEGRAM_API_BASE}/bot{token}/sendMessage", data=data, timeout=15)
    if r.status_code == 400:   # старий клієнт без copy_text — картка важливіша за кнопку
        data["reply_markup"] = json.dumps({"inline_keyboard": markup["inline_keyboard"][:1]}, ensure_ascii=False)
        r = requests.post(f"{config.TELEGRAM_API_BASE}/bot{token}/sendMessage", data=data, timeout=15)
    return r.status_code


def main():
    import sell
    ap = argparse.ArgumentParser()
    ap.add_argument("--blob")
    ap.add_argument("--mac")
    ap.add_argument("--nonce")
    a = ap.parse_args()
    import config
    key = os.getenv("OFFICE_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN or ""
    d = sell.unseal(a.blob, a.mac, key, a.nonce)
    photos = [b for b in (tg_photo(f, key) for f in (d.get("photos") or [])[:4]) if b]
    if photos:
        d["facts"] = read_label(photos, d.get("msg") or "") or {}
        if not d["facts"]:
            d["pic"] = False
    out = assess(d)
    if photos and not d.get("facts"):
        out["text"] += "\n⚠️ Наклейку на фото не прочитав — спробуй чіткіше фото або напиши номер з наклейки сам."
    link = d.get("link") if str(d.get("link") or "").startswith("https://www.kleinanzeigen.de/") else None
    code = send_office(out["text"], keyboard(out, link))
    # лог публічний — без тексту і імен
    print(f"пропозиція на Suche: {out['verdict']}, фото {len(photos)}, наклейка {'так' if d.get('facts') else 'ні'}, Telegram {code}")


if __name__ == "__main__":
    main()
