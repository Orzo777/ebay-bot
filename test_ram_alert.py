"""Регресійні тести для research/ram_alert.py і research/ram_mail_check.py на РЕАЛЬНИХ назвах
зі сповіщень Kleinanzeigen (22–23.09.2026). Без мережі."""
import os
import sys
import unittest

os.environ["RAM_PRICES_OFF"] = "1"   # фіксовані цифри Terapeak: щотижневе оновлення цін не змінює очікувані вердикти
sys.path.insert(0, ".")
sys.path.insert(0, "research")
from ram_alert import evaluate, format_html, seller_template
from ram_mail_check import _ad_key, build_keyboard


class TestModuleCount(unittest.TestCase):
    def test_4x16_not_mixed_with_2x32(self):
        self.assertEqual(evaluate("Crucial 4x16GB DDR4 2666 RAM Kit 64GB", 65)["verdict"], "SKIP")
        self.assertEqual(evaluate("Crucial 2x32GB DDR4 2666 RAM Kit 64GB", 65)["verdict"], "BUY-EXCELLENT")

    def test_4x8_sodimm_is_skip(self):
        self.assertEqual(evaluate("4x 8GB DDR4 SO-DIMM RAM (Gesamt 32GB) für Laptop / Mini-PC", 65)["verdict"], "SKIP")

    def test_2x8_labelled_16_is_skip(self):
        self.assertEqual(evaluate("16GB DDR5-5600 RAM (2x 8GB) SK hynix SO-DIMM RAM", 90)["verdict"], "SKIP")

    def test_ambiguous_set_title_rejected(self):
        self.assertEqual(evaluate("Crucial 2 / 4 x 16GB DDR4 2666 RAM CT16G4DFD8266 32GB 64GB Set", 65)["verdict"], "UNKNOWN")

    def test_server_ecc_skip(self):
        self.assertEqual(evaluate("64GB (2x 32GB) DDR4-2933 ECC RDIMM Server RAM – Samsung", 90)["verdict"], "SKIP")


class TestAmbiguousKit(unittest.TestCase):
    def test_bare_64gb_ddr4_is_kit_check(self):
        r = evaluate("DDR4 RAM Arbeitsspeicher 64GB Vengeance", 100)
        self.assertTrue(r["verdict"].startswith("BUY"))
        self.assertTrue(r["kit_unknown"])
        self.assertFalse(r["single_module_warning"])
        self.assertIn("2x32", seller_template(r))
        self.assertIn("2×32", format_html(r))

    def test_real_single_16_gets_single_warning(self):
        r = evaluate("SK Hynix 16GB DDR5 SODIMM RAM, 5600MHz, PC5-5600B", 50)
        self.assertEqual(r["verdict"], "BUY-EXCELLENT")
        self.assertTrue(r["single_module_warning"])
        self.assertFalse(r["kit_unknown"])
        self.assertIn("EIN Riegel mit 16 GB", seller_template(r))


class TestNewTypes(unittest.TestCase):
    def test_ddr5_sodimm_2x16_kit(self):
        r = evaluate("Crucial 32GB Kit DDR5-4800 CL40 CT2K16G48C40S5 2x16GB SODIMM", 170)
        self.assertEqual(r["type"], "DDR5 SO-DIMM 32 ГБ (2×16) кіт")
        self.assertEqual(r["verdict"], "BUY")
        self.assertEqual(evaluate("2x16 GB DDR5 RAM SODIMM Arbeitsspeicher", 200)["verdict"], "NEGOTIATE")  # до +15% над стелею
        self.assertEqual(evaluate("2x16 GB DDR5 RAM SODIMM Arbeitsspeicher", 220)["verdict"], "SKIP")       # далеко над стелею

    def test_ddr5_single_16_desktop(self):
        r = evaluate("Kingston FURY Beast DDR5 16GB 5200MT/s CL40 DIMM", 90)
        self.assertEqual(r["type"], "DDR5 UDIMM 16 ГБ (одна планка)")
        self.assertTrue(r["verdict"].startswith("BUY"))
        self.assertTrue(r["single_module_warning"])

    def test_not_added_types_still_skip(self):
        for t, p in [("Corsair Vengeance DDR5 48GB 2x24GB 6000", 150), ("Samsung 8GB DDR5-5600 SO-DIMM", 20),
                     ("Crucial DDR5 96GB Kit 2x48GB 5600", 200), ("Micron 16gb DDR5 sodimm 5600 2x 8Gb", 30)]:
            self.assertEqual(evaluate(t, p)["verdict"], "SKIP", t)


class TestPrices(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(evaluate("Samsung 64 GB SO-DIMM DDR4 2666 MHz Arbeitsspeicher (2 x 32GB)", 140)["verdict"], "BUY-GOOD")
        self.assertEqual(evaluate("Corsair Vengeance 64GB DDR5-6000 CL30 (2x32GB)", 300)["verdict"], "BUY-GOOD")
        self.assertEqual(evaluate("Crucial DDR5 16GB 5600 SODIMM", 145)["verdict"], "SKIP")


class TestTelegramCard(unittest.TestCase):
    def test_seller_text_fits_copy_button(self):
        for t, p in [("SK Hynix 16GB DDR5 SODIMM RAM, 5600MHz, PC5-5600B", 50),
                     ("DDR4 RAM Arbeitsspeicher 64GB Vengeance", 100),
                     ("Corsair Vengeance DDR5 RAM 32GB (2x16GB)", 180)]:
            s = seller_template(evaluate(t, p))
            self.assertLessEqual(len(s), 256, s)
            kb = build_keyboard("https://www.kleinanzeigen.de/s-anzeige/1", s)
            self.assertEqual(kb["inline_keyboard"][1][0]["copy_text"]["text"], s)

    def test_html_escaped(self):
        r = evaluate("Corsair <Vengeance> DDR5 RAM 32GB (2x16GB) & more", 180)
        h = format_html(r)
        self.assertIn("&lt;Vengeance&gt;", h)
        self.assertIn("&amp;", h)


class TestDedup(unittest.TestCase):
    def test_same_ad_same_price_same_key(self):
        a = dict(title="x", price=180.0, link="https://www.kleinanzeigen.de/s-anzeige/3520593662")
        b = dict(title="x (інший лист)", price=180.0, link="https://www.kleinanzeigen.de/s-anzeige/3520593662")
        self.assertEqual(_ad_key(a), _ad_key(b))

    def test_price_drop_is_new(self):
        a = dict(title="x", price=180.0, link="https://www.kleinanzeigen.de/s-anzeige/3520593662")
        b = dict(title="x", price=150.0, link="https://www.kleinanzeigen.de/s-anzeige/3520593662")
        self.assertNotEqual(_ad_key(a), _ad_key(b))


if __name__ == "__main__":
    unittest.main()
