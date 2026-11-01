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

    def test_label_photo_one_stick(self):
        # 01.11, перша справжня відповідь: «einem DDR4 Riegel 9 €» + фото наклейки CMK16GX4M2A2400C16 «16GB (2x8GB)»
        msg = "Guten Morgen, haben Sie Interesse an einem DDR4 Riegel ? 9€ Inklusive Versand"
        a = run(msg, "DDR4 Riegel", 9)
        self.assertEqual(a["verdict"], "unknown")
        facts = {"part_number": "CMK16GX4M2A2400C16", "label_kit": "16GB (2x8GB)", "gen": "DDR4", "form": "DIMM",
                 "speed_mhz": 2400, "brand": "Corsair", "modules_offered": 1, "module_gb": 8, "price_eur": 9}
        self.assertEqual(ka_wanted.label_item(facts), "Corsair DDR4 1x8GB 2400 MHz")   # номер набору не перетворює 1 планку на 2×8
        b = ka_wanted.assess({"msg": msg, "item": "DDR4 Riegel", "price": 9, "facts": facts})
        self.assertEqual(b["verdict"], "wrong")
        self.assertIn("8 ГБ (1 план.)", b["text"])
        self.assertIn("📷 З наклейки: CMK16GX4M2A2400C16", b["text"])

    def test_label_kit_no_second_photo_request(self):
        facts = {"part_number": "CMH32GX5M2B6000C30", "gen": "DDR5", "modules_offered": 2, "module_gb": 16, "speed_mhz": 6000,
                 "brand": "Corsair", "price_eur": 180}
        a = ka_wanted.assess({"msg": "Biete beide Riegel für 180€", "facts": facts})
        self.assertEqual(a["verdict"], "buy")
        self.assertNotIn("Aufkleber", a["reply"])

    def test_read_label_needs_usable_answer(self):
        self.assertIsNone(ka_wanted.read_label([b"x"], "m", ask=lambda imgs, prompt, budget: {"gen": "DDR4"}))
        good = {"gen": "DDR4", "modules_offered": 1, "module_gb": 8}
        self.assertEqual(ka_wanted.read_label([b"x"], "m", ask=lambda imgs, prompt, budget: good), good)
        self.assertIsNone(ka_wanted.read_label([], "m", ask=lambda *a, **k: good))

    def test_pic_hint(self):
        a = ka_wanted.assess({"msg": "haben Sie Interesse an einem DDR4 Riegel ? 9€", "price": 9, "pic": True})
        self.assertIn("У чаті є фото", a["text"])
        self.assertNotIn("Aufkleber", a["reply"])
        self.assertNotIn("preislich", a["reply"])   # ціну вже назвав


if __name__ == "__main__":
    unittest.main()
