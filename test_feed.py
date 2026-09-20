"""Тести стрічки mydealz → перевір (deal_feed.py). Без мережі; фікстури з test_check."""
import json
import os
import tempfile
import unittest

import deal_feed as F
from test_check import FakeFetcher, item, detail, NOW

RSS = """<rss><channel>
<item><category><![CDATA[Elektronik]]></category><pepper:merchant name="Amazon" price="27,98€"/><title><![CDATA[Anker Smart Ladegerät, Anker Nano 45W USB C]]></title><link>https://www.mydealz.de/deals/a-1</link><pubDate>Sun, 20 Sep 2026 17:00:00 +0000</pubDate></item>
<item><category><![CDATA[Hardware]]></category><pepper:merchant price="2.559,98€"/><title><![CDATA[Rennrad Wilier]]></title><link>https://www.mydealz.de/deals/b-2</link><pubDate>Sun, 20 Sep 2026 16:00:00 +0000</pubDate></item>
<item><category><![CDATA[Lebensmittel &amp; Haushalt]]></category><pepper:merchant name="EDEKA"/><title><![CDATA[Cola ab 2 Kisten]]></title><link>https://www.mydealz.de/deals/c-3</link><pubDate>Sun, 20 Sep 2026 15:00:00 +0000</pubDate></item>
</channel></rss>"""


class ParseTests(unittest.TestCase):
    def test_price_formats(self):
        self.assertEqual(F.parse_price("250€"), 250.0)
        self.assertEqual(F.parse_price("67,99€"), 67.99)
        self.assertEqual(F.parse_price("2.559,98€"), 2559.98)
        self.assertEqual(F.parse_price("1,5€"), 1.5)
        self.assertIsNone(F.parse_price(None))
        self.assertIsNone(F.parse_price(""))

    def test_rss_parse(self):
        d = F.parse_rss(RSS)
        self.assertEqual(len(d), 3)
        self.assertEqual((d[0]["cat"], d[0]["merchant"], d[0]["price"]), ("Elektronik", "Amazon", 27.98))
        self.assertEqual(d[1]["price"], 2559.98)
        self.assertIsNone(d[1]["merchant"])            # магазин без name
        self.assertIsNone(d[2]["price"])
        self.assertEqual(d[2]["cat"], "Lebensmittel & Haushalt")   # &amp; розкодовано в заголовках, не в категорії
        self.assertTrue(d[0]["link"].endswith("a-1"))


class FilterTests(unittest.TestCase):
    def deal(self, title, price=50.0, cat="Elektronik", merchant="Amazon"):
        return {"title": title, "price": price, "cat": cat, "merchant": merchant}

    def test_passes_normal_product(self):
        self.assertIsNone(F.prefilter(self.deal("Garmin Forerunner 255 Sport Smartwatch (GPS Version)", 173.55)))

    def test_rejects_various(self):
        cases = [
            (self.deal("X", price=None), "без ціни"),
            (self.deal("Reise", cat="Urlaub & Reisen"), "подорожі"),
            (self.deal("Kabel", price=3.99), "ціна поза"),
            (self.deal("Rennrad", price=2559.98), "ціна поза"),
            (self.deal("Sherlock Holmes Switch eShop", 19.0), "нове"),
            (self.deal("Kopfhörer B-Ware", 40.0), "нове"),
            (self.deal("Anker Ladegerät Refurbished", 40.0), "нове"),
            (self.deal("Spiel Nintendo Switch", 20.0, merchant="Nintendo eShop"), "цифров"),
            (self.deal("Cola ab 2 Kisten", 14.99), "змінна"),
            (self.deal("Google Play Geschenkkarte 25", 25.0), "нове"),
        ]
        for d, frag in cases:
            r = F.prefilter(d)
            self.assertIsNotNone(r, d)
            self.assertIn(frag, r, d)

    def test_query_extraction(self):
        q = F.make_query
        self.assertEqual(q("Garmin Forerunner 255 Sport Smartwatch (GPS Version)"), "Garmin Forerunner 255 Sport Smartwatch")
        self.assertEqual(q("[Prime] Oral-B iO 6 elektrische Zahnbürste für 69,99€"), "Oral-B iO 6 elektrische Zahnbürste")
        self.assertEqual(q("Toniebox 2 Starterset | Paw Patrol"), "Toniebox 2 Starterset")
        self.assertEqual(q("Braun Series 9 9660cc - Rasierer statt 300€"), "Braun Series 9 9660cc")
        self.assertLessEqual(len(q("a b c d e f g h i j k l").split()), 7)

    def test_build_product_uses_brand_and_model_not_generic_nouns(self):
        must = lambda q: [sorted(g)[0] for g in F.build_product(q).must]
        self.assertEqual(must("Garmin Forerunner 255 Sport Smartwatch"), ["garmin", "255"])
        self.assertEqual(must("Bulova Classic 96M177 Damenarmbanduhr"), ["bulova", "96m177"])
        m = must("Oral-B iO 6 elektrische Zahnbürste")
        self.assertIn("oral", m)
        self.assertIn("6", m)
        self.assertIn("io", m)                    # «iO 6» ≠ «iO Kids 6+» ≠ «iO 5»
        self.assertNotIn("zahnbuerste", m)        # загальний іменник не обов'язковий
        self.assertEqual(must("Toniebox 2 Starterset"), ["toniebox", "2"])
        # без моделі — стандартна логіка check, а не порожній must
        self.assertTrue(F.build_product("Birkenstock Arizona Taupe").must)

    def test_price_band_and_feed_urls_are_configurable(self):
        d = {"title": "Sony WH-1000XM5", "price": 90.0, "cat": "Elektronik", "merchant": "Amazon"}
        self.assertIsNone(F.prefilter(d))                                  # у стандартній смузі €12–450
        self.assertIn("ціна поза", F.prefilter(d, pmin=100, pmax=450))     # у смузі «крупні угоди» — ні
        self.assertIsNone(F.prefilter({**d, "price": 150.0}, pmin=100, pmax=450))
        urls = F.feed_urls(["elektronik", " gaming ", ""], hot=True)
        self.assertIn("https://www.mydealz.de/rss/hot", urls)
        self.assertIn("https://www.mydealz.de/rss/gruppe/gaming", urls)
        self.assertEqual(sum(1 for u in urls if u.endswith("/rss/new")), 1)

    def test_inbound_shipping_assumption(self):
        self.assertEqual(F.inbound_ship("Amazon"), 0.0)
        self.assertEqual(F.inbound_ship("MediaMarkt"), F.INBOUND_SHIP)
        self.assertEqual(F.inbound_ship(None), F.INBOUND_SHIP)

    def test_score_is_profit_per_week_of_capital(self):
        self.assertEqual(F.score(20.0, 4.0), 5.0)
        self.assertEqual(F.score(20.0, 0.5), 20.0)     # швидше за тиждень не рахуємо як «менше тижня»
        self.assertIsNone(F.score(None, 2.0))


