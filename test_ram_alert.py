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
        self.assertEqual(r["verdict"], "BUY-GOOD")   # 50 € + пересилка + Sicher bezahlen ≈ 58 €
        self.assertTrue(r["single_module_warning"])
        self.assertFalse(r["kit_unknown"])
        self.assertIn("EIN Riegel mit 16 GB", seller_template(r))

    def test_description_answers_kit_and_tested(self):
        r = evaluate("DDR4 RAM Arbeitsspeicher 64GB Vengeance", 100)
        r["desc"] = "2x32GB Kit, mit MemTest getestet, ohne Rechnung."
        t = seller_template(r)
        self.assertEqual(t, 'Hallo! Ich nehme den RAM und kaufe sofort – bitte für mich reservieren. '
                            'Ich zahle per „Sicher bezahlen", Versand und Gebühr übernehme ich. Danke!')
        self.assertNotIn("Скільки планок", format_html(r))


class TestNewTypes(unittest.TestCase):
    def test_ddr5_sodimm_2x16_kit(self):
        r = evaluate("Crucial 32GB Kit DDR5-4800 CL40 CT2K16G48C40S5 2x16GB SODIMM", 140)
        self.assertEqual(r["type"], "DDR5 SO-DIMM 32 ГБ (2×16) кіт")
        self.assertEqual(r["verdict"], "BUY")
        self.assertEqual(evaluate("2x16 GB DDR5 RAM SODIMM Arbeitsspeicher", 155)["verdict"], "NEGOTIATE")  # фікс. ціна: до +8%
        self.assertEqual(evaluate("2x16 GB DDR5 RAM SODIMM Arbeitsspeicher", 200)["verdict"], "SKIP")
        self.assertEqual(evaluate("2x16 GB DDR5 RAM SODIMM Arbeitsspeicher", 165, vb=True)["verdict"], "NEGOTIATE")  # VB: до +15%

    def test_ddr5_sodimm_2x32_kit(self):
        # 26.09: поділились у бот Kingston FURY Impact 64GB (2x32) SO-DIMM за 420 € — бот не знав типу
        t = "Kingston FURY Impact 64GB (2x32GB) DDR5-5600 SO-DIMM KF556S40IBK2"
        r = evaluate(t, 420)
        self.assertEqual(r["type"], "DDR5 SO-DIMM 64 ГБ (2×32) кіт")
        self.assertEqual(r["verdict"], "SKIP")                              # ≈445 € разом — заробіток ~17 €
        self.assertTrue(evaluate(t, 270)["verdict"].startswith("BUY"))
        self.assertEqual(evaluate(t, 310, vb=True)["verdict"], "NEGOTIATE")

    def test_types_added_26_09(self):
        # заміряні 26.09, коли з'ясувалось, що їх бракує
        self.assertEqual(evaluate("Corsair Vengeance DDR5 48GB 2x24GB 6000", 150)["type"], "DDR5 UDIMM 48 ГБ (2×24) кіт")
        self.assertEqual(evaluate("Corsair Vengeance DDR5 48GB 2x24GB 6000", 150)["verdict"], "BUY-EXCELLENT")
        self.assertEqual(evaluate("Kingston Fury Beast DDR5 16GB (2x8GB) 5200", 90)["type"], "DDR5 UDIMM 16 ГБ (2×8) кіт")
        self.assertEqual(evaluate("Crucial 32GB Kit DDR4 3200 SODIMM 2x16GB", 70)["type"], "DDR4 SO-DIMM 32 ГБ (2×16) кіт")
        self.assertIn("не купуємо", evaluate("Crucial DDR5 96GB Kit 2x48GB 5600", 200)["reason"])

    def test_ddr5_single_16_desktop(self):
        r = evaluate("Kingston FURY Beast DDR5 16GB 5200MT/s CL40 DIMM", 85)
        self.assertEqual(r["type"], "DDR5 UDIMM 16 ГБ (одна планка)")
        self.assertTrue(r["verdict"].startswith("BUY"))
        self.assertTrue(r["single_module_warning"])

    def test_not_added_types_still_skip(self):
        for t, p in [("Samsung 8GB DDR5-5600 SO-DIMM", 20),
                     ("Crucial DDR5 96GB Kit 2x48GB 5600", 200), ("Micron 16gb DDR5 sodimm 5600 2x 8Gb", 30)]:
            self.assertEqual(evaluate(t, p)["verdict"], "SKIP", t)


