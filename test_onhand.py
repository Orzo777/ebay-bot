"""«🗂 На руках» з фото (17.10): аркуш — групи з плитками «фото + №N + назва + дні»; без фото — сіра плитка з номером."""
import io
import sys
import unittest

sys.path.insert(0, ".")
sys.path.insert(0, "research")
from PIL import Image, ImageDraw

import onhand


def jpeg(img):
    b = io.BytesIO()
    img.save(b, "JPEG", quality=90)
    return b.getvalue()


def module_photo():
    img = Image.new("RGB", (900, 1200), (196, 194, 190))
    d = ImageDraw.Draw(img)
    for y in (350, 560):
        d.rectangle([120, y, 780, y + 170], fill=(25, 25, 28))
        d.rectangle([140, y + 150, 760, y + 168], fill=(190, 160, 70))
    return jpeg(img)


def screen_photo():
    img = Image.new("RGB", (900, 600), (12, 20, 60))
    ImageDraw.Draw(img).rectangle([100, 100, 800, 500], fill=(120, 200, 230))
    return jpeg(img)


class OnHandTest(unittest.TestCase):
    def test_pick_photo_skips_screen(self):
        im = onhand.pick_photo([screen_photo(), module_photo()])
        self.assertEqual(im.size, (900, 1200))                       # скріншот MemTest пропущено
        self.assertEqual(onhand.pick_photo([screen_photo()]).size, (900, 600))   # лише скріншот — краще, ніж нічого
        self.assertIsNone(onhand.pick_photo([b"junk"]))

    def test_sheet_layout(self):
        g = [dict(title="Протестувати", kind="test", items=[dict(n=5, title="Corsair 32GB DDR4-2400", days=6,
                                                                  image=onhand.pick_photo([module_photo()])),
                                                             dict(n=7, title="SK Hynix DDR5", days=6, image=None)]),
             dict(title="На eBay, чекає покупця", kind="ebay", items=[dict(n=4, title="Corsair 2133", days=6, image=None)])]
        im = Image.open(io.BytesIO(onhand.sheet(g)))
        self.assertEqual(im.width, onhand.COLS * onhand.TILE_W + (onhand.COLS + 1) * onhand.PAD)
        self.assertEqual(im.height, onhand.PAD + 2 * (onhand.HEAD_H + onhand.PHOTO_H + onhand.LABEL_H + onhand.PAD))
        px = im.convert("RGB").getpixel((onhand.PAD + 5, onhand.PAD + 25))
        self.assertGreater(px[0], 180)                               # помаранчева смуга «протестувати»

    def test_build_fetches_photos_and_falls_back(self):
        calls = []
        d = {"groups": [{"title": "Можна продавати", "kind": "sell", "items": [
            {"n": 1, "title": "OWC", "days": 18, "photos": ["f1", "f2"]}, {"n": 2, "title": "X", "days": 1, "photos": []}]}]}
        out = onhand.build(d, lambda fid, k: calls.append(fid) or module_photo(), "k", None, None, lambda *a, **k: None)
        self.assertTrue(out.startswith(b"\xff\xd8"))
        self.assertEqual(calls, ["f1", "f2"])
        self.assertIsNone(onhand.build({"groups": []}, None, "k", None, None, None))


if __name__ == "__main__":
    unittest.main()
