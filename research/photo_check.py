"""Перевірка фото оголошення через Gemini (безкоштовний рівень Google AI Studio, ключ GEMINI_API_KEY).

29.09: «Corsair Dominator DDR4 32 GB» за €80 — на фото наклейки «8GB (2x4GB)», тобто 4×4 = 16 ГБ; назва брехала.
Картка йде в Telegram одразу, як і раніше; через кілька секунд бот дописує в неї рядок «📷 …» (editMessageText).
Немає ключа, ліміт вичерпано, модель не відповіла — рядка просто не буде, картка лишається як є.

Ручна перевірка:
    python research/photo_check.py https://www.kleinanzeigen.de/s-anzeige/... "назва" ціна
"""
from __future__ import annotations

import base64
import json
import os
import re
import time

# Модель — найновіша Flash за псевдонімом; якщо псевдонім зникне, пробуємо конкретні
MODELS = [m for m in [os.getenv("GEMINI_MODEL"), "gemini-flash-latest", "gemini-flash-lite-latest"] if m]   # решту — discover_models()
API = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
MAX_IMAGES = 3
TIMEOUT = 25
_working = {}

_KA_IMG = re.compile(r"img\.kleinanzeigen\.de/api/v1/prod-ads/images/([0-9a-f]{2}/[0-9a-f-]{36})")


def ka_images(page: str) -> list[str]:
    """Фото з HTML сторінки оголошення KA, у порядку галереї (перше — головне), великий розмір."""
    ids = list(dict.fromkeys(_KA_IMG.findall(page or "")))
    return [f"https://img.kleinanzeigen.de/api/v1/prod-ads/images/{i}?rule=$_59.JPG" for i in ids[:MAX_IMAGES]]


def ebay_images(item: dict) -> list[str]:
    urls = [(item.get("image") or {}).get("imageUrl")] + [x.get("imageUrl") for x in item.get("additionalImages") or []]
    return [u for u in dict.fromkeys(urls) if u][:MAX_IMAGES]


# ----------------------------------------------------------------------------- що ми очікуємо (з нашої оцінки)
def expectation(res: dict) -> dict:
    t = res.get("type", "")
    if "total" in res:   # оперативка: «DDR4 UDIMM 32 ГБ (2×16) кіт», «DDR5 SO-DIMM 16 ГБ (одна планка)»
        m = re.search(r"\((\d)×(\d+)\)", t)
        modules, per = (int(m.group(1)), int(m.group(2))) if m else (1, res["total"])
        if res.get("kit_unknown"):
            modules, per = 2, res["total"] // 2
        return dict(kind="ram", gen=t[:4].upper(), laptop="SO-DIMM" in t, total=res["total"], modules=modules, per=per)
    for key, name in [("Series X", "xbox series x"), ("Switch 2", "switch 2"), ("OLED", "switch oled"), ("Lite", "switch lite"),
                      ("V1/V2", "switch v1/v2"), ("Digital", "ps5 digital"), ("PS5", "ps5 disc")]:
        if key in t:
            return dict(kind="console", model=name)
    return dict(kind="other")


PROMPT = """You inspect photos of a used item from a German classifieds listing. Title: "{title}".
Look ONLY at the photos (not the title) and answer with JSON:
{{"shown": "short description of what the photos show",
  "label_text": "exact text you can read on RAM module stickers (part number, capacity line like 8GB (2x4GB)), else empty",
  "ram_modules_visible": number of distinct RAM sticks visible in one photo (max over photos) or null,
  "ram_gb_per_module": capacity of ONE stick in GB or null. A Corsair/G.Skill kit sticker like "8GB (2x4GB)" or "32GB (2x16GB)"
      is printed on every stick of the kit and means per-stick = the number inside the parentheses (4 or 16),
  "ram_form": "desktop" (long DIMM, 288/240 pins) or "laptop" (short SO-DIMM) or null,
  "ram_gen": "DDR3" | "DDR4" | "DDR5" | null,
  "console_model": one of "xbox series x", "xbox series s", "xbox one", "ps5 disc", "ps5 digital", "ps5 pro", "ps4",
      "switch 2", "switch oled", "switch v1/v2", "switch lite", "other", or null if no console is visible.
      PS5 disc = has a disc slot/drive bulge; Series X = tall black tower, Series S = small white box,
  "only_box_or_accessory": true if the photos show only packaging, a controller or another accessory but no console / no RAM stick,
  "visible_damage": true if cracks, burn marks, missing parts or bent pins are clearly visible,
  "confidence": "high" | "medium" | "low"}}"""


