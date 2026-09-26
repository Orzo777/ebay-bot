"""Тести фільтра шахраїв research/ka_listing_check.py на фрагментах у форматі реальної сторінки
оголошення Kleinanzeigen (26.09.2026). Без мережі."""
import sys
import unittest
from datetime import date

sys.path.insert(0, ".")
sys.path.insert(0, "research")
from ka_listing_check import parse_listing, risk_lines

TODAY = date(2026, 9, 26)


def page(desc, since="16.07.2019", seller="Privater Nutzer"):
    return (f'<section id="viewad-contact"><span>B Bernert</span> {seller} <span>Aktiv seit {since}</span></section>'
            f'<h2 id="viewad-price">350 €</h2><p id="viewad-description-text" class="x">{desc}</p>')


HONEST = ("Verkaufe meine Xbox Series X, 1 TB, mit OVP und Controller. Läuft einwandfrei. "
          "Bezahlung bar bei Abholung oder per Sicher bezahlen mit Versand.")


class TestRisk(unittest.TestCase):
    def test_old_account_honest_text_is_low(self):
        r = parse_listing(page(HONEST), 290, 509, TODAY)
        self.assertEqual(r["level"], "low")
        self.assertIn("на KA з 2019", r["seller"])

    def test_fresh_account_is_high_with_redirect(self):
        r = parse_listing(page("Xbox Series X neu. Schreib mir auf WhatsApp 0176 1234567", since="20.09.2026"),
                          250, 509, TODAY)
        self.assertEqual(r["level"], "high")
        self.assertTrue(any("WhatsApp" in x or "поза Kleinanzeigen" in x for x in r["reasons"]))

    def test_paypal_friends_and_abroad_story(self):
        r = parse_listing(page("Bin auf Montage im Ausland, nur Versand. Zahlung per PayPal Freunde und Familie."),
                          300, 509, TODAY)
        self.assertEqual(r["level"], "high")

    def test_too_cheap_and_empty_text_is_medium(self):
        r = parse_listing(page("Xbox"), 190, 509, TODAY)
        self.assertEqual(r["level"], "high" if r["score"] >= 4 else "medium")
        self.assertTrue(any("40%" in x for x in r["reasons"]))

    def test_young_account_alone_is_low(self):
        r = parse_listing(page(HONEST, since="01.08.2026"), 300, 509, TODAY)
        self.assertEqual(r["level"], "low")
        self.assertTrue(any("молодий" in x for x in r["reasons"]))

    def test_refuses_buyer_protection(self):
        # реальний опис 26.09: «über PayPal als „Freunde & Familie" … die Bezahlfunktion von Kleinanzeigen kommen für mich nicht infrage»
        r = parse_listing(page('Versand nur über PayPal als „Freunde &amp; Familie". Andere Zahlungswege wie „PayPal '
                               'Käuferschutz" oder die Bezahlfunktion von Kleinanzeigen kommen für mich nicht infrage.'),
                          350, 509, TODAY)
        self.assertEqual(r["level"], "medium")
        self.assertTrue(any("Sicher bezahlen" in x for x in r["reasons"]))

    def test_gone_listing(self):
        r = parse_listing("<html>Diese Anzeige ist nicht mehr verfügbar</html>", 300, 509, TODAY)
        self.assertEqual(r["level"], "gone")

    def test_unreadable_page_is_unknown_not_scam(self):
        r = parse_listing("<html><title>Kleinanzeigen</title>Suche</html>", 65, 147, TODAY)
        self.assertEqual(r["level"], "unknown")
        self.assertIn("невідомо", risk_lines(r)[0])

    def test_card_lines_escape_and_unknown(self):
        self.assertIn("не вдалося", risk_lines(None)[0])
        lines = risk_lines({"level": "medium", "score": 2, "reasons": ["<b>x</b>"], "seller": "приватний"})
        self.assertIn("середній", lines[0])
        self.assertIn("&lt;b&gt;", lines[1])


if __name__ == "__main__":
    unittest.main()
