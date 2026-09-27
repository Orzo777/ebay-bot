"""Регресійні тести для research/console_alert.py на РЕАЛЬНИХ назвах приватних оголошень
Kleinanzeigen (Konsolen, 24.09.2026). Без мережі."""
import os
import sys
import unittest

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
from console_alert import SUSPICIOUS_BELOW, evaluate_console
from ram_alert import buy_cost, desc_facts, evaluate, format_html, offer_price, offer_template, seller_template


def verdict(title, price):
    r = evaluate_console(title, price)
    return r["verdict"] if r else None


class TestConsoleListings(unittest.TestCase):
    def test_real_cheap_consoles_are_buy(self):
        # повна вартість = ціна + пересилка 11 € + Sicher bezahlen (0,50 € + 4,5%)
        self.assertEqual(verdict("Xbox Series X 1 TB, 2 Controller, 15 Spiele", 250), "BUY-GOOD")
        self.assertEqual(verdict("Xbox Series X 1TB + 2 Spiele + Controller + OVP - Top Zustand", 300), "BUY")
        self.assertEqual(verdict("Xbox Series X Konsole (1TB) mit Rechnung und drei Controller!", 340), "BUY")
        self.assertEqual(verdict("Xbox Series X mit Originell Kontroller", 360), "NEGOTIATE")
        self.assertEqual(verdict("Microsoft Xbox Series X 1TB Black 4K Wi-Fi inkl. Controller", 390), "SKIP")
        self.assertEqual(evaluate_console("Microsoft Xbox Series X 1TB Black", 395, vb=True)["verdict"], "NEGOTIATE")

    def test_market_price_is_skip(self):
        self.assertEqual(verdict("Xbox Series X Konsole mit Controller und OVP", 500), "SKIP")
        self.assertEqual(verdict("Xbox Series X", 550), "SKIP")

    def test_accessories_are_skip(self):
        self.assertEqual(verdict("Xbox Series X Controller Robot White in Ovp", 55), "SKIP")
        self.assertEqual(verdict("Xbox Series X/S Speichererweiterung SSD 1TB Top!Kein Versand!!!", 150), "SKIP")
        self.assertEqual(verdict("Hori Racing Wheel Overdrive für Xbox Series X/S mit Halterung", 200), "SKIP")
        self.assertEqual(verdict("Xbox Series X Battle Beaver Controller", 165), "SKIP")

    def test_combos_swaps_and_other_consoles_skip(self):
        self.assertEqual(verdict("PS5 Digital + Xbox Series X 1TB + Elite Controller 2", 300), "SKIP")
        self.assertEqual(verdict("Tausche Playstation 5 + Edge Controller & Co gegen Xbox Series X", 200), "SKIP")
        self.assertEqual(verdict("Xbox Series X defekt für Bastler", 150), "SKIP")
        self.assertEqual(verdict("Microsoft Xbox Series X 1TB Digital Edition Robot White+Zubehör", 300), "SKIP")

    def test_too_cheap_is_skip(self):
        self.assertEqual(verdict("Xbox Series X", 120), "SKIP")

    def test_not_console_falls_through_to_ram(self):
        self.assertIsNone(evaluate_console("SK Hynix 16GB DDR5 SODIMM 5600MHz", 50))
        self.assertIsNone(evaluate_console("Xbox Series S 512GB", 150))

    def test_suspicious_warning_only_when_very_cheap(self):
        cheap = evaluate_console("Xbox Series X Konsole", SUSPICIOUS_BELOW - 30)
        normal = evaluate_console("Xbox Series X Konsole", SUSPICIOUS_BELOW + 50)
        self.assertTrue(any("Підозріло" in n for n in cheap["notes"]))
        self.assertFalse(any("Підозріло" in n for n in normal["notes"]))