def _download(url: str) -> bytes | None:
    import requests
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return r.content if r.status_code == 200 and len(r.content) > 1000 else None
    except Exception:
        return None


def discover_models(key: str) -> list[str]:
    """Доступні цьому ключу Flash-моделі з підтримкою зображень (29.09: 2.5/2.0 уже «no longer available to new users»).
    Найновіші — першими; без TTS/image-generation/live/embedding."""
    if "list" in _working:
        return _working["list"]
    import requests
    names = []
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": key},
                         params={"pageSize": 200}, timeout=10)
        for m in r.json().get("models", []):
            n = m.get("name", "").split("/")[-1]
            if ("flash" in n and "generateContent" in (m.get("supportedGenerationMethods") or [])
                    and not re.search(r"tts|image|live|embed|audio|thinking-exp|native", n)):
                names.append(n)
    except Exception as e:
        print(f"   [фото] список моделей: {e.__class__.__name__}")

    def ver(n):
        v = re.search(r"(\d+(?:\.\d+)?)", n)
        return (float(v.group(1)) if v else 0, "lite" not in n, "preview" not in n and "exp" not in n)
    _working["list"] = sorted(names, key=ver, reverse=True)
    return _working["list"]


def ask_gemini(images: list[bytes], prompt: str, key: str | None = None) -> dict | None:
    import requests
    key = key or os.getenv("GEMINI_API_KEY")
    if not key or not images:
        return None
    parts = [{"text": prompt}] + [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(b).decode()}}
                                  for b in images]
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    order = ([_working["m"]] if "m" in _working else []) + [m for m in MODELS + discover_models(key)
                                                            if m != _working.get("m")]
    order = list(dict.fromkeys(order))[:8]
    t_end = time.time() + 40   # картка вже в Telegram; довше за ~40 с рядок «📷» не чекаємо
    for model in order + order[:1]:   # 503 «high demand» (29.09) — інша модель, потім ще раз перша
        if time.time() > t_end:
            break
        try:
            r = requests.post(API.format(model), headers={"x-goog-api-key": key}, json=body, timeout=TIMEOUT)
        except Exception as e:
            print(f"   [фото] {model}: {e.__class__.__name__}")
            continue
        if r.status_code in (400, 404, 500, 503):   # моделі немає / перевантажена — наступна
            print(f"   [фото] {model}: {r.status_code} {r.text[:120]}")
            time.sleep(1)
            continue
        if r.status_code != 200:   # 429 — ліміт безкоштовного рівня; картка лишається без рядка
            print(f"   [фото] {model}: {r.status_code} {r.text[:160]}")
            return None
        _working["m"] = model
        try:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip()).strip())
        except Exception:
            print(f"   [фото] {model}: не JSON: {r.text[:160]}")
            return None
    return None


# ----------------------------------------------------------------------------- порівняння
def compare(exp: dict, ph: dict) -> tuple[list[str], list[str]]:
    """→ (розбіжності, попередження)."""
    bad, warn = [], []
    if not ph or ph.get("confidence") == "low":
        return bad, warn
    if ph.get("only_box_or_accessory"):
        bad.append("на фото лише коробка / аксесуар, самого товару не видно")
    if ph.get("visible_damage"):
        warn.append("на фото видно пошкодження")
    if exp["kind"] == "ram":
        per, vis = ph.get("ram_gb_per_module"), ph.get("ram_modules_visible")
        if isinstance(per, int) and per and per != exp["per"]:
            n = vis if isinstance(vis, int) and vis > 1 else None
            bad.append(f"на наклейці {per} GB на планку" + (f", планок видно {n} → разом {n * per} GB" if n else "")
                       + f" (у назві {exp['total']} GB, ми рахували {exp['modules']}×{exp['per']})")
        elif isinstance(vis, int) and vis > exp["modules"] and exp["modules"] >= 1 and not per:
            warn.append(f"на фото {vis} планки, а ми рахували {exp['modules']} — уточни, що продається")
        form = ph.get("ram_form")
        if form == "laptop" and not exp["laptop"]:
            bad.append("на фото ноутбучні планки (SO-DIMM), а не настільні")
        elif form == "desktop" and exp["laptop"]:
            bad.append("на фото настільні планки, а не ноутбучні (SO-DIMM)")
        gen = (ph.get("ram_gen") or "").upper()
        if gen in ("DDR3", "DDR4", "DDR5") and gen != exp["gen"]:
            bad.append(f"на наклейці {gen}, а не {exp['gen']}")
    elif exp["kind"] == "console":
        m = (ph.get("console_model") or "").lower()
        if m and m not in ("other", exp["model"]) and not (exp["model"] == "switch v1/v2" and m.startswith("switch")
                                                           and m != "switch 2"):
            bad.append(f"на фото, схоже, {m}, а не {exp['model']}")
    return bad, warn


