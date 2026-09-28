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


class TestPickupAndDefects(unittest.TestCase):
    """27.09: самовивіз у Гамбурзі (замасковані індекси «22***») і відсів несправного за описом."""

    def test_masked_zip_pickup_only(self):
        r = evaluate_ebay(item("Xbox Series X Konsole", 250, ship=None, zip_="22***"))
        self.assertTrue(r["pickup"])
        self.assertAlmostEqual(r["buy_cost"], 256.0)

    def test_pickup_cheaper_than_shipping(self):
        lst = item("Xbox Series X Konsole", 300, ship="10.49", zip_="21***")
        lst["pickup_ok"] = True
        r = evaluate_ebay(lst)
        self.assertTrue(r["pickup"])
        self.assertAlmostEqual(r["buy_cost"], 306.0)
        self.assertIn("🚶", format_card(r, lst, risk_of(lst, "", 545), "new", NOW))

    def test_seen_then_pickup_revealed(self):
        st = {}
        lst = item("Xbox Series X", 300)
        self.assertEqual(decide(lst, st, NOW), "new")
        lst2 = dict(lst, pickup_ok=True)
        self.assertEqual(decide(lst2, st, NOW), "pickup")
        self.assertIsNone(decide(lst2, st, NOW))

    def test_broken_reason(self):
        from ram_alert import broken_reason
        self.assertTrue(broken_reason("Die Konsole ist defekt, HDMI Port gebrochen"))
        self.assertTrue(broken_reason("Laufwerk funktioniert nicht mehr"))
        self.assertIsNone(broken_reason("Keine Defekte, läuft einwandfrei"))
        self.assertIsNone(broken_reason("Konsole top, nur der zweite Controller ist defekt"))
        self.assertIsNone(broken_reason("nicht defekt, nur selten benutzt"))
        self.assertIsNone(broken_reason(None))


class TestSellerMessage(unittest.TestCase):
    def test_card_has_seller_text_and_buttons(self):
        from ebay_watch import ebay_message, keyboard
        lst = item("Microsoft Xbox Series X 1TB Konsole", 355, ship="10.99", offer=True)
        r = evaluate_ebay(lst)
        r["desc"] = "Läuft einwandfrei, Rechnung vorhanden."
        txt = format_card(r, lst, risk_of(lst, r["desc"], 545), "new", NOW)
        self.assertIn("Текст продавцю", txt)
        msg = ebay_message(r)
        self.assertLessEqual(len(msg), 256)
        self.assertIn("über eBay", msg)
        self.assertNotIn("Rechnung?", msg)   # опис уже відповів
        rows = keyboard(lst["url"], r)["inline_keyboard"]
        self.assertEqual(len(rows), 3)       # посилання + текст + текст із пропозицією

    def test_pickup_message(self):
        from ebay_watch import ebay_message
        lst = item("Xbox Series X Konsole", 300, ship=None, zip_="22***")
        m = ebay_message(evaluate_ebay(lst))
        self.assertIn("Hamburg ab", m)
        self.assertLessEqual(len(m), 256)


class TestDefectSymptoms(unittest.TestCase):
    """27.09: PS5 «geht nach 5-30 Minuten von alleine aus … an Bastler» — несправна, навіть без слова «defekt»."""

    def test_symptoms(self):
        from ram_alert import broken_reason
        for d in ["Sie geht an, aber nach 5-30 Minuten geht sie von alleine immer aus, verkaufe sie an Bastler",
                  "Nach einer Weile schaltet sich die Konsole einfach ab", "Laufwerk liest keine Discs mehr",
                  "PS5 zeigt nur Blue Light", "Verkauf für Bastler"]:
            self.assertTrue(broken_reason(d), d)

    def test_not_symptoms(self):
        from ram_alert import broken_reason
        for d in ["Läuft einwandfrei, wird nicht heiß, kein Stick Drift", "Konsole wurde gereinigt, leise und kühl",
                  "Abholung in Hamburg oder Versand"]:
            self.assertIsNone(broken_reason(d), d)


class TestNegotiateFixedPrice(unittest.TestCase):
    """28.09: «ЗАПРОПОНУЙ ЦІНУ» без Preisvorschlag не мав тексту з сумою."""

    def test_fixed_price_negotiate_has_offer_text(self):
        from ebay_watch import ebay_message, keyboard, offer_ebay
        lst = item("Microsoft Xbox Series X 1TB Konsole", 390, ship="10.99", offer=False)
        r = evaluate_ebay(lst)
        self.assertEqual(r["verdict"], "NEGOTIATE")
        o = offer_ebay(r)
        self.assertIsNotNone(o)
        self.assertIn(f"für {o} €", ebay_message(r, o))
        self.assertIn("Preis an", ebay_message(r, o))
        self.assertEqual(len(keyboard(lst["url"], r)["inline_keyboard"]), 3)
        self.assertIn(f"{o} €", format_card(r, lst, risk_of(lst, "", 545), "new", NOW))

    def test_too_cheap_headline(self):
        from ram_alert import evaluate, format_html
        r = evaluate("ADATA Premier DDR5-5600 16GB RAM Kit (2x 8GB)", 30)
        html_ = format_html(r)
        self.assertIn("ПІДОЗРІЛО ДЕШЕВО", html_)
        self.assertNotIn("ВІДМІННО", html_)


class TestAuctions(unittest.TestCase):
    """28.09: аукціони — сповіщення за ≤3 год до кінця, якщо ставка ще нижча за межу; раз на оголошення."""

    def _it(self, bid, hours_left):
        from datetime import timedelta
        end = (NOW + timedelta(hours=hours_left)).isoformat().replace("+00:00", "Z")
        return dict(itemId="v1|9|0", title="Corsair Vengeance DDR5 32GB (2x16GB) 6000MHz", currentBidPrice={"value": str(bid)},
                    itemEndDate=end, buyingOptions=["AUCTION"], conditionId="3000", itemWebUrl="https://www.ebay.de/itm/9",
                    shippingOptions=[{"shippingCost": {"value": "6.19"}}], seller={"username": "s", "feedbackScore": 100},
                    itemLocation={"postalCode": "10***", "country": "DE"})

    def test_candidate(self):
        from ebay_watch import auction_candidate
        st = {}
        self.assertIsNone(auction_candidate(self._it(140, 30), st, NOW))    # ще далеко до кінця
        self.assertIsNotNone(auction_candidate(self._it(140, 2), st, NOW))  # ≤3 год, ставка нижча межі
        self.assertIsNone(auction_candidate(self._it(140, 2), st, NOW))     # вдруге — ні
        self.assertIsNone(auction_candidate(dict(self._it(300, 2), itemId="v1|8|0"), {}, NOW))   # уже дорого

    def test_share_link_ids(self):
        from ebay_watch import ebay_item_id
        self.assertEqual(ebay_item_id("https://www.ebay.de/itm/257771018556?_skw=ddr5&hash=x"), "257771018556")
        self.assertEqual(ebay_item_id("https://www.ebay.de/itm/Corsair-DDR5/168736560292"), "168736560292")
        self.assertIsNone(ebay_item_id("https://www.kleinanzeigen.de/s-anzeige/x/3525219406-279-1"))
