"""Відповіді на наше «Suche Arbeitsspeicher» (01.11): оцінка пропозиції і готова відповідь німецькою. Без мережі."""
import os
import sys
import unittest

os.environ.setdefault("RAM_PRICES_OFF", "1")
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import ka_wanted  # noqa: E402


def run(msg, item="", price=None):
    return ka_wanted.assess({"msg": msg, "item": item, "price": price, "uk": "переклад", "who": "Max"})


class AssessTest(unittest.TestCase):
    def test_cheap_kit_buy(self):
        a = run("Hallo, ich hätte 2x16GB DDR5 6000 Corsair Vengeance für 150€", "Corsair Vengeance DDR5 2x16GB 6000", 150)
        self.assertEqual(a["verdict"], "buy")
        self.assertIn("150 € passt", a["reply"])
        self.assertIn("Aufkleber", a["reply"])   # перед покупкою — фото наклейки
        self.assertNotIn("ціна однієї планки", a["text"])   # ціна не змінилась — без плутаних пояснень

    def test_no_price_shows_cap(self):
        a = run("Ich habe 2x16 GB DDR4 3200 Kingston Fury, was zahlst du?", "Kingston Fury DDR4 2x16GB 3200")
        self.assertEqual(a["verdict"], "ask")
        self.assertRegex(a["text"], r"вигідно до <b>\d+ €</b>")
        self.assertIn("preislich", a["reply"])

    def test_expensive_names_max(self):
        a = run("Corsair DDR5 2x16 6000 für 400€", "Corsair DDR5 2x16GB 6000", 400)
        self.assertEqual(a["verdict"], "expensive")
        cap = ka_wanted._cap_item(a["res"])
        self.assertIn(f"mehr als {cap} €", a["reply"])

    def test_per_stick_price_doubles(self):
        a = run("Biete 2x32GB DDR5 5600 Kingston, Preis pro Riegel 120€", "Kingston DDR5 2x32GB 5600", 120)
        self.assertIn("240 €", a["reply"])
        self.assertIn("ціна за одну планку", a["text"])

    def test_ddr3_declined(self):
        a = run("Hab noch DDR3 8GB rumliegen, 10€", "DDR3 8GB", 10)
        self.assertEqual(a["verdict"], "wrong")
        self.assertIn("suche ich leider nicht", a["reply"])

    def test_unknown_asks_details(self):
        a = run("Hallo, ist die Anzeige noch aktuell?")
        self.assertEqual(a["verdict"], "unknown")
        self.assertIn("DDR4 oder DDR5", a["reply"])

    def test_copy_button_fits_telegram(self):
        for a in (run("Corsair DDR5 2x16 6000 für 150€", "Corsair DDR5 2x16GB 6000", 150), run("Hallo?")):
            self.assertLessEqual(len(a["reply"]), ka_wanted.COPY_MAX)
            kb = ka_wanted.keyboard(a, None)
            self.assertEqual(kb["inline_keyboard"][1][0]["copy_text"]["text"], a["reply"])
            self.assertEqual(kb["inline_keyboard"][0][0]["url"], ka_wanted.CHATS)


if __name__ == "__main__":
    unittest.main()
