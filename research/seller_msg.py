"""Тексти продавцям «як людина» (11.10, прохання користувача).
Які питання ставити, вирішують правила (ram_alert._questions: опис уже відповів — не питаємо); Gemini лише формулює —
перший прогін показав, що flash-lite сама плутається (питала про чек, коли в описі «Rechnung vorhanden»).

Шаблон (ram_alert.buyer_message / ebay_watch.ebay_message) лишається запасним варіантом. Якщо ввімкнено (SELLER_MSG=1 у
робочих процесах і є GEMINI_API_KEY), Gemini переписує його природною німецькою з урахуванням опису: питання —
лише ті, на які опис і назва ще не відповіли (продавець пише «18 Monate im PC, wegen Upgrade ausgebaut» — не питаємо,
чи працює і чи є чек). Відповідь перевіряємо: сума та сама, умови оплати на місці, без контактів / посилань / інших сум;
щось не так — повертаємо шаблон. Тести SELLER_MSG не ставлять — мережі там немає.
"""
import os
import re

_CACHE: dict = {}
_BAD = re.compile(r"https?://|www\.|@|whats\s?app|telegram|telefon|handy|nummer|überweis|freunde|familie|[а-яіїєґ]", re.I)
_EUR = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:€|euro\b|eur\b)", re.I)

PROMPT = """You write a short message from a private buyer to a seller on {platform} in Germany.
Write natural, friendly, casual German like a real person typing on a phone, not a template. {address}
Start with "Hallo" or "Hi".
Item: "{title}", listed at {price} €.
Seller's description (may be empty):
\"\"\"{desc}\"\"\"

The message must:
- {goal}
{reserve}- {terms}
- Questions: {questions}
  These were already checked against the description. Ask them (rephrased naturally); drop one only if the description
  states its answer word for word. Do not add any other question.
- Do not repeat the description back, no flattery, no emojis, no "Sehr geehrte", at most one exclamation mark.
- 2-3 short sentences, at most 230 characters in total.
{amount_rule}- Never suggest paying outside {platform}; no phone numbers, e-mails or links.
Answer with JSON: {{"text": "..."}}"""


def _enabled() -> bool:
    return os.getenv("SELLER_MSG") == "1" and bool(os.getenv("GEMINI_API_KEY"))


def _amounts_ok(text: str, allowed: set[int]) -> bool:
    for a in _EUR.findall(text):
        if round(float(a.replace(",", "."))) not in allowed:
            return False
    return True


def problem(text, channel: str, offer: int | None, price: float, pickup: bool) -> str:
    """'' — текст годиться; інакше коротка причина (для журналу, без самого тексту)."""
    if not isinstance(text, str) or not 30 <= len(text) <= 256:
        return "довжина"
    if _BAD.search(text):
        return "контакти / оплата поза платформою / кирилиця"
    t = text.lower()
    if offer is not None and (str(offer) not in text or not _amounts_ok(text, {offer})):
        return "сума"
    if offer is None and not _amounts_ok(text, {round(price)}):
        return "інша сума"
    if pickup:
        return "" if "hamburg" in t or "abhol" in t else "немає самовивозу"
    if channel == "ka":
        return "" if "sicher bezahlen" in t else "немає «Sicher bezahlen»"
    return "" if "ebay" in t else "немає «eBay»"


def valid(text, channel: str, offer: int | None, price: float, pickup: bool) -> bool:
    return not problem(text, channel, offer, price, pickup)


def humanize(r: dict, offer: int | None, template: str, channel: str, goal: str, terms: str,
             questions: list[str], reserve: bool) -> str:
    """→ текст від Gemini або template. channel: «ka» | «ebay»."""
    if not _enabled():
        return template
    title, price, pickup = str(r.get("title") or "")[:140], float(r.get("price") or 0), bool(r.get("pickup"))
    key = (title, price, offer, channel, pickup)
    if key in _CACHE:
        return _CACHE[key]
    from photo_check import ask_gemini
    prompt = PROMPT.format(
        platform="Kleinanzeigen" if channel == "ka" else "eBay",
        address="Use du." if channel == "ka" else "Use Sie.",
        title=title.replace('"', "'"), price=f"{price:.0f}",
        desc=(r.get("desc") or "")[:1500].replace('"""', "'''"),
        goal=goal, reserve="- Ask them to reserve it for you.\n" if reserve else "", terms=terms,
        questions="; ".join(f'"{q}"' for q in questions) or "(none)",
        amount_rule=f"- Mention the amount {offer} € exactly once and no other amounts.\n" if offer is not None
        else "- Do not mention any other price.\n")
    try:
        got = ask_gemini([], prompt, allow_text=True, budget=12, temperature=0.7)
    except Exception as e:
        print(f"   [текст] {e.__class__.__name__}")
        got = None
    text = re.sub(r"\s+", " ", str((got or {}).get("text") or "")).strip()
    why = problem(text, channel, offer, price, pickup)
    ok = not why
    print(f"   [текст] Gemini: {'ok' if ok else 'шаблон — ' + why} ({len(text)} симв.)")
    _CACHE[key] = text if ok else template
    return _CACHE[key]


if __name__ == "__main__":   # ручна перевірка (photo_check.yml, mode=text): шаблон проти Gemini на типових описах
    import sys
    sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]
    os.environ["SELLER_MSG"] = "1"
    from console_alert import evaluate_console
    from ebay_watch import ebay_message
    from ram_alert import apply_pickup, offer_template, seller_template
    cases = [
        ("Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200", 100, "War 18 Monate in meinem Gaming-PC, wegen Upgrade auf 64 GB "
         "ausgebaut. Lief immer stabil. Versand möglich.", "ka"),
        ("DDR5 RAM 32GB", 200, "Verkaufe Arbeitsspeicher, Privatverkauf, keine Garantie.", "ka"),
        ("Kingston Fury Beast DDR5 32GB 6000", 260, "", "ka"),
        ("Xbox Series X 1TB mit Controller", 450, "Kaum genutzt, gekauft im März bei Saturn. Rechnung vorhanden.", "ka"),
        ("PS5 Slim Disc 1TB", 380, "Konsole funktioniert, wurde aber wenig gespielt.", "ebay"),
        ("G.Skill Ripjaws V 32GB (2x16GB) DDR4", 85, "Abholung in Hamburg-Altona.", "pickup"),
    ]
    for title, price, desc, ch in cases:
        r = evaluate_console(title, price, vb=True) or __import__("ram_alert").evaluate(title, price, vb=True)
        r["desc"] = desc
        if ch == "pickup":
            apply_pickup(r)
        from ram_alert import offer_price
        o = offer_price(r)
        print(f"\n=== {title} · {price} € · {r['verdict']} · пропозиція {o}\nопис: {desc or '—'}")
        os.environ["SELLER_MSG"] = "0"
        def make():
            return ebay_message(r, o) if ch == "ebay" else (offer_template(r) or seller_template(r))
        tpl = make()
        os.environ["SELLER_MSG"] = "1"
        _CACHE.clear()
        print("шаблон:", tpl)
        print("Gemini:", make())
