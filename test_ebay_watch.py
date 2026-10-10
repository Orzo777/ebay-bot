"""Тести eBay-джерела (research/ebay_watch.py): оцінка з eBay-вартістю, свіжість, здешевлення, шахраї. Без мережі."""
import os as _os
_os.environ.setdefault("RAM_PRICES_OFF", "1")   # цифри Terapeak, а не щоденні ціни сторожа (research/ram_prices.json)
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
sys.path.insert(0, "research")
from ebay_watch import decide, evaluate_ebay, format_card, listing_of, offer_ebay, prune, risk_of

# Перша Switch вимкнена 04.10 (console_alert.SWITCH1_ON); ці тести стережуть логіку її розпізнавання — на час модуля вмикаємо
def setUpModule():
    import console_alert
    console_alert._switch1_saved, console_alert.SWITCH1_ON = console_alert.SWITCH1_ON, True


def tearDownModule():
    import console_alert
    console_alert.SWITCH1_ON = console_alert._switch1_saved

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



class TestPass7(unittest.TestCase):
    def _raw(self, zip_, pickup=True, group=False):
        it = dict(itemId="v1|9|0", title="Sony PlayStation 5 825 GB Digital Edition", price={"value": "300"},
                  itemLocation={"postalCode": zip_, "country": "DE"}, buyingOptions=["FIXED_PRICE"],
                  shippingOptions=[{"shippingCost": {"value": "10.49"}}], seller={"feedbackScore": 50})
        if pickup:
            it["pickupOptions"] = [{"pickupLocationType": "STORE"}]
        if group:
            it["itemGroupHref"] = "https://api.ebay.com/buy/browse/v1/item/get_items_by_item_group?item_group_id=1"
        return it

    def test_pickup_only_in_hamburg(self):
        self.assertFalse(listing_of(self._raw("88***"))["pickup_ok"])
        self.assertFalse(evaluate_ebay(listing_of(self._raw("88***"))).get("pickup"))
        self.assertTrue(listing_of(self._raw("22***"))["pickup_ok"])

    def test_variation_group_skipped(self):
        self.assertEqual(evaluate_ebay(listing_of(self._raw("10***", pickup=False, group=True)))["verdict"], "SKIP")

    def test_defect_in_title(self):
        self.assertEqual(evaluate_ebay(item("Sony PS5 Konsole weiß, Laufwerk streikt", 250))["verdict"], "SKIP")

    def test_css_stripped_from_description(self):
        from unittest import mock
        import ebay_watch
        d = {"description": "<style>.a{color:red}" + "x{}" * 5000 + "</style><p>Ich suche eine PS5</p>"}
        with mock.patch("main._request_with_backoff", return_value=d):
            self.assertEqual(ebay_watch.fetch_desc(mock.Mock(), "1"), "Ich suche eine PS5")


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
        self.assertNotIn("<code>", txt)   # 10.10: тексти продавцю — лише в кнопках «📋»
        self.assertTrue(txt.startswith("🛒 <b>eBay</b> · 🟡 <b>БЕРИ</b> · заробіток ≈ <b>"))
        self.assertLessEqual(txt.count("\n"), 9)   # коротка картка
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
        lst = item("Microsoft Xbox Series X 1TB Konsole", 415, ship="10.99", offer=False)   # 09.10: комісія eBay 0 — ціни зсунуті вгору на неї
        r = evaluate_ebay(lst)
        self.assertEqual(r["verdict"], "NEGOTIATE")
        o = offer_ebay(r)
        self.assertIsNotNone(o)
        self.assertIn(f"für {o} €", ebay_message(r, o))
        self.assertIn("Preis an", ebay_message(r, o))
        kb = keyboard(lst["url"], r)["inline_keyboard"]
        self.assertEqual(len(kb), 2)   # «торгуйся»: одна кнопка, одразу з пропозицією (29.09)
        self.assertIn(f"für {o} €", kb[1][0]["copy_text"]["text"])
        card = format_card(r, lst, risk_of(lst, "", 545), "new", NOW)
        self.assertIn(f"Напиши продавцю: <b>{o} €</b>", card)
        self.assertEqual(card.count("<code>"), 0)   # текст із пропозицією — кнопкою (kb[1])

    def test_too_cheap_headline(self):
        from ram_alert import evaluate, format_html
        r = evaluate("ADATA Premier DDR5-5600 16GB RAM Kit (2x 8GB)", 30)
        html_ = format_html(r)
        self.assertIn("ПІДОЗРІЛО ДЕШЕВО", html_)
        self.assertNotIn("ВІДМІННО", html_)


