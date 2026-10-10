"""Тихі години (09.10): уночі «можна» без звуку, «бери» й аукціони — зі звуком. Без мережі."""
import os
import sys
import unittest
from datetime import datetime, timezone

os.environ.setdefault("RAM_PRICES_OFF", "1")
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import quiet


class QuietTest(unittest.TestCase):
    def test_hours_and_verdicts(self):
        night = datetime(2026, 10, 9, 22, 30, tzinfo=timezone.utc)   # 00:30 за Берліном (CEST)
        day = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)      # 12:00
        early = datetime(2026, 10, 9, 4, 59, tzinfo=timezone.utc)    # 06:59
        self.assertTrue(quiet.silent("BUY", night))
        self.assertTrue(quiet.silent("NEGOTIATE", early))
        self.assertFalse(quiet.silent("BUY-GOOD", night))
        self.assertFalse(quiet.silent("BUY-EXCELLENT", night))
        self.assertFalse(quiet.silent("BUY", day))
        os.environ["QUIET_HOURS"] = "0"
        try:
            self.assertFalse(quiet.silent("BUY", night))
        finally:
            os.environ.pop("QUIET_HOURS")

    def test_ka_card_uses_it(self):
        import ram_mail_check as rmc
        from test_ram_mail_hint import ka_mail
        calls, saved = [], (rmc.send_telegram_card, rmc.check_listing, rmc.add_to_card, quiet.night)
        rmc.send_telegram_card = lambda *a: (calls.append(a), (1, "{}"))[1]
        rmc.add_to_card = lambda *a, **k: False
        rmc.check_listing = lambda *a: {"level": "low", "score": 0, "reasons": [], "seller": "x", "buy_now": True}
        quiet.night = lambda now=None: True
        try:
            rmc._process(ka_mail("Xbox Series X 1TB", 410, ad_id="901"), 6, False, set(), {})   # «бери» (10.10: межі вищі)
            rmc._process(ka_mail("Xbox Series X 1TB", 200, ad_id="902"), 6, False, set(), {})   # «бери»
        finally:
            rmc.send_telegram_card, rmc.check_listing, rmc.add_to_card, quiet.night = saved
        self.assertEqual([c[4] for c in calls], [True, False])


class BoughtButtonTest(unittest.TestCase):
    def test_pickup_card_has_bought_button(self):
        import ram_mail_check as rmc
        kb = rmc.build_keyboard("https://www.kleinanzeigen.de/s-anzeige/x/3531265450-1", None, None, None, ("3531265450", 50.0))
        self.assertEqual(kb["inline_keyboard"][-1][0]["callback_data"], "b|3531265450|50")
        self.assertNotIn("callback_data", str(rmc.build_keyboard("https://k", None)))   # не самовивіз — без кнопки


if __name__ == "__main__":
    unittest.main()
