"""Тести оцінювача RAM (ram_check.py). Без мережі."""
import unittest

import ram_check as R

CATS = [{"categoryId": "1", "categoryName": "Arbeitsspeicher (RAM)"}, {"categoryId": "2", "categoryName": "Computer, Tablets & Netzwerk"}]


def it(title, price, seller, fb=500, ship=0.0):
    return {"itemId": f"v1|{abs(hash((title, seller))) % 10**9}|0", "title": title,
            "price": {"value": f"{price:.2f}", "currency": "EUR"},
            "shippingOptions": [{"shippingCost": {"value": f"{ship:.2f}", "currency": "EUR"}, "shippingCostType": "FIXED"}],
            "seller": {"username": seller, "feedbackScore": fb, "feedbackPercentage": "99"},
            "categories": CATS, "conditionId": "3000"}


class ParseTests(unittest.TestCase):
    def test_user_example(self):
        s = R.parse_spec("SK Hynix DDR5 UDIMM 32GB 2x16GB PC5-5600B RAM")
        self.assertEqual((s.gen, s.form, s.total_gb, s.modules, s.speed), ("ddr5", "udimm", 32, 2, 5600))

    def test_forms_and_generations(self):
        self.assertEqual(R.parse_spec("DDR4 16GB SODIMM 3200 Laptop").form, "sodimm")
        self.assertEqual(R.parse_spec("Samsung 64GB DDR4 RDIMM ECC Reg").form, "server")
        self.assertEqual(R.parse_spec("DDR5 16GB").gen, "ddr5")
        self.assertEqual(R.parse_spec("PC4-2666V 8GB").gen, "ddr4")
        self.assertEqual(R.parse_spec("Corsair 2 x 8 GB DDR4").total_gb, 16)

    def test_unparseable_is_none_not_guess(self):
        s = R.parse_spec("Arbeitsspeicher schnell")
        self.assertIsNone(s.gen)
        self.assertIsNone(s.total_gb)
        self.assertIsNone(R.parse_spec("DDR5 RAM 7GB").total_gb)           # нереальна ємність
        self.assertIsNone(R.parse_spec("DDR5 5600 MHz").total_gb)          # швидкість не ємність

    def test_comparable_rejects_other_form_or_size(self):
        s = R.parse_spec("DDR5 32GB 2x16GB UDIMM")
        self.assertTrue(R.comparable("Crucial 32GB Kit (2x16GB) DDR5-5600 UDIMM", s))
        self.assertFalse(R.comparable("Crucial 32GB Kit (2x16GB) DDR5-5600 SODIMM Laptop", s))   # інший форм-фактор
        self.assertFalse(R.comparable("Crucial 16GB DDR5-5600 UDIMM", s))                       # інша ємність
        self.assertFalse(R.comparable("DDR4 32GB 2x16GB UDIMM", s))                             # інше покоління
        self.assertFalse(R.comparable("32GB DDR5 Kit UDIMM Kühler Heatsink nur", s))            # аксесуар/шум
        self.assertFalse(R.comparable("Micron 32GB DDR5 ECC UDIMM 2x16GB", s))                  # ECC ≠ не-ECC

    def test_queries(self):
        qs = R.queries(R.parse_spec("DDR5 32GB 2x16GB UDIMM"))
        self.assertEqual(qs[0], "DDR5 32GB 2x16GB UDIMM")
        self.assertIn("DDR5 32GB UDIMM", qs)


def market(prices, title="Kingston 32GB Kit (2x16GB) DDR5-5600 UDIMM", cond="used"):
    spec = R.parse_spec("DDR5 32GB 2x16GB UDIMM")
    items = [it(f"{title} #{i}", p, f"s{i}") for i, p in enumerate(prices)]
    return R.side_stats(items, spec, cond), spec


