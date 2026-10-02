"""Відповідь продавця реплаєм на картку (02.10): карта карток і пошук картки. Без мережі."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("RAM_PRICES_OFF", "1")
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import cardmap
import ka_reply
import sell

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


class CardMapTest(unittest.TestCase):
    def test_remember_and_prune(self):
        c = {}
        cardmap.remember(c, "https://www.kleinanzeigen.de/s-anzeige/ram/3528457610-225-1", 101, "2x 16GB Samsung DDR4-3200", NOW)
        cardmap.remember(c, "https://www.kleinanzeigen.de/s-anzeige/1111111", 102, "PS5 Slim", NOW - timedelta(days=20))
        cardmap.remember(c, "https://www.kleinanzeigen.de/s-anzeige/2222222", 103, "Xbox", NOW)
        cardmap.remember(c, "https://www.ebay.de/itm/123", 104, "eBay — не KA", NOW)
        cardmap.remember(c, "https://www.kleinanzeigen.de/s-anzeige/3333333", None, "без id", NOW)
        self.assertEqual(sorted(c), ["2222222", "3528457610"])

    def test_find_by_id_then_title(self):
        c = {}
        cardmap.remember(c, "/s-anzeige/3528457610", 101, "2x 16GB Samsung DDR4-3200 RAM Desktop (32GB Kit)", NOW)
        cardmap.remember(c, "/s-anzeige/4444444", 105, "Corsair Vengeance LPX 32GB DDR4", NOW)
        self.assertEqual(cardmap.find(c, ["3528457610"], ""), 101)
        self.assertEqual(cardmap.find(c, ["999"], "2x 16GB Samsung DDR4-3200 RAM Desktop"), 101)
        self.assertEqual(cardmap.find(c, [], "Corsair Vengeance LPX 32GB DDR4 3200"), 105)
        self.assertIsNone(cardmap.find(c, [], "Sony PS5 Slim Disc"))      # не наша картка — окремим повідомленням
        self.assertIsNone(cardmap.find(c, [], "RAM"))                     # надто мало слів — не вгадуємо

    def test_same_title_newest_card(self):
        c = {}
        cardmap.remember(c, "/s-anzeige/5555551", 201, "Xbox Series X 1TB", NOW - timedelta(days=2))
        cardmap.remember(c, "/s-anzeige/5555552", 202, "Xbox Series X 1TB", NOW)
        self.assertEqual(cardmap.find(c, [], "Xbox Series X 1TB"), 202)


class KaReplyTest(unittest.TestCase):
    def test_main_replies_to_found_card(self):
        import requests
        posted = []
        d = tempfile.mkdtemp()
        ka = os.path.join(d, "ka.json")
        with open(ka, "w", encoding="utf-8") as fh:
            json.dump({"cards": {"3528457610": {"m": 77, "t": "2x 16GB Samsung DDR4-3200", "ts": NOW.isoformat()}}}, fh)
        blob, mac = sell.seal({"text": "💬 <b>Відповідь продавця</b>", "markup": "{}", "listing": "", "ids": ["3528457610"]},
                              "tok", "n1")
        old_post, old_env = requests.post, dict(os.environ)
        requests.post = lambda url, data=None, **k: (posted.append(data), type("R", (), {"status_code": 200})())[1]
        os.environ["OFFICE_BOT_TOKEN"] = "tok"
        sys.argv = ["ka_reply.py", "--blob", blob, "--mac", mac, "--nonce", "n1", "--ka", ka, "--share", os.path.join(d, "none.json")]
        try:
            ka_reply.main()
        finally:
            requests.post = old_post
            os.environ.clear()
            os.environ.update(old_env)
        self.assertEqual(json.loads(posted[0]["reply_parameters"])["message_id"], 77)
        self.assertEqual(posted[0]["parse_mode"], "HTML")


class RememberedOnSendTest(unittest.TestCase):
    def test_ka_mail_card_is_remembered(self):
        import ram_mail_check as rmc
        from test_ram_mail_hint import ka_mail
        saved = (rmc.send_telegram_card, rmc.check_listing, rmc.add_to_card, rmc.CARDS)
        rmc.send_telegram_card = lambda *a: (555, "{}")
        rmc.add_to_card = lambda *a, **k: False
        rmc.check_listing = lambda *a: {"level": "low", "score": 0, "reasons": [], "seller": "x"}
        rmc.CARDS = {}
        try:
            rmc._process(ka_mail("Xbox Series X 1TB", 200, ad_id="3523290701"), 6, False, set(), {})
            self.assertEqual(rmc.CARDS["3523290701"]["m"], 555)
        finally:
            rmc.send_telegram_card, rmc.check_listing, rmc.add_to_card, rmc.CARDS = saved


if __name__ == "__main__":
    unittest.main()