class TestAuctions(unittest.TestCase):
    """Аукціони: сповіщення в останні 2–20 хв (29.09; було ≤3 год), якщо ставка ще нижча за межу; раз на оголошення."""

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
        self.assertIsNone(auction_candidate(self._it(140, 2), st, NOW))        # 2 год — ще рано
        self.assertIsNone(auction_candidate(self._it(140, 1 / 60), st, NOW))   # 1 хв — уже пізно
        self.assertIsNotNone(auction_candidate(self._it(140, 0.25), st, NOW))  # 15 хв, ставка нижча межі
        self.assertIsNone(auction_candidate(self._it(140, 0.2), st, NOW))      # вдруге — ні
        self.assertIsNone(auction_candidate(dict(self._it(300, 0.25), itemId="v1|8|0"), {}, NOW))   # уже дорого

    def test_low_bid_is_not_a_scam_floor(self):
        # 30.09: «дешевше €150» / «заглушка» — для фіксованих цін; ставка 45 € за 15 хв до кінця — шанс, а не шахрай
        from ebay_watch import auction_candidate
        it = dict(self._it(45, 0.25), itemId="v1|6|0", title="Nintendo Switch Konsole Grau 32 GB mit Joy-Con")
        got = auction_candidate(it, {}, NOW)
        self.assertIsNotNone(got)
        self.assertEqual(got[1]["price"], 45)

    def test_bid_up_to_ceiling(self):
        # на останніх хвилинах — до самої межі (було 90% межі)
        from ebay_watch import auction_candidate, evaluate_ebay, listing_of
        base = self._it(100, 0.25)
        cap = evaluate_ebay(listing_of(dict(base, price=base["currentBidPrice"])))["cap"]
        self.assertIsNotNone(auction_candidate(dict(self._it(round(cap - 8), 0.25), itemId="v1|5|0"), {}, NOW))
        self.assertIsNone(auction_candidate(dict(self._it(round(cap), 0.25), itemId="v1|4|0"), {}, NOW))

    def test_last_minutes_text(self):
        from ebay_watch import auction_candidate, auction_lines
        it = dict(self._it(140, 0.25), itemId="v1|7|0")
        lst, r = auction_candidate(it, {}, NOW)
        text = " ".join(auction_lines(r, it, NOW))
        self.assertIn("15 хв", text)
        self.assertIn("1–2 хв до кінця", text)

    def test_live_card(self):
        # 06.10: картка аукціону оновлюється — поточна ставка, час, «уже дорожче», після кінця — фінал
        from datetime import timedelta

        import ebay_watch as ew
        it = dict(self._it(140, 0.25), itemId="v1|3|0", bidCount=4)
        lst, r = ew.auction_candidate(it, {}, NOW)
        card = dict(ew.auction_card(it, r, "🛒 <b>eBay</b>\n<i>Corsair</i>\n", "🏷 Продати: 300–330 €"), m=77, kb="{}")
        first = ew.live_text(card, NOW)
        self.assertIn("🟢 <b>Іде</b>", first)
        self.assertIn("зараз 140 € (4 ставок)", first)
        self.assertIn("🏷 Продати", first)
        max_bid = int((r["cap"] - r["ship_in"]) // 1)
        edits, answers = [], [dict(it, currentBidPrice={"value": str(max_bid + 10)}, bidCount=9), None]
        saved = (ew.edit_card, ew.__dict__.get("_request_with_backoff"))
        import main
        old_req = main._request_with_backoff
        main._request_with_backoff = lambda *a, **k: answers.pop(0)
        ew.edit_card = lambda mid, text, kb: edits.append((mid, text))
        st = {"auction_cards": {"v1|3|0": card}}
        try:
            ew.update_auction_cards(type("C", (), {"_headers": lambda self: {}})(), st, NOW + timedelta(minutes=5))
            self.assertIn(f"зараз {max_bid + 10} € (9 ставок)", edits[-1][1])
            self.assertIn("Уже дорожче вигідного", edits[-1][1])
            self.assertIn("v1|3|0", st["auction_cards"])
            ew.update_auction_cards(type("C", (), {"_headers": lambda self: {}})(), st, NOW + timedelta(minutes=16))   # 404 після кінця
            self.assertIn("🏁 <b>Аукціон завершено</b>", edits[-1][1])
            self.assertIn(f"фінальна ставка {max_bid + 10} €", edits[-1][1])
            self.assertIn("дорожче за наш максимум", edits[-1][1])
            self.assertEqual(st["auction_cards"], {})   # фінал показали — забули
            self.assertEqual(st["auction_results"][0]["final"], max_bid + 10)   # для тижневого звіту
            self.assertEqual(edits[0][0], 77)
        finally:
            main._request_with_backoff = old_req
            ew.edit_card = saved[0]

    def test_share_link_ids(self):
        from ebay_watch import ebay_item_id
        self.assertEqual(ebay_item_id("https://www.ebay.de/itm/257771018556?_skw=ddr5&hash=x"), "257771018556")
        self.assertEqual(ebay_item_id("https://www.ebay.de/itm/Corsair-DDR5/168736560292"), "168736560292")
        self.assertIsNone(ebay_item_id("https://www.kleinanzeigen.de/s-anzeige/x/3525219406-279-1"))



class CleanUrlTest(unittest.TestCase):
    def test_short_item_link(self):
        from ebay_watch import clean_url
        self.assertEqual(clean_url("https://www.ebay.de/itm/336837002037?_skw=ddr4&hash=item4e6:g:abc&amdata=enc%3AAQ"),
                         "https://www.ebay.de/itm/336837002037")
        self.assertEqual(clean_url("https://www.ebay.de/itm/Corsair-32GB/336837002037"), "https://www.ebay.de/itm/336837002037")
        self.assertIsNone(clean_url(None))



class ShareAuctionTest(unittest.TestCase):
    """10.10: аукціон, яким поділились, за стартові 1 € — не «заглушка», а порада максимальної ставки."""

    def test_share_auction_one_euro(self):
        import ebay_watch as ew
        import main
        item = {"itemId": "v1|307224749417|0", "title": "Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200 CL16",
                "currentBidPrice": {"value": "1.00", "currency": "EUR"}, "buyingOptions": ["AUCTION"], "bidCount": 0,
                "itemEndDate": (NOW + timedelta(days=5)).isoformat().replace("+00:00", "Z"),
                "shippingOptions": [{"shippingCost": {"value": "5.49"}}], "itemWebUrl": "https://www.ebay.de/itm/307224749417?x",
                "conditionId": "3000", "seller": {"username": "s", "feedbackScore": 50, "feedbackPercentage": "100.0"},
                "itemLocation": {"country": "DE"}}
        old_req, old_desc = main._request_with_backoff, ew.fetch_desc
        main._request_with_backoff = lambda *a, **k: item
        ew.fetch_desc = lambda client, iid: "Funktioniert einwandfrei, getestet."
        try:
            msg, r, lst = ew.share_ebay("307224749417", client=type("C", (), {"_headers": lambda self: {}})(), now=NOW)
        finally:
            main._request_with_backoff, ew.fetch_desc = old_req, old_desc
        self.assertEqual(r["verdict"], "AUCTION")
        self.assertNotIn("заглушка", msg)
        self.assertIn("🔨 <b>Аукціон</b>: зараз 1 €", msg)
        self.assertRegex(msg, r"максимальну ставку \d+ €")
        self.assertEqual(lst["url"], "https://www.ebay.de/itm/307224749417")
        # 10.10: ставок немає, до кінця 5 днів → попросити «Sofort-Kaufen» між «вигідно» і максимумом
        self.assertIn("Sofort-Kaufen", msg)
        self.assertEqual(r["bin_offer"] % 5, 0)
        self.assertLess(r["bin_offer"], r["cap"] - r["ship_in"])
        self.assertIn(f"für {r['bin_offer']} €", r["bin_text"])
        self.assertLessEqual(len(r["bin_text"]), 256)

    def test_share_auction_with_best_offer(self):
        # 10.10: «EUR 1,00 oder Preisvorschlag» — аукціон, де продавець дозволив пропозицію ціни
        import ebay_watch as ew
        import main
        item = {"itemId": "v1|307224749417|0", "title": "32GB DDR4 RAM (2x16GB) für Laptop/Notebook SK Hynix",
                "currentBidPrice": {"value": "1.00"}, "buyingOptions": ["AUCTION", "BEST_OFFER"], "bidCount": 0,
                "itemEndDate": (NOW + timedelta(days=9)).isoformat().replace("+00:00", "Z"),
                "shippingOptions": [{"shippingCost": {"value": "5.49"}}], "itemWebUrl": "https://www.ebay.de/itm/307224749417",
                "conditionId": "3000", "seller": {"username": "s", "feedbackScore": 626, "feedbackPercentage": "100.0"},
                "itemLocation": {"country": "DE"}}
        old_req, old_desc = main._request_with_backoff, ew.fetch_desc
        main._request_with_backoff = lambda *a, **k: item
        ew.fetch_desc = lambda client, iid: "Gebraucht, aber super Zustand und voll funktionsfähig."
        try:
            msg, r, lst = ew.share_ebay("307224749417", client=type("C", (), {"_headers": lambda self: {}})(), now=NOW)
        finally:
            main._request_with_backoff, ew.fetch_desc = old_req, old_desc
        self.assertIn("є <b>Preisvorschlag</b>", msg)
        self.assertNotIn("Sofort-Kaufen", msg)
        self.assertTrue(r["bin_label"].startswith("📋 Текст до Preisvorschlag"))
        self.assertIn(f"{r['bin_offer']} €", r["bin_text"])

    def test_bin_offer_only_without_bids(self):
        from ebay_watch import bin_offer
        r = {"cap": 80.0, "good": 65.0, "ship_in": 5.0, "price": 1.0}
        end = (NOW + timedelta(days=2)).isoformat().replace("+00:00", "Z")
        self.assertEqual(bin_offer(r, {"bidCount": 0, "itemEndDate": end}, NOW)[0], 65)
        self.assertIsNone(bin_offer(r, {"bidCount": 2, "itemEndDate": end}, NOW))   # є ставки — eBay не дозволить
        soon = (NOW + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
        self.assertIsNone(bin_offer(r, {"bidCount": 0, "itemEndDate": soon}, NOW))