class EvaluateTests(unittest.TestCase):
    def test_suspiciously_cheap_is_check_not_buy(self):
        used, spec = market([380, 400, 410, 420, 450, 460])
        new, _ = market([540, 550, 560, 580], cond="new")
        r = R.evaluate(spec, new, used, 75.0)
        self.assertEqual(r.verdict, "CHECK")               # 75 / ~410 = 18% → перевірити, не купувати наосліп
        self.assertTrue(any("ПЕРЕВІРИТИ" in x for x in r.reasons))
        self.assertGreater(r.deal.net, 200)

    def test_normal_discount_buy(self):
        used, spec = market([380, 400, 410, 420, 450, 460])
        new, _ = market([540, 550, 560, 580], cond="new")
        r = R.evaluate(spec, new, used, 250.0)
        self.assertEqual(r.verdict, "BUY")

    def test_no_discount_skip(self):
        used, spec = market([380, 400, 410, 420, 450, 460])
        new, _ = market([540, 550, 560, 580], cond="new")
        self.assertEqual(R.evaluate(spec, new, used, 400.0).verdict, "SKIP")

    def test_thin_used_falls_back_to_75pct_of_new_with_warning(self):
        new, spec = market([540, 550, 560, 580], cond="new")
        used, _ = market([300], cond="used")                # 1 продавець — мало
        r = R.evaluate(spec, new, used, 250.0)
        self.assertAlmostEqual(r.sale, 0.75 * new.fast, places=1)
        self.assertTrue(any("ПРИПУЩЕННЯ" in x or "не виміряно" in x for x in r.warnings + [r.sale_basis]))

    def test_used_price_is_capped_below_new(self):
        used, spec = market([600, 620, 640, 660, 680])          # «вживане» дорожче за нове — з малої вибірки
        new, _ = market([540, 550, 560, 580], cond="new")
        r = R.evaluate(spec, new, used, 250.0)
        self.assertLessEqual(r.sale, 0.9 * new.fast + 0.01)
        self.assertIn("90%", r.sale_basis)

    def test_unreadable_market_is_unknown(self):
        used, spec = market([300], cond="used")
        new, _ = market([500], cond="new")
        r = R.evaluate(spec, new, used, 100.0)
        self.assertEqual(r.verdict, "UNKNOWN")

    def test_unparsed_spec_is_unknown(self):
        r = R.evaluate(R.parse_spec("щось швидке"), R.MarketSide("new"), R.MarketSide("used"), 50.0)
        self.assertEqual(r.verdict, "UNKNOWN")

    def test_used_fee_is_5_percent_for_ram(self):
        used, spec = market([380, 400, 410, 420, 450, 460])
        new, _ = market([540, 550, 560, 580], cond="new")
        r = R.evaluate(spec, new, used, 250.0)
        self.assertEqual(r.fee.rate, 0.05)

    def test_one_price_per_seller_and_low_feedback_ignored(self):
        spec = R.parse_spec("DDR5 32GB 2x16GB UDIMM")
        items = [it("Kingston 32GB Kit (2x16GB) DDR5 UDIMM A", 100, "same"), it("Kingston 32GB Kit (2x16GB) DDR5 UDIMM B", 300, "same"),
                 it("Kingston 32GB Kit (2x16GB) DDR5 UDIMM C", 50, "newbie", fb=2)]
        ms = R.side_stats(items, spec, "used")
        self.assertEqual(ms.sellers, 1)
        self.assertEqual(ms.minimum, 100)                   # новачок із fb=2 не впливає; один продавець = одна ціна


class FakeRam:
    calls = 0

    def __init__(self, new, used):
        self._n, self._u = new, used

    def search_cond(self, q, cond, min_price=15):
        FakeRam.calls += 1
        return self._n if cond == R.NEW_COND else self._u


class RunTests(unittest.TestCase):
    def test_end_to_end(self):
        title = "Kingston 32GB Kit (2x16GB) DDR5-5600 UDIMM"
        new = [it(f"{title} N{i}", p, f"n{i}") for i, p in enumerate([540, 550, 560, 580])]
        used = [it(f"{title} U{i}", p, f"u{i}") for i, p in enumerate([380, 400, 410, 420, 450])]
        res = R.run(FakeRam(new, used), "SK Hynix DDR5 32GB 2x16GB PC5-5600B UDIMM", 75.0)
        self.assertEqual(res.verdict, "CHECK")
        text = R.format_result(res, 75.0)
        self.assertIn("CHECK", text)
        self.assertIn("Перед покупкою", text)
        self.assertIn("Sicher bezahlen", text)


if __name__ == "__main__":
    unittest.main()
