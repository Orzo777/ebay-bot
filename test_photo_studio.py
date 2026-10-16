"""Фото «як у магазині» (16.10): білий фон замість паперу, товар по центру; екран тесту / темний фон — не чіпаємо."""
import io
import sys
import unittest

sys.path.insert(0, ".")
sys.path.insert(0, "research")
import numpy as np
from PIL import Image, ImageDraw

import photo_studio


def jpeg(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "JPEG", quality=92)
    return b.getvalue()


def module_photo() -> bytes:
    """Світло-сірий папір з тінню, дві темні «планки» з білою наклейкою й золотими контактами, порошинка в куті."""
    img = Image.new("RGB", (900, 1200), (196, 194, 190))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 900, 899, 1199], fill=(160, 158, 154))           # тінь на папері
    for y in (350, 560):
        d.rectangle([120, y, 780, y + 170], fill=(25, 25, 28))       # радіатор
        d.rectangle([380, y + 30, 560, y + 80], fill=(235, 235, 235))  # наклейка
        d.rectangle([140, y + 150, 760, y + 168], fill=(190, 160, 70))  # контакти
    d.ellipse([820, 120, 826, 126], fill=(60, 60, 60))               # порошинка
    d.rectangle([430, 60, 470, 95], fill=(90, 90, 95))                # шматок скотчу / крихта (16.10: пройшов у кадр)
    return jpeg(img)


class StudioTest(unittest.TestCase):
    def test_white_background_and_centered(self):
        out = photo_studio.studio(module_photo())
        self.assertIsNotNone(out)
        a = np.asarray(Image.open(io.BytesIO(out)).convert("RGB")).astype(int)
        self.assertEqual(a.shape[:2], (photo_studio.OUT, photo_studio.OUT))
        for y, x in [(10, 10), (10, -10), (-10, 10), (-10, -10)]:
            self.assertGreater(a[y, x].mean(), 245)                   # кути — білі (без паперу й тіні)
        mid = a[photo_studio.OUT // 2 - 200:photo_studio.OUT // 2 + 200, photo_studio.OUT // 2 - 300:photo_studio.OUT // 2 + 300]
        self.assertLess(mid.mean(), 150)                              # товар по центру (порошинка не зсунула кадр)
        top = a[:photo_studio.OUT // 4]
        self.assertGreater(top.min(axis=2).mean(), 240)               # зверху — чисто, без шматка скотчу

    def test_screen_photo_untouched(self):
        img = Image.new("RGB", (900, 1200), (12, 20, 60))             # темний екран MemTest86
        ImageDraw.Draw(img).rectangle([100, 400, 800, 700], fill=(120, 200, 230))
        self.assertIsNone(photo_studio.studio(jpeg(img)))

    def test_fallback_to_original(self):
        import sell
        self.assertEqual(sell.studio_or_original(b"not an image"), b"not an image")


if __name__ == "__main__":
    unittest.main()