class TestNegotiate(unittest.TestCase):
    def test_negotiate_offer_whole_sum(self):
        # 26.09 (вечір, ціни Terapeak p25 545): 360 € — трохи понад стелю 348 → торг, пропозиція 320
        r = evaluate_console("Xbox Series X Console", 360)
        self.assertEqual(r["verdict"], "NEGOTIATE")
        self.assertEqual(offer_price(r), 320)
        t = offer_template(r)
        self.assertIn("320 €", t)
        self.assertTrue(t.startswith("Hallo! Ich nehme die Xbox Series X für 320 €"))   # хук: одразу рішення і сума
        self.assertIn("reservieren", t)
        self.assertIn("Versand und Gebühr übernehme ich", t)
        self.assertNotIn("noch da", t)
        self.assertIn("Xbox Series X", t)
        self.assertLessEqual(len(t), 256)
        self.assertIn("Запропонуй <b>320 €</b>", format_html(r))

    def test_negotiate_offer_not_above_cap(self):
        r = evaluate_console("Xbox Series X 1TB", 370, vb=True)
        self.assertEqual(r["verdict"], "NEGOTIATE")
        self.assertLessEqual(buy_cost(offer_price(r), r["ship_in"]), r["cap"])   # разом — не вище стелі
        self.assertEqual(offer_price(r) % 5, 0)
        self.assertIn("ТОРГУЙСЯ", format_html(r))

    def test_good_and_excellent_have_no_offer(self):
        self.assertIsNone(offer_price(evaluate_console("Xbox Series X 1TB", 260)))
        self.assertIsNone(offer_template(evaluate_console("Xbox Series X 1TB", 220)))

    def test_tiny_discount_not_offered(self):
        # 270 € → ціль дала б 265 € (-2%): торгуватись за 5 € не варто, купуй як є
        self.assertIsNone(offer_price(evaluate_console("Xbox Series X 1TB", 270)))

    def test_far_above_cap_skip(self):
        self.assertEqual(verdict("Xbox Series X 1TB", 450), "SKIP")


class TestCard(unittest.TestCase):
    def test_card_uses_console_text_and_notes(self):
        r = evaluate_console("Xbox Series X 1TB mit Controller", 290)
        html = format_html(r)
        self.assertIn("Xbox Series X", html)
        self.assertIn("Sicher bezahlen", html)
        t = seller_template(r)
        self.assertNotIn("RAM", t)
        self.assertLessEqual(len(t), 256)
        # порядок: беру → умови → питання
        self.assertLess(t.index("reservieren"), t.index("Sicher bezahlen"))
        self.assertLess(t.index("Sicher bezahlen"), t.index("Laufwerk"))

    def test_description_answers_skip_questions(self):
        r = evaluate_console("Xbox Series X 1TB mit Controller", 290)
        r["desc"] = "Konsole läuft einwandfrei, Rechnung von MediaMarkt liegt bei."
        t = seller_template(r)
        self.assertNotIn("Laufwerk", t)
        self.assertNotIn("Rechnung", t)
        self.assertIn("справне", format_html(r))
        self.assertIn("є чек", format_html(r))

    def test_description_facts(self):
        self.assertEqual(desc_facts("Nicht getestet, keine Rechnung.")["works"], False)
        self.assertEqual(desc_facts("Nicht getestet, keine Rechnung.")["receipt"], False)
        self.assertEqual(desc_facts("Laufwerk defekt")["works"], False)
        self.assertIsNone(desc_facts("Privatverkauf ohne Gewähr.")["works"])   # юридична формула, не «не тестовано»
        self.assertIsNone(desc_facts(None)["receipt"])

    def test_ram_card_unchanged(self):
        r = evaluate("Crucial 2x32GB DDR4 2666 RAM Kit 64GB", 65)
        self.assertIn("RAM", seller_template(r))