def market_items(price_list, title="Toniebox 2 Starterset Paw Patrol"):
    cats = [{"categoryId": "1", "categoryName": "Spielzeug"}]
    return [item(f"{title} Nr{i}", p, seller=f"s{i}", cats=cats, item_id=f"v1|{i}|0")
            for i, p in enumerate(price_list)]


class ProcessTests(unittest.TestCase):
    D = {"cat": "Family & Kids", "title": "Toniebox 2 Starterset Paw Patrol", "merchant": "MediaMarkt",
         "price": 84.99, "link": "https://x/1", "pub": "x"}

    def test_filtered_costs_no_calls(self):
        f = FakeFetcher()
        row = F.process({**self.D, "title": "Toniebox eShop Code"}, f, stage2_left=[1])
        self.assertEqual(row["outcome"], "filtered")
        self.assertEqual(f.calls, 0)

    def test_no_market(self):
        f = FakeFetcher(search_results=[])
        row = F.process(self.D, f, stage2_left=[1])
        self.assertEqual(row["outcome"], "no-market")
        self.assertEqual(f.calls, 1)                    # лише 1 виклик на відсів

    def test_uneconomic_when_market_is_near_buy_price(self):
        f = FakeFetcher(search_results=market_items([90, 92, 94, 96, 98, 100, 102, 104]))
        row = F.process(self.D, f, stage2_left=[1])
        self.assertEqual(row["outcome"], "uneconomic")
        self.assertEqual(f.calls, 1)

    def test_candidate_gets_velocity_stage(self):
        prices = [125, 128, 130, 132, 135, 138, 140, 142]
        its = market_items(prices)
        details = {it["itemId"]: detail(20, 50, 30, it["itemId"]) for it in its}
        f = FakeFetcher(search_results=its, items=details)
        row = F.process(self.D, f, stage2_left=[1])
        self.assertEqual(row["outcome"], "candidate")
        self.assertIn(row["verdict"], ("BUY", "SKIP", "UNKNOWN"))
        self.assertGreater(f.calls, 1)                  # був вимір швидкості
        self.assertIsNotNone(row["net"])
        self.assertGreater(row["net"], 0)

    def test_candidate_pending_when_stage2_budget_spent(self):
        f = FakeFetcher(search_results=market_items([125, 128, 130, 132, 135, 138, 140, 142]))
        row = F.process(self.D, f, stage2_left=[0])
        self.assertEqual(row["outcome"], "candidate")
        self.assertEqual(row["verdict"], "PENDING")
        self.assertEqual(f.calls, 1)

    def test_min_profit_threshold_changes_classification(self):
        its = market_items([125, 128, 130, 132, 135, 138, 140, 142])
        f = FakeFetcher(search_results=its)
        loose = F.process(self.D, f, stage2_left=[0], min_profit=15)
        self.assertEqual(loose["outcome"], "candidate")
        f2 = FakeFetcher(search_results=its)
        strict = F.process(self.D, f2, stage2_left=[0], min_profit=60)       # «крупні угоди»: ≥€60 чистими
        self.assertEqual(strict["outcome"], "uneconomic")

    def test_thin_market_never_candidate(self):
        f = FakeFetcher(search_results=market_items([140, 150]))
        row = F.process(self.D, f, stage2_left=[1])
        self.assertEqual(row["outcome"], "thin-market")


class StateAndReportTests(unittest.TestCase):
    def test_seen_roundtrip_and_report(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "s.json")
            F.save_seen({"a", "b"}, sp)
            self.assertEqual(F.load_seen(sp), {"a", "b"})
            self.assertEqual(F.load_seen(os.path.join(d, "nope.json")), set())
            lp = os.path.join(d, "log.jsonl")
            rows = [
                {"cat": "Elektronik", "outcome": "candidate", "verdict": "BUY", "buy": 80.0, "net": 20.0, "roi": 0.25,
                 "score": 5.0, "weeks": 4.0, "market": {"fast": 120.0, "n": 10, "sellers": 8}, "merchant": "Amazon",
                 "title": "Test Produkt", "flags": []},
                {"cat": "Elektronik", "outcome": "filtered"},
                {"cat": "Fashion", "outcome": "no-market"},
            ]
            with open(lp, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            text = F.report(lp)
            self.assertIn("Elektronik", text)
            self.assertIn("КАНДИДАТИ", text)
            self.assertIn("Test Produkt", text)


if __name__ == "__main__":
    unittest.main()
