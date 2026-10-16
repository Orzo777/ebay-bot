"""Фото для оголошення «як у магазині» (16.10, прохання користувача): білий фон замість паперу, без тіні й засвіту, товар
по центру квадратного кадру з відступами і легкою тінню під ним. Без нейромереж — лише Pillow + numpy:
фон — світлі ненасичені пікселі, з'єднані з краєм кадру (наклейка на планці оточена радіатором, тож лишається; золоті
контакти насичені — теж лишаються). Фото екрана (MemTest86), темний або строкатий фон — не чіпаємо (None → оригінал).

    python research/photo_studio.py in.jpg out.jpg
"""
import io
import sys

import numpy as np
from PIL import Image, ImageFilter, ImageOps

WORK = 480        # маска рахується на зменшеній копії — швидко
OUT = 1600        # квадрат для eBay (мінімум 500 px, краще ≥ 1600)


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


def background_mask(img: Image.Image) -> np.ndarray | None:
    """→ маска фону (True — фон) на розмірі WORK або None, якщо фон не світлий і рівний."""
    small = img.convert("RGB").copy()
    small.thumbnail((WORK, WORK))
    a = np.asarray(small).astype(np.int16)
    lum = a.mean(axis=2)
    sat = a.max(axis=2) - a.min(axis=2)
    border = np.concatenate([lum[0], lum[-1], lum[:, 0], lum[:, -1]])
    bsat = np.concatenate([sat[0], sat[-1], sat[:, 0], sat[:, -1]])
    bg_lum = float(np.median(border))
    if bg_lum < 120 or float(np.median(bsat)) > 40 or float(np.std(border)) > 45:
        return None   # темний / кольоровий / строкатий фон (фото екрана тощо)
    allowed = (lum > bg_lum * 0.5) & (sat < 45)   # папір і тінь на ньому; планка темна, контакти — насичені
    # заливаємо по «звуженому» фону: вузькі проходи (виріз між контактами, щілини) не пускають фон усередину планки
    narrow = erode(allowed, 3)
    seed = np.zeros_like(allowed)
    seed[0, :] = seed[-1, :] = seed[:, 0] = seed[:, -1] = True
    bg = dilate(_grow(seed, narrow), 3) & allowed
    # другий прохід: у вузькі щілини (між планками) — лише там, де яскравість точно як у паперу (плата й відблиски темніші
    # або насиченіші — лишаються)
    paper = (lum > 0.72 * bg_lum) & (lum < 1.15 * bg_lum) & (sat < 28)
    bg = _grow(bg, paper | bg)
    fg = erode(dilate(~bg, 2), 2)                 # закрити дрібні дірки в товарі
    fg = dilate(erode(fg, 3), 3)                  # прибрати порошинки й крихти на папері
    bg = ~fg
    share = fg.mean()
    if share < 0.04 or share > 0.85:
        return None
    return bg


def studio(data: bytes) -> bytes | None:
    """JPEG / PNG → JPEG на білому фоні або None (не той випадок — лишаємо оригінал)."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    bg = background_mask(img)
    if bg is None:
        return None
    mask = Image.fromarray(((~bg) * 255).astype(np.uint8)).resize(img.size, Image.BILINEAR)
    mask = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(1.5))   # без ореолу паперу по краю
    box = mask.point(lambda v: 255 if v > 128 else 0).getbbox()
    if not box:
        return None
    pad = int(0.04 * max(img.size))
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(img.width, box[2] + pad), min(img.height, box[3] + pad))
    obj, m = img.crop(box), mask.crop(box)
    obj = ImageOps.autocontrast(obj, cutoff=0.5)   # рівна експозиція без засвіту
    scale = 0.86 * OUT / max(obj.size)
    size = (max(1, int(obj.width * scale)), max(1, int(obj.height * scale)))
    obj, m = obj.resize(size, Image.LANCZOS), m.resize(size, Image.LANCZOS)
    canvas = Image.new("RGB", (OUT, OUT), "white")
    x, y = (OUT - size[0]) // 2, (OUT - size[1]) // 2
    shadow = Image.new("L", (OUT, OUT), 0)   # легка тінь під товаром
    shadow.paste(m.point(lambda v: v * 0.35), (x + int(0.012 * OUT), y + int(0.018 * OUT)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(OUT * 0.012))
    canvas.paste(Image.new("RGB", (OUT, OUT), (150, 150, 150)), (0, 0), shadow)
    canvas.paste(obj, (x, y), m)
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


if __name__ == "__main__":
    res = studio(open(sys.argv[1], "rb").read())
    if res is None:
        print("не обробляю (фон не світлий / рівний) — лишається оригінал")
    else:
        open(sys.argv[2], "wb").write(res)
        print("готово:", sys.argv[2])