class TestPS5(unittest.TestCase):
    # 26.09: PS5 Slim Disc — слабкий кандидат з міні-дослідження (KA 2 з 18 у зоні торгу)
    def test_disc_and_digital_types(self):
        self.assertEqual(evaluate_console("PS5 Slim Disc Edition 1TB", 300)["type"], "PS5 з дисководом (вживана)")
        self.assertEqual(evaluate_console("Playstation 5 Konsole mit 2 Controllern", 300)["type"], "PS5 з дисководом (вживана)")
        self.assertEqual(evaluate_console("PS5 Digital Edition Slim", 250)["type"], "PS5 Digital (вживана)")

    def test_rejects(self):
        for t, p in [("PS5 Pro 2TB", 700), ("PS5 Controller DualSense", 40), ("PS5 Konsole defekt", 100),
                     ("PS5 Spiele Paket", 60), ("Suche PS5 Slim", 300)]:
            self.assertEqual(evaluate_console(t, p)["verdict"], "SKIP", t)

    def test_negotiate_zone_and_offer(self):
        r = evaluate_console("PS5 Slim Disc Edition 1TB", 320, vb=True)
        self.assertEqual(r["verdict"], "NEGOTIATE")
        self.assertIn("die PS5", offer_template(r))
        self.assertIn("PSN", seller_template(r))

    def test_not_ps5_goes_to_ram(self):
        self.assertIsNone(evaluate_console("Crucial 32GB DDR5 2x16GB", 100))


class TestSwitch2(unittest.TestCase):
    # 26.09: KA €220–270 при швидкому продажу на eBay €382 (вживані); ігри й аксесуари з «Switch 2» у назві — не консоль
    def test_console_vs_games(self):
        self.assertEqual(evaluate_console("Nintendo Switch 2 + Mario Kart World", 220)["verdict"], "BUY")
        self.assertEqual(evaluate_console("Pokémon Legenden: Z-A - Nintendo Switch 2 Edition", 35)["verdict"], "SKIP")
        self.assertEqual(evaluate_console("Nintendo Switch 2 Pro Controller", 60)["verdict"], "SKIP")
        self.assertIsNone(evaluate_console("FeinTech SW212 HDMI 2.1 Switch 2x1 + Audio Extractor", 25))
        self.assertEqual(evaluate_console("Nintendo Switch OLED", 200)["verdict"], "SKIP")   # з 27.09 оцінюється, але дорого

    def test_sealed_cheap_warns(self):
        r = evaluate_console("Nintendo Switch 2 – neu & originalverpackt", 230)
        self.assertIn("шахрай", r["notes"][0])
        self.assertIn("die Switch 2", seller_template(r))


class TestFixes27(unittest.TestCase):
    def test_controller_limited_edition_is_not_ps5(self):
        # 27.09: «PS5 DualSense Controller LeBron James Limited Edition Neu OVP» 200 € прийшов у підписку PS5
        r = evaluate_console("PS5 DualSense Controller LeBron James Limited Edition Neu OVP", 200)
        self.assertEqual(r["verdict"], "SKIP")

    def test_switch_titles_from_share(self):
        # 27.09 «Поділитися → бот»: «Switch 2 zu verkaufen» і «Switch v2 Zelda Fanpaket» не розпізнавались
        self.assertEqual(evaluate_console("Switch 2 zu verkaufen", 180)["type"], "Nintendo Switch 2 (вживана)")
        self.assertEqual(evaluate_console("Nintendo Switch v2 Zelda Fanpaket", 150)["type"], "Nintendo Switch V1/V2 (вживана)")
        self.assertEqual(evaluate_console("Nintendo Switch OLED weiß", 150)["type"], "Nintendo Switch OLED (вживана)")
        self.assertEqual(evaluate_console("Nintendo Switch Lite türkis", 60)["type"], "Nintendo Switch Lite (вживана)")
        self.assertEqual(evaluate_console("Nintendo Switch Spiele Paket", 60)["verdict"], "SKIP")
        self.assertIsNone(evaluate_console("TP-Link Switch 8 Port Gigabit", 25))
        self.assertIsNone(evaluate_console("FeinTech HDMI Switch 2x1", 25))


if __name__ == "__main__":
    unittest.main()