class TestPrices(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(evaluate("Samsung 64 GB SO-DIMM DDR4 2666 MHz Arbeitsspeicher (2 x 32GB)", 105)["verdict"], "BUY-GOOD")
        self.assertEqual(evaluate("Corsair Vengeance 64GB DDR5-6000 CL30 (2x32GB)", 285)["verdict"], "BUY-GOOD")
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


class TestModelFromDesc(unittest.TestCase):
    def test_model_only_when_unambiguous(self):
        from ram_alert import model_from_desc
        self.assertEqual(model_from_desc("Xbox zu verkaufen", "Meine Xbox Series X 1TB mit Controller"), "Xbox Series X")
        self.assertEqual(model_from_desc("Switch Nintendo", "Nintendo Switch 2 kaum benutzt"), "Nintendo Switch 2")
        self.assertIsNone(model_from_desc("Xbox zu verkaufen", "Xbox One S mit 2 Controllern"))
        self.assertIsNone(model_from_desc("Nintendo Spielkonsole", "Switch 2 oder Switch Lite"))
        self.assertIsNone(model_from_desc("DDR5 32GB", "Series X"))


if __name__ == "__main__":
    unittest.main()


class TestPickup(unittest.TestCase):
    """Самовивіз у Гамбурзі (27.09): без пересилки й збору, готівкою; текст продавцю — «заберу й заплачу бар»."""

    def test_pickup_cheaper_than_shipping(self):
        from ram_alert import PICKUP_COST, apply_pickup, buyer_message
        ship = evaluate("Kingston Fury Beast DDR5 32GB (2x16GB) 6000", 230)
        pick = apply_pickup(evaluate("Kingston Fury Beast DDR5 32GB (2x16GB) 6000", 230))
        self.assertAlmostEqual(pick["buy_cost"], 230 + PICKUP_COST)
        self.assertLess(pick["buy_cost"], ship["buy_cost"])
        msg = buyer_message(pick)
        self.assertIn("bar", msg)
        self.assertNotIn("Sicher bezahlen", msg)
        self.assertLessEqual(len(msg), 256)

    def test_pickup_can_turn_skip_into_negotiate(self):
        from ram_alert import apply_pickup
        from console_alert import evaluate_console
        self.assertEqual(evaluate_console("Xbox Series X 1TB Konsole", 415, vb=True)["verdict"], "SKIP")
        self.assertIn(apply_pickup(evaluate_console("Xbox Series X 1TB Konsole", 415, vb=True))["verdict"],
                      ("BUY", "NEGOTIATE"))

    def test_hamburg_caps_match_model(self):
        import ram_mail_check as m
        from ram_alert import NEGOTIATE_UP, PICKUP_COST, REAL, costs
        import console_alert as ca

        def mx(p25, c):
            return int((p25 - c) / 1.3 * NEGOTIATE_UP - PICKUP_COST)
        self.assertEqual(m.HAMBURG_MAX["xbox"], mx(ca.XBOX_SERIES_X["p25"], ca.costs(ca.XBOX_SERIES_X["p25"])))
        self.assertEqual(m.HAMBURG_MAX["ddr5"], max(mx(v["p25"], costs(v["p25"])) for k, v in REAL.items() if k[0] == "ddr5"))
        self.assertTrue(m.is_pickup_search("Neue Anzeigen für „Konsolen - xbox series x in Hamburg“"))
        self.assertIn("l9409r30", m.public_search_url("Neue Anzeigen für „Konsolen - ps5 in Hamburg“"))


class TestAudit28Ram(unittest.TestCase):
    """Прогін 626 реальних оголошень KA (28.09): назви, які бот розумів неправильно."""

    def test_parse(self):
        from ram_parse import parse_title
        cases = {"2x Corsair Vengeance DDR5 16GB 5600MHz SODIMM RAM insg. 32GB neu": (32, 2),
                 "3x  32GB DDR5 SODIMM": (96, 3), "Crucial 64GB DDR5 RAM (1x64GB) 5600MHz SODIMM": (64, 1),
                 "NEU Corsair Vengeance DDR5 48GB (2x24GB) 5600MHz nicht 32GB 64GB": (48, 2),
                 "G.Skill 64-GB-Kit DDR 5 RAM": (64, 1), "32 GB DDR4 RAM 3200 MHz – 2x16 GB – Gaming PC": (32, 2),
                 "Corsair Vengeance 64GB Verpackung geöffnet DDR4": (64, 1),
                 "Samsung 16GB (2 Stk) DDR5 SODIMM 5600": (16, 2)}
        for t, (tot, mods) in cases.items():
            p, why = parse_title(t)
            self.assertIsNotNone(p, f"{t}: {why}")
            self.assertEqual((p["total"], p["modules"]), (tot, mods), t)
        for t in ["Gaming PC 7800x3d 64GB DDR5-6000 RTX4080 Super", "10 Stück DDR4 8GB",
                  "PC-Hardware-Bundle – Ryzen 7 3700X + 32 GB DDR4 + B550 Mainboard"]:
            self.assertIsNone(parse_title(t)[0], t)

    def test_evaluate(self):
        self.assertEqual(evaluate("2x 16GB DDR4 RAM (G.Skill Aegis & Crucial Ballistix)", 65)["verdict"], "SKIP")
        self.assertEqual(evaluate("RAM 1x32Gb 2x16Gb DDR 4", 1)["verdict"], "SKIP")        # ціна-заглушка
        r = evaluate("Kingston FURY Beast DDR5 32GB 5600 MHz CL40", 200)                  # кіт 2x16 без «2x16»
        self.assertTrue(r["verdict"].startswith("BUY") and r["kit_unknown"])
        r = evaluate("Kingston FURY Beast DDR5 16 GB Kit – 5200 MHz – CL40", 80)
        self.assertEqual(r["type"], evaluate("Kingston FURY Beast DDR5 2x8GB", 80)["type"])
        self.assertIn("48", evaluate("48GB DDR5-6800 CL34 RGB – SK Hynix M-Die nicht 32GB 64GB", 250)["type"])
        r = evaluate("Crucial 64GB DDR5 RAM (1x64GB) 5600MHz SODIMM", 300)
        self.assertEqual(r["verdict"], "SKIP")                                              # 1x64 — не кіт 2x32


class TestPass3Parse(unittest.TestCase):
    def test_titles(self):
        from ram_parse import parse_title
        cases = {"16GB Corsair DDR4-3200 RGB Kit": (16, 1), "16GB G.Skill Aegis - 2mal 8 GB DDR4-3200": (16, 2),
                 "Corsair Vengeance RGB Pro CMW32GX4M2Z3600C18  DDR4 3600 MHz": (32, 2),
                 "Kingston FURY KF432C16BBK2/32 DDR4": (32, 2), "G.Skill F4-3200C16D-32GVK Ripjaws": (32, 2),
                 "Crucial CT2K16G4DFD832A DDR4": (32, 2), "16GB (2*8GB) SO-DIMM PC4-3200 SK Hynix": (16, 2),
                 "Crucial 8‑GB‑DDR4‑2133 Desktop‑RAM‑Modul": (8, 1), "Samsung RAM 16GB 2x8 5600MHz Laptop DDR5": (16, 2)}
        for t, exp in cases.items():
            p, why = parse_title(t)
            self.assertIsNotNone(p, f"{t}: {why}")
            self.assertEqual((p["total"], p["modules"]), exp, t)
        self.assertIsNone(parse_title("Corsair Light Enhancement Kit DDR4")[0])
        self.assertEqual(parse_title("Samsung M425R2GA3BB0-CQK 16GB DDR5")[0]["form"], "sodimm")
