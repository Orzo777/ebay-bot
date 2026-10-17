"""«🗂 На руках» з фото (17.10, прохання користувача: «я не пам'ятаю, що там за 5 і 6 — очі пам'ятають»).

Одна картинка-аркуш: групи (протестувати / можна продавати / йде до тебе / на eBay / проблема), у кожній — плитки
«фото + №N + назва + скільки днів». Фото — своє з обліку (перше не-скріншот), інакше головне фото оголошення eBay
(виставлене або куплене) чи KA. Без фото — сіра плитка з номером.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont, ImageOps

COLS = 3
TILE_W, PHOTO_H, LABEL_H = 400, 280, 100
PAD, HEAD_H = 14, 56
BG, TILE_BG, PHOTO_BG = (245, 245, 247), (255, 255, 255), (236, 236, 239)
COLORS = {"test": (230, 126, 34), "sell": (39, 174, 96), "way": (52, 152, 219), "ebay": (142, 68, 173), "problem": (192, 57, 43),
          "other": (127, 140, 141)}
_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans{b}.ttf", "C:/Windows/Fonts/DejaVuSans{b}.ttf",
          "/usr/share/fonts/dejavu/DejaVuSans{b}.ttf"]


def _font(size: int, bold: bool = False):
    """→ (шрифт, чи вміє кирилицю). Без DejaVu — вбудований (лише латиниця)."""
    for p in _FONTS:
        p = p.format(b="-Bold" if bold else "")
        if os.path.exists(p):
            return ImageFont.truetype(p, size), True
    try:
        return ImageFont.load_default(size), False
    except TypeError:
        return ImageFont.load_default(), False


def _latin(s: str) -> str:
    return s.replace("№", "#").replace(" дн.", "d").encode("ascii", "ignore").decode()


def _fit(draw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip(" (-–,/") + "…"


def _wrap2(draw, text: str, font, width: int) -> list[str]:
    """До двох рядків по межі слова; решта — «…»."""
    words, lines = text.split(), [""]
    for w in words:
        t = (lines[-1] + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            lines[-1] = t
        elif len(lines) == 1:
            lines.append(w)
        else:
            lines[-1] = _fit(draw, t, font, width)
            break
    return [_fit(draw, x, font, width) for x in lines if x]


def pick_photo(blobs: list[bytes]) -> Image.Image | None:
    """Перше фото на світлому рівному фоні (не скріншот MemTest); інакше перше, що відкрилось."""
    import photo_studio
    first = None
    for b in blobs:
        try:
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(b))).convert("RGB")
        except Exception:
            continue
        if photo_studio.plain_background(im):
            return im
        first = first or im
    return first


def tight(im: Image.Image) -> Image.Image:
    """Обрізати до самого товару (наша маска, без Poof — безкоштовно): планки на весь кадр плитки, а не крихітні посередині."""
    import numpy as np
    import photo_studio
    try:
        bg = photo_studio.background_mask(im)
    except Exception:
        return im
    if bg is None:
        return im
    ys, xs = np.where(~bg)
    if not len(xs):
        return im
    k = im.width / bg.shape[1]
    pad = int(0.05 * max(im.size))
    box = (max(0, int(xs.min() * k) - pad), max(0, int(ys.min() * k) - pad),
           min(im.width, int((xs.max() + 1) * k) + pad), min(im.height, int((ys.max() + 1) * k) + pad))
    return im.crop(box)


def _thumb(im: Image.Image | None, n: int, fonts) -> Image.Image:
    box = Image.new("RGB", (TILE_W, PHOTO_H), PHOTO_BG)
    if im is None:
        d = ImageDraw.Draw(box)
        t = f"№{n}" if fonts["cyr"] else f"#{n}"
        w = d.textlength(t, font=fonts["big"])
        d.text(((TILE_W - w) / 2, PHOTO_H / 2 - 30), t, fill=(160, 160, 165), font=fonts["big"])
        return box
    im = tight(im)
    im.thumbnail((TILE_W - 12, PHOTO_H - 12), Image.LANCZOS)
    box.paste(im, ((TILE_W - im.width) // 2, (PHOTO_H - im.height) // 2))
    return box


def sheet(groups: list[dict]) -> bytes:
    """groups: [{"title", "kind", "items": [{"n", "title", "days", "image": PIL.Image|None}]}] → JPEG."""
    big, cyr = _font(46, True)
    fonts = {"big": big, "cyr": cyr, "head": _font(26, True)[0], "num": _font(24, True)[0], "small": _font(19)[0]}
    groups = [g for g in groups if g.get("items")]
    W = COLS * TILE_W + (COLS + 1) * PAD
    H = PAD + sum(HEAD_H + ((len(g["items"]) + COLS - 1) // COLS) * (PHOTO_H + LABEL_H + PAD) for g in groups)
    canvas = Image.new("RGB", (W, max(H, 200)), BG)
    d = ImageDraw.Draw(canvas)
    y = PAD
    for g in groups:
        color = COLORS.get(g.get("kind"), COLORS["other"])
        d.rounded_rectangle([PAD, y + 6, W - PAD, y + HEAD_H - 8], 10, fill=color)
        head = f"{g['title']} · {len(g['items'])}"
        d.text((PAD + 16, y + 13), head if cyr else _latin(head), fill="white", font=fonts["head"])
        y += HEAD_H
        for i, it in enumerate(g["items"]):
            x = PAD + (i % COLS) * (TILE_W + PAD)
            ty = y + (i // COLS) * (PHOTO_H + LABEL_H + PAD)
            d.rounded_rectangle([x - 1, ty - 1, x + TILE_W, ty + PHOTO_H + LABEL_H], 8, fill=TILE_BG, outline=(220, 220, 225))
            canvas.paste(_thumb(it.get("image"), it["n"], fonts), (x, ty))
            d.rectangle([x, ty + PHOTO_H - 4, x + TILE_W, ty + PHOTO_H], fill=color)
            num = f"№{it['n']}" if cyr else f"#{it['n']}"
            days = f"{it['days']} дн." if it.get("days") is not None else ""
            d.text((x + 10, ty + PHOTO_H + 8), num, fill=color, font=fonts["num"])
            if days:
                dw = d.textlength(days if cyr else _latin(days), font=fonts["small"])
                d.text((x + TILE_W - 10 - dw, ty + PHOTO_H + 12), days if cyr else _latin(days), fill=(120, 120, 125), font=fonts["small"])
            name = str(it.get("title") or "")
            name = name if cyr else _latin(name)
            for j, line in enumerate(_wrap2(d, name, fonts["small"], TILE_W - 20)):
                d.text((x + 10, ty + PHOTO_H + 42 + 25 * j), line, fill=(40, 40, 45), font=fonts["small"])
        y += ((len(g["items"]) + COLS - 1) // COLS) * (PHOTO_H + LABEL_H + PAD)
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=88, optimize=True)
    return out.getvalue()


def build(d: dict, file_fn, key: str, token: str | None, post, get) -> bytes | None:
    """d["groups"] з Apps Script: items {n, title, days, photos: [file_id…], item_id, link} → JPEG аркуша або None."""
    import parcels
    groups = []
    for g in d.get("groups") or []:
        items = []
        for it in g.get("items") or []:
            blobs = []
            for fid in (it.get("photos") or [])[:4]:
                b = file_fn(fid, key)
                if b:
                    blobs.append(b)
            im = pick_photo(blobs)
            if im is None:   # своїх фото немає — головне фото оголошення (виставлене / куплене на eBay, KA)
                url = parcels.picture_for({"id": it.get("item_id") or "", "link": it.get("link") or ""}, None, token, post, get)
                if url:
                    try:
                        r = get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                        im = pick_photo([r.content]) if r.status_code == 200 else None
                    except Exception as e:
                        print(f"фото {it.get('n')}: {e.__class__.__name__}")
            items.append(dict(n=it["n"], title=it.get("title") or "", days=it.get("days"), image=im))
        groups.append(dict(title=g.get("title") or "", kind=g.get("kind") or "other", items=items))
    if not any(g["items"] for g in groups):
        return None
    return sheet(groups)
