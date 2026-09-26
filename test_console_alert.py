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
        self.assertEqual(verdict("Xbox Series X 1TB + 2 Spiele + Controller + OVP - Top Zustand", 270), "BUY")
        self.assertEqual(verdict("Xbox Series X Konsole (1TB) mit Rechnung und drei Controller!", 279), "BUY")
        self.assertEqual(verdict("Xbox Series X mit Originell Kontroller", 336), "NEGOTIATE")
        self.assertEqual(verdict("Microsoft Xbox Series X 1TB Black 4K Wi-Fi inkl. Controller", 370), "SKIP")
        self.assertEqual(evaluate_console("Microsoft Xbox Series X 1TB Black", 370, vb=True)["verdict"], "NEGOTIATE")

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
    def test_user_example_335_offer_300(self):
        # приклад користувача 26.09: консоль за 335 € («можна») — пише продавцю, що візьме за 300
        r = evaluate_console("Xbox Series X Console", 335)
        self.assertEqual(r["verdict"], "NEGOTIATE")   # разом з пересилкою і Sicher bezahlen ≈ 362 € > стелі 357
        self.assertEqual(offer_price(r), 300)          # 300 € + пересилка + збір ≈ 325 € — у межах
        t = offer_template(r)
        self.assertIn("300 €", t)
        self.assertTrue(t.startswith("Hallo! Ich nehme die Xbox Series X für 300 €"))   # хук: одразу рішення і сума
        self.assertIn("reservieren", t)
        self.assertIn("Versand und Gebühr übernehme ich", t)
        self.assertNotIn("noch da", t)
        self.assertIn("Xbox Series X", t)
        self.assertLessEqual(len(t), 256)
        self.assertIn("Запропонуй <b>300 €</b>", format_html(r))

    def test_negotiate_offer_not_above_cap(self):
        r = evaluate_console("Xbox Series X 1TB", 380, vb=True)
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


if __name__ == "__main__":
    unittest.main()
