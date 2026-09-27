"""Тести eBay-джерела (research/ebay_watch.py): оцінка з eBay-вартістю, свіжість, здешевлення, шахраї. Без мережі."""
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, ".")
sys.path.insert(0, "research")
from ebay_watch import decide, evaluate_ebay, format_card, listing_of, offer_ebay, prune, risk_of

NOW = datetime(2026, 9, 27, 14, 0, tzinfo=timezone.utc)


def item(title, price, ship="6.99", created="2026-09-27T13:50:00.000Z", offer=False, fb=120, pct="100.0",
         cond="3000", zip_="10115", iid="v1|1|0"):
    it = dict(itemId=iid, title=title, price={"value": str(price), "currency": "EUR"},
              itemWebUrl="https://www.ebay.de/itm/1", itemCreationDate=created,
              buyingOptions=["FIXED_PRICE"] + (["BEST_OFFER"] if offer else []), conditionId=cond,
              seller={"username": "verk", "feedbackScore": fb, "feedbackPercentage": pct},
              itemLocation={"postalCode": zip_, "country": "DE"})
    if ship is not None:
        it["shippingOptions"] = [{"shippingCost": {"value": ship, "currency": "EUR"}}]
    return listing_of(it)


class TestEvaluate(unittest.TestCase):
    def test_cheap_xbox_is_buy_with_ebay_cost(self):
        r = evaluate_ebay(item("Microsoft Xbox Series X 1TB Konsole mit Controller", 250, ship="10.99"))
        self.assertTrue(r["verdict"].startswith("BUY"))
        self.assertAlmostEqual(r["buy_cost"], 260.99)   # без збору «Sicher bezahlen»
        self.assertFalse(any("Sicher bezahlen" in n for n in r["notes"]))

    def test_market_price_skipped(self):
        self.assertEqual(evaluate_ebay(item("Microsoft Xbox Series X 1TB Konsole", 480))["verdict"], "SKIP")

    def test_accessory_skipped(self):
        self.assertEqual(evaluate_ebay(item("Xbox Series X Speichererweiterung 1TB", 160))["verdict"], "SKIP")

    def test_ram_kit(self):
        r = evaluate_ebay(item("Kingston Fury Beast DDR5 32GB (2x16GB) 6000MHz", 150, ship="4.99"))
        self.assertTrue(r["verdict"].startswith("BUY"))
        self.assertAlmostEqual(r["profit_est"], r["net_q"] - 154.99)

    def test_parts_condition_skipped(self):
        self.assertEqual(evaluate_ebay(item("Xbox Series X Konsole", 200, cond="7000"))["verdict"], "SKIP")

    def test_pickup_only_outside_hamburg_skipped(self):
        self.assertEqual(evaluate_ebay(item("Xbox Series X Konsole", 200, ship=None))["verdict"], "SKIP")
        r = evaluate_ebay(item("Xbox Series X Konsole", 250, ship=None, zip_="22305"))
        self.assertTrue(r["verdict"].startswith("BUY") and r["pickup"])

    def test_offer_only_with_best_offer(self):
        cap_ish = evaluate_ebay(item("Xbox Series X Konsole", 340, ship="10.99"))
        self.assertIsNone(offer_ebay(cap_ish))
        neg = evaluate_ebay(item("Xbox Series X Konsole", 355, ship="10.99", offer=True))
        self.assertIn(neg["verdict"], ("BUY", "NEGOTIATE"))
        o = offer_ebay(neg)
        self.assertIsNotNone(o)
        self.assertLessEqual(o + 10.99, neg["cap"] + 0.01)


class TestFreshness(unittest.TestCase):
    def test_new_fresh_then_seen(self):
        st = {}
        lst = item("Xbox Series X", 300)
        self.assertEqual(decide(lst, st, NOW), "new")
        self.assertIsNone(decide(lst, st, NOW))

    def test_old_listing_not_new_but_price_drop_is(self):
        st = {}
        old = item("Xbox Series X", 450, created="2026-09-20T10:00:00.000Z")
        self.assertIsNone(decide(old, st, NOW))
        self.assertEqual(decide(item("Xbox Series X", 320, created="2026-09-20T10:00:00.000Z"), st, NOW), "drop")

    def test_prune(self):
        st = {"items": {"a": [1, "2026-09-01T00:00:00+00:00"], "b": [1, NOW.isoformat()]}}
        prune(st, NOW)
        self.assertEqual(list(st["items"]), ["b"])


class TestRisk(unittest.TestCase):
    def test_contact_and_payment_block(self):
        lst = item("Xbox Series X", 300)
        self.assertTrue(risk_of(lst, "Bitte per WhatsApp melden 0151 2345678", 545)["hard"])
        self.assertTrue(risk_of(lst, "Zahlung nur PayPal Freunde und Familie", 545)["hard"])
        self.assertFalse(risk_of(lst, "Konsole läuft einwandfrei, mit OVP", 545)["hard"])

    def test_new_account_cheap_blocked_soft_warning_otherwise(self):
        self.assertTrue(risk_of(item("Xbox Series X", 250, fb=0), "", 545)["hard"])
        r = risk_of(item("Xbox Series X", 340, fb=3), "", 545)
        self.assertFalse(r["hard"])
        self.assertTrue(r["soft"])

    def test_card_renders(self):
        lst = item("Microsoft Xbox Series X 1TB Konsole", 300, offer=True)
        r = evaluate_ebay(lst)
        txt = format_card(r, lst, risk_of(lst, "", r["quick_sale"]), "new", NOW)
        self.assertIn("eBay", txt)
        self.assertIn("10 хв тому", txt)
        self.assertNotIn("Sicher bezahlen", txt)


if __name__ == "__main__":
    unittest.main()
