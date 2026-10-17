"""Фото для оголошення «як у магазині» (16.10, прохання користувача): білий фон замість паперу, без тіні й засвіту, товар
по центру квадратного кадру з відступами і легкою тінню під ним. Без нейромереж — лише Pillow + numpy:
фон — світлі ненасичені пікселі, з'єднані з краєм кадру (наклейка на планці оточена радіатором, тож лишається; золоті
контакти насичені — теж лишаються). Фото екрана (MemTest86), темний або строкатий фон — не чіпаємо (None → оригінал).
17.10 (користувач: «знайди сервіс з результатом як Pixelcut, безкоштовний»): контур товару — Poof.bg (100 фото на місяць
безкоштовно, повна роздільність); ключ POOF_KEY у секретах GitHub. Без ключа, при збої чи дивній масці — наша маска.
Маски кешуються (PHOTO_CACHE, sell.yml: actions/cache): прев'ю й публікація того самого фото — один запит.

    python research/photo_studio.py in.jpg out.jpg
"""
import hashlib
import io
import os
import sys
import time

import numpy as np
from PIL import Image, ImageFilter, ImageOps

WORK = 900        # маска — на зменшеній копії (16.10: 480 давало рвані краї на стиснутих фото з Telegram)
K = WORK // 160   # радіуси звуження / розширення під цей розмір
OUT = 1600        # квадрат для eBay (мінімум 500 px, краще ≥ 1600)
POOF_URL = "https://api.poof.bg/v1/remove"
POOF_PER_MIN = 4  # безкоштовний план: 4 запити на хвилину
_calls: list[float] = []


def _grow(seed: np.ndarray, allowed: np.ndarray, max_iter: int = 2000) -> np.ndarray:
    """Заливка від seed по allowed (4-сусідство) — без scipy."""
    m = seed & allowed
    for _ in range(max_iter):
        g = m.copy()
        g[1:, :] |= m[:-1, :]
        g[:-1, :] |= m[1:, :]
        g[:, 1:] |= m[:, :-1]
        g[:, :-1] |= m[:, 1:]
        g &= allowed
        if (g == m).all():
            break
        m = g
    return m


def _shift_or(m: np.ndarray) -> np.ndarray:
    g = m.copy()
    g[1:, :] |= m[:-1, :]
    g[:-1, :] |= m[1:, :]
    g[:, 1:] |= m[:, :-1]
    g[:, :-1] |= m[:, 1:]
    return g


def dilate(m: np.ndarray, k: int) -> np.ndarray:
    for _ in range(k):
        m = _shift_or(m)
    return m


def erode(m: np.ndarray, k: int) -> np.ndarray:
    return ~dilate(~m, k)


def keep_big(fg: np.ndarray, share: float = 0.12) -> np.ndarray:
    """Лише великі об'єкти (планки, консоль): усе, що менше за share від найбільшого, — у фон (крихти, клаптики скотчу)."""
    rest, comps = fg.copy(), []
    while rest.any() and len(comps) < 40:
        seed = np.zeros_like(rest)
        seed[np.unravel_index(np.argmax(rest), rest.shape)] = True
        comp = _grow(seed, rest)
        comps.append(comp)
        rest &= ~comp
    if not comps:
        return fg
    big = max(c.sum() for c in comps)
    out = np.zeros_like(fg)
    for c in comps:
        if c.sum() >= share * big:
            out |= c
    return out


def plain_background(img: Image.Image) -> bool:
    """Світлий рівний фон по краях кадру (папір, стіл). Екран MemTest86, темний / строкатий фон — False."""
    small = img.convert("RGB").copy()
    small.thumbnail((WORK, WORK))
    a = np.asarray(small).astype(np.int16)
    lum = a.mean(axis=2)
    sat = a.max(axis=2) - a.min(axis=2)
    border = np.concatenate([lum[0], lum[-1], lum[:, 0], lum[:, -1]])
    bsat = np.concatenate([sat[0], sat[-1], sat[:, 0], sat[:, -1]])
    return not (float(np.median(border)) < 120 or float(np.median(bsat)) > 40 or float(np.std(border)) > 45)


def _pace() -> None:
    now = time.time()
    _calls[:] = [t for t in _calls if now - t < 60]
    if len(_calls) >= POOF_PER_MIN:
        time.sleep(61 - (now - _calls[0]))
    _calls.append(time.time())


def poof_mask(img: Image.Image, key: str, post, cache: str = "") -> Image.Image | None:
    """Poof.bg → маска товару (L, розмір img; 255 — товар) або None (немає ключа, ліміт, збій, дивна маска)."""
    if not key or post is None:
        return None
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)   # уже повернуте за EXIF — маска збігається з кадром
    data = buf.getvalue()
    path = os.path.join(cache, hashlib.sha1(data).hexdigest() + ".png") if cache else ""
    if path and os.path.exists(path):
        try:
            return Image.open(path).convert("L").resize(img.size, Image.LANCZOS)
        except Exception:
            pass
    r = None
    for _ in range(3):
        _pace()
        try:
            r = post(POOF_URL, headers={"x-api-key": key}, files={"image_file": ("photo.jpg", data, "image/jpeg")}, timeout=60)
        except Exception as e:
            print(f"Poof: {e.__class__.__name__}")
            return None
        if r.status_code != 429:
            break
    if r is None or r.status_code != 200:
        print(f"Poof: HTTP {getattr(r, 'status_code', '?')} — наша обробка")   # 402/403 — скінчились безкоштовні 100
        return None
    try:
        out = Image.open(io.BytesIO(r.content))
        if "A" not in out.getbands():
            return None
        m = out.getchannel("A").resize(img.size, Image.LANCZOS)
    except Exception:
        return None
    share = float((np.asarray(m) > 128).mean())
    if not 0.04 <= share <= 0.85:
        return None   # вирізав майже все або нічого — не довіряємо
    if path:
        try:
            os.makedirs(cache, exist_ok=True)
            m.save(path, "PNG", optimize=True)
        except OSError:
            pass
    return m


