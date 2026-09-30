"""Тести перевірки фото (research/photo_check.py): що ми очікуємо і як порівнюємо з відповіддю моделі. Без мережі."""
import sys
import unittest

sys.path.insert(0, ".")
sys.path.insert(0, "research")
from console_alert import evaluate_console
from photo_check import compare, expectation, ka_images
from ram_alert import evaluate


class TestPhotoCheck(unittest.TestCase):
    def test_corsair_4x4_sold_as_32(self):
        # 29.09: «Corsair Dominator RAM DDR 4 32 GB» за €80, на фото чотири планки з наклейкою «8GB (2x4GB)»
        exp = expectation(evaluate("Corsair Dominator RAM DDR 4 32 GB 3200mhz", 80))
        self.assertEqual((exp["total"], exp["modules"], exp["per"]), (32, 2, 16))
        bad, _ = compare(exp, {"ram_gb_per_module": 4, "ram_modules_visible": 4, "ram_form": "desktop", "ram_gen": "DDR4",
                               "confidence": "high"})
        self.assertTrue(bad and "16 GB" in bad[0])

    def test_matching_kit(self):
        exp = expectation(evaluate("Corsair Vengeance DDR5 32GB (2x16GB) 6000", 150))
        bad, warn = compare(exp, {"ram_gb_per_module": 16, "ram_modules_visible": 2, "ram_form": "desktop", "ram_gen": "DDR5",
                                  "confidence": "high"})
        self.assertEqual((bad, warn), ([], []))

    def test_laptop_and_low_confidence(self):
        exp = expectation(evaluate("Crucial DDR5 32GB (2x16GB) 5600", 100))
        self.assertTrue(compare(exp, {"ram_form": "laptop", "confidence": "medium"})[0])
        self.assertEqual(compare(exp, {"ram_form": "laptop", "confidence": "low"}), ([], []))

    def test_console_model(self):
        exp = expectation(evaluate_console("Xbox Series X 1TB Konsole", 300))
        self.assertEqual(exp["model"], "xbox series x")
        self.assertTrue(compare(exp, {"console_model": "xbox series s", "confidence": "high"})[0])
        self.assertFalse(compare(exp, {"console_model": "xbox series x", "confidence": "high"})[0])
        self.assertTrue(compare(exp, {"console_model": None, "only_box_or_accessory": True, "confidence": "high"})[0])
        exp = expectation(evaluate_console("PS5 Digital Edition Slim", 250))
        self.assertEqual(exp["model"], "ps5 digital")

    def test_ka_images(self):
        page = ('x https://img.kleinanzeigen.de/api/v1/prod-ads/images/35/35a26f80-95c0-4f6c-b0e1-53177f8aa286?rule=$_59.AUTO '
                'https://img.kleinanzeigen.de/api/v1/prod-ads/images/35/35a26f80-95c0-4f6c-b0e1-53177f8aa286?rule=$_57.AUTO '
                'https://img.kleinanzeigen.de/api/v1/prod-ads/images/b9/b9d94559-d038-4be6-9bc3-6a6f43e3ee59?rule=$_59.JPG')
        self.assertEqual(len(ka_images(page)), 2)
        self.assertTrue(ka_images(page)[0].endswith("?rule=$_57.JPG"))


if __name__ == "__main__":
    unittest.main()
