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
        self._h, self._c, self._k, self._sh = rmc.send_telegram_hint, rmc.send_telegram_card, rmc.check_listing, rmc.SEND_HINTS
        rmc.SEND_HINTS = True   # логіку підказок тестуємо, хоча в роботі вона вимкнена
        rmc.send_telegram_hint = lambda text, link: self.hints.append((text, link))
        rmc.send_telegram_card = lambda *a: self.cards.append(a)
        rmc.check_listing = lambda *a: {"level": "low", "score": 0, "reasons": [], "seller": "приватний, на KA з 2019"}

    def tearDown(self):
        rmc.send_telegram_hint, rmc.send_telegram_card, rmc.check_listing, rmc.SEND_HINTS = self._h, self._c, self._k, self._sh

    def test_non_deal_listing_gives_silent_hint(self):
        # реальний випадок 26.09 10:07: у листі Series S €219, а Series X €290 з тієї ж пачки — прихований
        sent = rmc._process(ka_mail("X Box Series S", 219), 6, False, set(), {})
        self.assertEqual((sent, len(self.cards), len(self.hints)), (0, 0, 1))
        self.assertIn("xbox series x", self.hints[0][0])
        self.assertIn("не той товар", self.hints[0][0])
        self.assertIn("model_s:series_x", self.hints[0][1])   # публічний пошук, не m-suche-verwenden (500 без входу)

    def test_deal_gives_card_not_hint(self):
        sent = rmc._process(ka_mail("Xbox Series X 1TB + OVP", 250), 6, False, set(), {})
        self.assertEqual((sent, len(self.hints)), (1, 0))
        self.assertIn("/s-konsolen/xbox/", self.cards[0][3])   # кнопка «інші нові збіги» — публічна сторінка

    def test_scam_seller_gives_no_card(self):
        rmc.check_listing = lambda *a: {"level": "medium", "score": 3, "reasons": ["x"], "seller": "приватний",
                                        "block": True, "hard": ["хоче оплату переказом / PayPal Freunde"]}
        sent = rmc._process(ka_mail("Xbox Series X 1TB + OVP", 250), 6, False, set(), {})
        self.assertEqual((sent, len(self.cards)), (0, 0))

    def test_pc_scam_seller_skipped(self):
        pcs, old = [], (rmc.send_pc_card, rmc.PC_BOT_TOKEN)
        rmc.send_pc_card, rmc.PC_BOT_TOKEN = pcs.append, "x"
        rmc.check_listing = lambda *a: {"level": "medium", "score": 2, "reasons": [], "seller": "", "block": True,
                                        "hard": ["відмовляється від «Sicher bezahlen»"]}
        try:
            m = ka_mail("Terra PC / i5-6400 / 8GB Ram / 256GB SSD / Win11", 60)
            m.replace_header("Subject", "Neue Treffer zu deiner Suche „PCs in Hamburg (+20 km)“")
            self.assertEqual(rmc._process(m, 6, False, set(), {}), 0)
            self.assertEqual(pcs, [])
        finally:
            rmc.send_pc_card, rmc.PC_BOT_TOKEN = old

    def test_hints_off_in_production(self):
        rmc.SEND_HINTS = False
        rmc._process(ka_mail("X Box Series S", 219), 6, False, set(), {})
        self.assertEqual(self.hints, [])

    def test_public_search_urls(self):
        self.assertIn("preis:20:256/ddr5-32gb/k0c225",
                      rmc.public_search_url("Neue Treffer zu deiner Suche „PC-Zubehör & Software - ddr5 32gb in Ganz Deutschland“"))
        self.assertIn("model_s:switch_2", rmc.public_search_url("Neue Treffer zu deiner Suche „Konsolen - switch 2 in Ganz Deutschland“"))
        self.assertIsNone(rmc.public_search_url("Neue Treffer zu deiner Suche „щось нове“"))

    def test_pc_filter_real_titles(self):
        # 26.09: у ПК-бот прийшов «Raspberry Pi 3 Model B im transparenten Gehäuse» — не ПК
        skip = ["Raspberry Pi 3 Model B im transparenten Gehäuse", "Fujitsu Futro S720 Thin Client",
                "PC Gehäuse mit Netzteil", "Netzteil für PC 500W", "Monitor 24 Zoll Samsung",
                "Tastatur und Maus Set", "Mainboard mit i5 und 8GB RAM",
                # мініпк (26.09: «прибери міні пк»)
                "Terra PC / i5-6400 / 8GB Ram / 256GB SSD / Win11 / 25x22x10cm", "Lenovo ThinkCentre M720q i5 8GB",
                "Dell OptiPlex 3070 Micro i5", "HP EliteDesk 800 G3 Mini", "Intel NUC i5", "Fujitsu Esprimo Q556",
                "Mini PC Beelink N100", "HP ProDesk 400 G4 DM"]
        keep = ["PC + Monitor i7,16GB,500W 85+,GPU", "Gaming PC mit Netzteil 600W", "Dell Optiplex 7050 i5 8GB",
                "Computer Tower", "Alter PC", "HP Desktop PC Windows 10", "Gaming PC Gehäuse RGB mit Ryzen 5",
                "Fujitsu Esprimo P757 i5 Tower", "Dell OptiPlex 7050 SFF i5", "Gaming PC 45x20x42cm GTX 1060"]
        for t in skip:
            self.assertIsNotNone(rmc.pc_skip_reason(t), t)
        for t in keep:
            self.assertIsNone(rmc.pc_skip_reason(t), t)

    def test_pc_search_goes_to_separate_bot(self):
        pcs, old = [], (rmc.send_pc_card, rmc.PC_BOT_TOKEN)
        rmc.send_pc_card, rmc.PC_BOT_TOKEN = pcs.append, "x"
        try:
            m = ka_mail("PC + Monitor i7,16GB,500W 85+,GPU", 25)
            m.replace_header("Subject", "Neue Treffer zu deiner Suche „PCs in Hamburg (+20 km)“")
            seen = set()
            self.assertEqual(rmc._process(m, 6, False, seen, {}), 1)
            self.assertEqual(rmc._process(m, 6, False, seen, {}), 0)   # дубль не шлемо
        finally:
            rmc.send_pc_card, rmc.PC_BOT_TOKEN = old
        self.assertEqual(len(pcs), 1)
        self.assertIn("i7", rmc.pc_text(pcs[0]))
        self.assertEqual((len(self.cards), len(self.hints)), (0, 0))   # у головний бот — нічого

    def test_right_type_but_pricier_gives_no_hint(self):
        # випадок 26.09: Corsair DDR5 16GB, дорожче стелі — у тій пачці більше нічого не було, підказка — шум
        sent = rmc._process(ka_mail("Corsair Vengeance 16GB DDR5 6000 RAM", 190), 6, False, set(), {})
        self.assertEqual((sent, len(self.hints)), (0, 0))

    def test_negotiate_card_has_offer_button(self):
        rmc._process(ka_mail("Xbox Series X 1TB", 340), 6, False, set(), {})
        self.assertEqual(len(self.cards), 1)
        offer_text = self.cards[0][5]
        self.assertIn("Versand und Gebühr übernehme ich", offer_text)
        kb = rmc.build_keyboard("l", "s", "q", offer_text)
        self.assertTrue(any("пропозицією" in row[0]["text"] for row in kb["inline_keyboard"]))

    def test_hint_throttled_and_stale_mail_silent(self):
        times = {}
        rmc._process(ka_mail("X Box Series S", 219, ad_id="1"), 6, False, set(), times)
        rmc._process(ka_mail("X Box Series S", 230, ad_id="2"), 6, False, set(), times)
        self.assertEqual(len(self.hints), 1)
        rmc._process(ka_mail("X Box Series S", 219, search_id="9", age_min=600), 6, False, set(), {})
        self.assertEqual(len(self.hints), 1)

    def test_late_mail_only_good_deals_silent(self):
        # 01.10: Apps Script не спрацював, запасний cron приніс листи 2-годинної давнини пачкою
        rmc._process(ka_mail("Xbox Series X 1TB", 340, ad_id="11", age_min=120), 6, False, set(), {})   # NEGOTIATE
        self.assertEqual(len(self.cards), 0)
        rmc._process(ka_mail("Xbox Series X 1TB", 200, ad_id="12", age_min=120), 6, False, set(), {})   # вигідно
        self.assertEqual(len(self.cards), 1)
        self.assertIn("Запізніла картка", self.cards[0][0])
        self.assertTrue(self.cards[0][4])   # тихо
        rmc._process(ka_mail("Xbox Series X 1TB", 200, ad_id="13", age_min=20), 6, False, set(), {})
        self.assertNotIn("Запізніла", self.cards[1][0])
        self.assertFalse(self.cards[1][4])


if __name__ == "__main__":
    unittest.main()