def background_mask(img: Image.Image) -> np.ndarray | None:
    """→ маска фону (True — фон) на розмірі WORK або None, якщо фон не світлий і рівний."""
    if not plain_background(img):
        return None   # темний / кольоровий / строкатий фон (фото екрана тощо)
    small = img.convert("RGB").copy()
    small.thumbnail((WORK, WORK))
    a = np.asarray(small).astype(np.int16)
    lum = a.mean(axis=2)
    sat = a.max(axis=2) - a.min(axis=2)
    # 16.10 (фото №4: тьмяне світло, градієнт, темна тінь від лампи): яскравість — відносно МІСЦЕВОГО фону, а не середнього.
    # Місцевий фон: максимум у вікні, ширшому за планку (темні предмети зникають), розмитий. Тінь на папері темніша за фон
    # у 1,5–2 рази, чорний радіатор — у 4–6.
    tiny = Image.fromarray(lum.astype(np.uint8)).resize((max(8, lum.shape[1] // 6), max(8, lum.shape[0] // 6)), Image.BILINEAR)
    local = tiny.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.GaussianBlur(4)).resize(small.size, Image.BILINEAR)
    rel = lum / np.maximum(np.asarray(local).astype(np.float32), 1.0)
    # 17.10 (фото №5): посеред широкого чорного радіатора «місцевий фон» — сам радіатор (rel ≈ 1) → дірка в планці.
    # Тож ще й абсолютна яскравість: папір і тінь на ньому — не темніші за 0,4 від фону по краях кадру (радіатор — 0,1–0,2)
    border = np.concatenate([lum[0], lum[-1], lum[:, 0], lum[:, -1]])
    bright = lum > 0.4 * float(np.median(border))
    allowed = (rel > 0.42) & (sat < 45) & bright   # папір і тінь на ньому; планка темна, контакти — насичені
    # заливаємо по «звуженому» фону: вузькі проходи (виріз між контактами, щілини) не пускають фон усередину планки
    narrow = erode(allowed, K)
    seed = np.zeros_like(allowed)
    seed[0, :] = seed[-1, :] = seed[:, 0] = seed[:, -1] = True
    bg = dilate(_grow(seed, narrow), K) & allowed
    # другий прохід: у вузькі щілини (між планками) — лише там, де яскравість точно як у паперу (плата й відблиски темніші
    # або насиченіші — лишаються)
    paper = (rel > 0.36) & (sat < 28) & bright   # і затінений папір між планками (тінь 0,44–0,65; радіатор 0,08–0,17; між контактами від 0,32)
    bg = _grow(bg, paper | bg)
    fg = erode(dilate(~bg, K // 2 + 1), K // 2 + 1)   # закрити дрібні дірки в товарі
    fg = dilate(erode(fg, K), K)                      # прибрати порошинки й крихти на папері
    fg = keep_big(fg)                             # 16.10: і більші сторонні шматки (крихта, скотч) — лише сам товар
    bg = ~fg
    share = fg.mean()
    if share < 0.04 or share > 0.85:
        return None
    return bg


def studio(data: bytes, key: str = "", post=None, cache: str = "") -> bytes | None:
    """JPEG / PNG → JPEG на білому фоні або None (не той випадок — лишаємо оригінал).
    key/post — Poof.bg (контур товару); без них або при збої — наша маска."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    if not plain_background(img):
        return None   # екран тесту тощо — і запит до Poof не витрачаємо
    mask = poof_mask(img, key, post, cache)
    if mask is None:
        bg = background_mask(img)
        if bg is None:
            return None
        mask = Image.fromarray(((~bg) * 255).astype(np.uint8)).resize(img.size, Image.BILINEAR)
        mask = mask.filter(ImageFilter.GaussianBlur(1.2))   # м'який край без ореолу паперу
    box = mask.point(lambda v: 255 if v > 128 else 0).getbbox()
    if not box:
        return None
    pad = int(0.04 * max(img.size))
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(img.width, box[2] + pad), min(img.height, box[3] + pad))
    obj, m = img.crop(box), mask.crop(box)
    obj = ImageOps.autocontrast(obj, cutoff=0.5)   # рівна експозиція без засвіту
    # 16.10: кадр під розмір оригіналу — фото з Telegram (720–1280 px) не розтягуємо до 1600 (було розмито); не менше 800
    side = max(800, min(OUT, int(max(obj.size) / 0.86)))
    scale = 0.86 * side / max(obj.size)
    size = (max(1, int(obj.width * scale)), max(1, int(obj.height * scale)))
    obj, m = obj.resize(size, Image.LANCZOS), m.resize(size, Image.LANCZOS)
    canvas = Image.new("RGB", (side, side), "white")
    x, y = (side - size[0]) // 2, (side - size[1]) // 2
    shadow = Image.new("L", (side, side), 0)   # легка тінь під товаром
    shadow.paste(m.point(lambda v: v * 0.35), (x + int(0.012 * side), y + int(0.018 * side)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(side * 0.012))
    canvas.paste(Image.new("RGB", (side, side), (150, 150, 150)), (0, 0), shadow)
    canvas.paste(obj, (x, y), m)
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


if __name__ == "__main__":
    import requests
    res = studio(open(sys.argv[1], "rb").read(), os.getenv("POOF_KEY", ""), requests.post)
    if res is None:
        print("не обробляю (фон не світлий / рівний) — лишається оригінал")
    else:
        open(sys.argv[2], "wb").write(res)
        print("готово:", sys.argv[2])
