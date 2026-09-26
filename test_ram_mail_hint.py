"""Тести тихої підказки «глянь підписку» в research/ram_mail_check.py. Kleinanzeigen кладе в лист
лише ОДНЕ оголошення з пачки (діагностика 26.09.2026: у 25 листах по одному id, хоча в дзвіночку
«2/4/5 neue Ergebnisse»). Без мережі: Telegram підмінено."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import ram_mail_check as rmc

SUBJECT = "Neue Treffer zu deiner Suche „Konsolen - xbox series x in Ganz Deutschland“"


def ka_mail(title, price, search_id="767883050", ad_id="3523290701", age_min=1):
    body = (f'<a href="https://www.kleinanzeigen.de/m-suche-verwenden.html?id={search_id}&utm_source=system_email">'
            f'Neue Treffer ansehen</a><img alt="Bild zur Anzeige {title}"> {price} € Von Privat '
            f'<a href="https://www.kleinanzeigen.de/s-anzeige/{ad_id}?utm=x" title="Anzeige ansehen">Anzeige ansehen</a>')
    msg = EmailMessage()
    msg["Subject"] = SUBJECT
    msg["Date"] = format_datetime(datetime.now(timezone.utc) - timedelta(minutes=age_min))
    msg.set_content(body, subtype="html")
    return msg


class TestParsing(unittest.TestCase):
    def test_search_link_and_name(self):
        sid, link = rmc.search_link('x href="https://www.kleinanzeigen.de/m-suche-verwenden.html?id=767883050&utm=1"')
        self.assertEqual(sid, "767883050")
        self.assertEqual(link, "https://www.kleinanzeigen.de/m-suche-verwenden.html?id=767883050")
        self.assertEqual(rmc.search_name(SUBJECT), "xbox series x")

    def test_title_html_entities_unescaped(self):
        body = '<img alt="Bild zur Anzeige Xbox Series X Konsole mit 2 Controllern &amp; Speichererweiterung"> 300 €'
        self.assertEqual(rmc.extract_listings("", body)[0]["title"],
                         "Xbox Series X Konsole mit 2 Controllern & Speichererweiterung")

    def test_vb_flag_parsed(self):
        body = ('<img alt="Bild zur Anzeige Xbox Series X"> 340 € VB Von Privat '
                '<img alt="Bild zur Anzeige Xbox Series X 1TB"> 350 € Von Privat')
        a, b = rmc.extract_listings("", body)
        self.assertTrue(a["vb"])
        self.assertFalse(b["vb"])

    def test_card_keyboard_has_search_button(self):
        kb = rmc.build_keyboard("https://www.kleinanzeigen.de/s-anzeige/1", "Hallo", "https://s")
        self.assertEqual(kb["inline_keyboard"][-1][0]["url"], "https://s")


class TestThrottle(unittest.TestCase):
    def test_one_hint_per_search_per_gap(self):
        now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
        times = {}
        self.assertTrue(rmc.should_hint("1", now, times))
        times["1"] = now.isoformat()
        self.assertFalse(rmc.should_hint("1", now + timedelta(minutes=10), times))
        self.assertTrue(rmc.should_hint("2", now + timedelta(minutes=10), times))
        self.assertTrue(rmc.should_hint("1", now + timedelta(minutes=rmc.HINT_GAP_MIN), times))


class TestProcess(unittest.TestCase):
    def setUp(self):
        self.hints, self.cards = [], []
        self._h, self._c, self._k = rmc.send_telegram_hint, rmc.send_telegram_card, rmc.check_listing
        rmc.send_telegram_hint = lambda text, link: self.hints.append((text, link))
        rmc.send_telegram_card = lambda *a: self.cards.append(a)
        rmc.check_listing = lambda *a: {"level": "low", "score": 0, "reasons": [], "seller": "приватний, на KA з 2019"}

    def tearDown(self):
        rmc.send_telegram_hint, rmc.send_telegram_card, rmc.check_listing = self._h, self._c, self._k

    def test_non_deal_listing_gives_silent_hint(self):
        # реальний випадок 26.09 10:07: у листі Series S €219, а Series X €290 з тієї ж пачки — прихований
        sent = rmc._process(ka_mail("X Box Series S", 219), 6, False, set(), {})
        self.assertEqual((sent, len(self.cards), len(self.hints)), (0, 0, 1))
        self.assertIn("xbox series x", self.hints[0][0])
        self.assertIn("не той товар", self.hints[0][0])
        self.assertTrue(self.hints[0][1].endswith("id=767883050"))

    def test_deal_gives_card_not_hint(self):
        sent = rmc._process(ka_mail("Xbox Series X 1TB + OVP", 250), 6, False, set(), {})
        self.assertEqual((sent, len(self.hints)), (1, 0))
        self.assertTrue(self.cards[0][3].endswith("id=767883050"))   # кнопка «інші нові збіги»

    def test_pc_search_ignored(self):
        m = ka_mail("PC + Monitor i7,16GB,500W 85+,GPU", 25)
        m.replace_header("Subject", "Neue Treffer zu deiner Suche „PCs in Hamburg (+20 km)“")
        self.assertEqual(rmc._process(m, 6, False, set(), {}), 0)
        self.assertEqual((len(self.cards), len(self.hints)), (0, 0))

    def test_right_type_but_pricier_gives_no_hint(self):
        # випадок 26.09: Corsair DDR5 16GB, дорожче стелі — у тій пачці більше нічого не було, підказка — шум
        sent = rmc._process(ka_mail("Corsair Vengeance 16GB DDR5 6000 RAM", 190), 6, False, set(), {})
        self.assertEqual((sent, len(self.hints)), (0, 0))

    def test_negotiate_card_has_offer_button(self):
        rmc._process(ka_mail("Xbox Series X 1TB", 340), 6, False, set(), {})
        self.assertEqual(len(self.cards), 1)
        offer_text = self.cards[0][5]
        self.assertIn("Versand und Käuferschutz zahle ich", offer_text)
        kb = rmc.build_keyboard("l", "s", "q", offer_text)
        self.assertTrue(any("пропозицією" in row[0]["text"] for row in kb["inline_keyboard"]))

    def test_hint_throttled_and_stale_mail_silent(self):
        times = {}
        rmc._process(ka_mail("X Box Series S", 219, ad_id="1"), 6, False, set(), times)
        rmc._process(ka_mail("X Box Series S", 230, ad_id="2"), 6, False, set(), times)
        self.assertEqual(len(self.hints), 1)
        rmc._process(ka_mail("X Box Series S", 219, search_id="9", age_min=600), 6, False, set(), {})
        self.assertEqual(len(self.hints), 1)


if __name__ == "__main__":
    unittest.main()
