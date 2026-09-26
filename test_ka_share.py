"""Тести «Поділитися → бот» (research/ka_share.py) на фрагментах у форматі реальної сторінки
оголошення Kleinanzeigen (26.09.2026). Без мережі."""
import os
import sys
import unittest

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
from ka_share import evaluate_listing, listing_url, parse_page


def page(title, price="350 €", shipping='<span class="boxedarticle--details--shipping"> + Versand ab 2,99 €</span>',
         seller="Privater Nutzer", since="16.07.2019",
         desc="Verkaufe meine Konsole, läuft einwandfrei, Bezahlung per Sicher bezahlen möglich."):
    return (f'<h1 id="viewad-title" class="boxedarticle--title" itemprop="name"> {title}</h1>'
            f'<h2 class="boxedarticle--price" id="viewad-price"> {price}</h2> {shipping}'
            f'<section id="viewad-contact">{seller} Aktiv seit {since}</section>'
            f'<p id="viewad-description-text" class="x">{desc}</p>')


class TestUrl(unittest.TestCase):
    def test_share_text_and_long_link(self):
        t = "Schau dir das an: Xbox https://www.kleinanzeigen.de/s-anzeige/xbox-series-x-1tb-ovp/3523539635-279-3950"
        self.assertEqual(listing_url(t), "https://www.kleinanzeigen.de/s-anzeige/3523539635")
        self.assertEqual(listing_url("https://www.kleinanzeigen.de/s-anzeige/3523539635"),
                         "https://www.kleinanzeigen.de/s-anzeige/3523539635")
        self.assertIsNone(listing_url("hallo"))


class TestParse(unittest.TestCase):
    def test_fields(self):
        p = parse_page(page("Xbox Series X 1TB + OVP", "300 € VB"))
        self.assertEqual((p["title"], p["price"], p["vb"], p["ship_from"]), ("Xbox Series X 1TB + OVP", 300.0, True, 2.99))
        self.assertFalse(p["pickup_only"])

    def test_pickup_and_no_price(self):
        p = parse_page(page("Xbox Series X", "VB", shipping="Nur Abholung"))
        self.assertIsNone(p["price"])
        self.assertTrue(p["pickup_only"])

    def test_not_a_listing(self):
        self.assertIsNone(parse_page("<html>Suche</html>"))


class TestEvaluate(unittest.TestCase):
    def test_negotiate_card_with_risk(self):
        msg, res = evaluate_listing(page("Xbox Series X 1TB + OVP", "340 €"), "u")
        self.assertEqual(res["verdict"], "NEGOTIATE")
        self.assertIn("ТОРГУЙСЯ", msg)
        self.assertIn("Ризик шахрайства", msg)
        self.assertIn("Запропонуй", msg)

    def test_console_shipping_not_below_estimate(self):
        _, res = evaluate_listing(page("Xbox Series X 1TB", "250 €"), "u")
        self.assertEqual(res["ship_in"], 11.0)   # «Versand ab 2,99» — не вір, консоль так не пересилають

    def test_skip_explains_ceiling(self):
        msg, res = evaluate_listing(page("Xbox Series X 1TB", "520 €"), "u")
        self.assertEqual(res["verdict"], "SKIP")
        self.assertIn("Не бери", msg)
        self.assertIn("вигідно лише до", msg)

    def test_ram_listing_and_pickup_note(self):
        msg, res = evaluate_listing(page("Crucial 16GB DDR5-4800 UDIMM Arbeitsspeicher RAM", "65 €",
                                         shipping="Nur Abholung"), "u")
        self.assertIn(res["verdict"], ("BUY-EXCELLENT", "BUY-GOOD"))
        self.assertIn("самовивіз", msg)

    def test_scam_listing_is_rejected_not_carded(self):
        msg, res = evaluate_listing(page("Xbox Series X 1TB", "250 €",
                                         desc="Top Zustand, mit OVP. Bezahlung nur per PayPal Freunde und Familie."), "u")
        self.assertIn("схоже на шахрая", msg)
        self.assertNotIn("Запропонуй", msg)

    def test_no_price(self):
        msg, res = evaluate_listing(page("Xbox Series X", "VB"), "u")
        self.assertIsNone(res)
        self.assertIn("Ціни немає", msg)


if __name__ == "__main__":
    unittest.main()
