"""Регресійні тести для research/console_alert.py на РЕАЛЬНИХ назвах приватних оголошень
Kleinanzeigen (Konsolen, 24.09.2026). Без мережі."""
import os
import sys
import unittest

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
from console_alert import SUSPICIOUS_BELOW, evaluate_console, seller_text
from ram_alert import evaluate, format_html, seller_template


def verdict(title, price):
    r = evaluate_console(title, price)
    return r["verdict"] if r else None


class TestConsoleListings(unittest.TestCase):
    def test_real_cheap_consoles_are_buy(self):
        self.assertEqual(verdict("Xbox Series X 1TB + 2 Spiele + Controller + OVP - Top Zustand", 270), "BUY-GOOD")
        self.assertEqual(verdict("Xbox Series X Konsole (1TB) mit Rechnung und drei Controller!", 279), "BUY-GOOD")
        self.assertEqual(verdict("Xbox Series X mit Originell Kontroller", 336), "BUY")
        self.assertEqual(verdict("Microsoft Xbox Series X 1TB Black 4K Wi-Fi inkl. Controller", 370), "SKIP")

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


class TestCard(unittest.TestCase):
    def test_card_uses_console_text_and_notes(self):
        r = evaluate_console("Xbox Series X 1TB mit Controller", 290)
        html = format_html(r)
        self.assertIn("Xbox Series X", html)
        self.assertIn("Sicher bezahlen", html)
        self.assertEqual(seller_template(r), seller_text())
        self.assertNotIn("RAM", seller_template(r))

    def test_seller_text_fits_copy_button(self):
        self.assertLessEqual(len(seller_text()), 256)

    def test_ram_card_unchanged(self):
        r = evaluate("Crucial 2x32GB DDR4 2666 RAM Kit 64GB", 65)
        self.assertIn("RAM", seller_template(r))


if __name__ == "__main__":
    unittest.main()
