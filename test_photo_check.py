"""Тести перевірки фото (research/photo_check.py): що ми очікуємо і як порівнюємо з відповіддю моделі. Без мережі."""
import os as _os
_os.environ.setdefault("RAM_PRICES_OFF", "1")   # цифри Terapeak, а не щоденні ціни сторожа (research/ram_prices.json)
import sys
import unittest

sys.path.insert(0, ".")
sys.path.insert(0, "research")
from console_alert import evaluate_console
from html import escape

from photo_check import SERVER_MSG, add_to_card, compare, expectation, ka_images
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

    def test_server_ram_on_photo(self):
        # 03.10: KA «SK Hynix 2x 16GB DDR4 RAM PC4-2666V» за 85 € — на наклейці HMA82GR7AFR8N, PC4-2666V-RE1-11 (RDIMM)
        exp = expectation(evaluate("SK Hynix 2x 16GB DDR4 RAM PC4-2666V Arbeitsspeicher", 85))
        ok = {"ram_gb_per_module": 16, "ram_modules_visible": 2, "ram_form": "desktop", "ram_gen": "DDR4", "confidence": "high"}
        self.assertEqual(compare(exp, dict(ok, ram_server=False, label_text="16GB 2Rx8 PC4-2666V-UA2-11"))[0], [])
        self.assertIn(SERVER_MSG, compare(exp, dict(ok, ram_server=True))[0][0])
        self.assertIn(SERVER_MSG, compare(exp, dict(ok, label_text="16GB 2Rx8 PC4 - 2666V - RE1 - 11"))[0][0])   # модель сказала «ні»
        self.assertIn(SERVER_MSG, compare(exp, dict(ok, label_text="HMA82GR7AFR8N-VK TF AC"))[0][0])

    def test_server_card_folded_only_for_subscriptions(self):
        import requests

        import photo_check
        sent = []
        saved = (photo_check.photo_line, requests.post)
        photo_check.photo_line = lambda *a: ("📷 <b>⛔ ФОТО НЕ ЗБІГАЄТЬСЯ З НАЗВОЮ:</b> " + escape(SERVER_MSG) + " — …", True)
        requests.post = lambda url, data=None, **k: (sent.append(data), type("R", (), {"status_code": 200})())[1]
        try:
            res = {"price": 85}
            add_to_card(7, "КАРТКА", '{"inline_keyboard": []}', res, "SK Hynix 2x 16GB DDR4", ["u"], collapse_server=True)
            add_to_card(8, "КАРТКА", '{"inline_keyboard": []}', res, "SK Hynix 2x 16GB DDR4", ["u"])   # «поділитися»
        finally:
            photo_check.photo_line, requests.post = saved
        self.assertTrue(sent[0]["text"].startswith("🗑 <b>Відсіяно"))
        self.assertNotIn("КАРТКА", sent[0]["text"])
        self.assertNotIn("reply_markup", sent[0])   # кнопки зникають
        self.assertIn("КАРТКА", sent[1]["text"])
        self.assertIn("reply_markup", sent[1])

    def test_server_ram_by_title_and_description(self):
        from ram_alert import refine_by_desc
        self.assertEqual(evaluate("SK Hynix 2x 16GB DDR4 PC4-2666V-RE1 Arbeitsspeicher", 85)["verdict"], "SKIP")
        self.assertEqual(evaluate("Micron 2x16GB 2RX8 PC4-2666V-RE2-12 MTA18ASF2G72PDZ", 85)["verdict"], "SKIP")
        self.assertNotEqual(evaluate("Samsung 2x16GB DDR4 M378A2K43EB1-CWE PC4-3200AA-UA2-11", 85)["verdict"], "SKIP")
        t = "SK Hynix 2x 16GB DDR4 RAM PC4-2666V Arbeitsspeicher"
        r = evaluate(t, 85)
        self.assertNotEqual(r["verdict"], "SKIP")
        desc = ("Modellbezeichnung: HMA82GR7AFR8N-VK TF AC - Bauform: 288-pin DIMM. Er eignet sich hervorragend, "
                "um den Arbeitsspeicher deines PCs aufzurüsten. Preis pro Stück.")
        self.assertIn("серверна", refine_by_desc(r, t, 85, False, desc, evaluate)["reason"])
        self.assertNotEqual(refine_by_desc(r, t, 85, False, "Non-ECC, unbuffered, kein Server RAM. Läuft im Gaming-PC.",
                                           evaluate)["verdict"], "SKIP")

    def test_ecc_unbuffered_is_separate_type(self):
        # 04.10: eBay «2x16GB SK Hynix DDR4 2666 (HMA82GU7CJR8N)» — ECC UDIMM (U7, наклейка PC4-2666V-EE1), не серверна
        r = evaluate("2x16GB SK Hynix DDR4 RAM 2666 MHz PC4-2666V Arbeitsspeicher (HMA82GU7CJR8N)", 65.69)
        plain = evaluate("SK Hynix 2x16GB DDR4 RAM 2666 MHz PC4-2666V Arbeitsspeicher", 65.69)
        self.assertIn("ECC UDIMM", r["type"])
        self.assertTrue(r.get("ecc_udimm") and r["notes"])
        self.assertLess(r["quick_sale"], plain["quick_sale"])   # обережніше за звичайну
        for t in ["Kingston 32GB 2x16 GB DDR4-2400 DIMM, ungepuffert, MIT ECC", "2x16GB DDR4 ECC UDIMM 2666",
                  "32GB Kit (2x16GB) Samsung DDR4-2666 ECC UDIMM M391A2K43BB1-CTD", "16GB SK hynix ECC UDIMM Server RAM 2x16GB DDR4"]:
            self.assertIn("ECC UDIMM", evaluate(t, 40).get("type", ""), t)
        for t in ["DDR4 32GB 2x16 Unbuffered, Non-ECC Corsair", "DDR4 32GB 2x16 unbuffered non ECC"]:
            self.assertNotIn("ECC", evaluate(t, 40).get("type", ""), t)
        for t in ["Samsung 32GB 2x16 DDR4 ECC Registered RDIMM", "2x16GB 2Rx8 PC4-2666V-RE1-11 DDR4", "2x16GB DDR4 2666 ECC RAM"]:
            self.assertEqual(evaluate(t, 40)["verdict"], "SKIP", t)   # Registered і «просто ECC» — як раніше
        exp = expectation(r)
        ok = {"ram_gb_per_module": 16, "ram_modules_visible": 2, "ram_form": "desktop", "ram_gen": "DDR4", "confidence": "high"}
        self.assertEqual(compare(exp, dict(ok, ram_server=True, ram_ecc_unbuffered=True,
                                           label_text="16GB 2Rx8 PC4-2666V-EE1-11 HMA82GU7CJR8N-VK"))[0], [])
        self.assertEqual(compare(exp, dict(ok, ram_server=True, label_text="16GB 2Rx8 PC4-2666V-EE1-11"))[0], [])

    def test_ka_images(self):
        page = ('x https://img.kleinanzeigen.de/api/v1/prod-ads/images/35/35a26f80-95c0-4f6c-b0e1-53177f8aa286?rule=$_59.AUTO '
                'https://img.kleinanzeigen.de/api/v1/prod-ads/images/35/35a26f80-95c0-4f6c-b0e1-53177f8aa286?rule=$_57.AUTO '
                'https://img.kleinanzeigen.de/api/v1/prod-ads/images/b9/b9d94559-d038-4be6-9bc3-6a6f43e3ee59?rule=$_59.JPG')
        self.assertEqual(len(ka_images(page)), 2)
        self.assertTrue(ka_images(page)[0].endswith("?rule=$_57.JPG"))


if __name__ == "__main__":
    unittest.main()