def photo_line(res: dict, title: str, image_urls: list[str]) -> tuple[str, bool] | None:
    """→ (рядок для картки, чи є розбіжність) або None (немає ключа / фото / відповіді)."""
    from html import escape
    if not os.getenv("GEMINI_API_KEY") or not image_urls:
        return None
    exp = expectation(res)
    if exp["kind"] == "other":
        return None
    t0 = time.time()
    imgs = [b for b in (_download(u) for u in image_urls[:MAX_IMAGES]) if b]
    ph = ask_gemini(imgs, PROMPT.format(title=title.replace('"', "'")[:120]))
    print(f"   [фото] {len(imgs)} фото, {time.time() - t0:.1f} с: {json.dumps(ph, ensure_ascii=False)[:300] if ph else '—'}")
    if not ph:
        return None
    bad, warn = compare(exp, ph)
    label = (ph.get("label_text") or "").strip()
    lbl = f" · наклейка «{escape(label[:60])}»" if label else ""
    if bad:
        return ("📷 <b>⛔ ФОТО НЕ ЗБІГАЄТЬСЯ З НАЗВОЮ:</b> " + escape("; ".join(bad)) + lbl
                + ("\n📷 ⚠️ " + escape("; ".join(warn)) if warn else "") + "\nНе купуй, поки продавець не пояснить.", True)
    if ph.get("confidence") == "low":
        return "📷 Фото нечіткі — модель не впевнена, глянь сам" + lbl, False
    return "📷 Фото збігається з назвою ✓" + lbl + ("\n📷 ⚠️ " + escape("; ".join(warn)) if warn else ""), False


def add_to_card(message_id: int | None, html_text: str, reply_markup: str | None, res: dict, title: str,
                image_urls: list[str]) -> bool:
    """Дописує рядок «📷 …» у вже надіслану картку. Розбіжність — угору картки, «✓» — вниз."""
    if not message_id:
        return False
    got = photo_line(res, title, image_urls)
    if not got:
        return False
    line, bad = got
    import requests

    import config
    text = (line + "\n\n" + html_text) if bad else (html_text + "\n\n" + line)
    data = {"chat_id": config.TELEGRAM_CHAT_ID, "message_id": message_id, "text": text[:4096], "parse_mode": "HTML",
            "disable_web_page_preview": "true"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    try:
        r = requests.post(f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/editMessageText", data=data, timeout=15)
        if r.status_code != 200:
            print(f"   [фото] editMessageText {r.status_code}: {r.text[:160]}")
        return r.status_code == 200
    except Exception as e:
        print(f"   [фото] editMessageText: {e.__class__.__name__}")
        return False


if __name__ == "__main__":
    import sys

    sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.dirname(os.path.abspath(__file__))]
    import requests

    from console_alert import evaluate_console
    from ram_alert import evaluate
    url, title, price = sys.argv[1], sys.argv[2], float(sys.argv[3])
    res = evaluate_console(title, price) or evaluate(title, price)
    if "kleinanzeigen" in url:
        imgs = ka_images(requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "de-DE"}, timeout=15).text)
    else:
        from ebay_watch import _client, ebay_item_id
        from main import _request_with_backoff
        c = _client()
        it = _request_with_backoff("GET", "https://api.ebay.com/buy/browse/v1/item/get_item_by_legacy_id",
                                   headers=c._headers(), params={"legacy_item_id": ebay_item_id(url)})
        imgs = ebay_images(it)
    print("моделі:", discover_models(os.getenv("GEMINI_API_KEY", "")))
    print("очікуємо:", expectation(res), "\nфото:", imgs)
    print(photo_line(res, title, imgs))
