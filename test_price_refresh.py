"""Тести щотижневого оновлення цін RAM (research/price_refresh.py) без мережі: фейковий fetch."""
import json
import os
import sys
import tempfile
import unittest

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import ram_alert
from price_refresh import MIN_SELLERS, key_str, next_ratio, refresh

KEY = ("ddr5", "udimm", False, 32, 2)


def fake_fetch(prices_by_query):
    """Повертає по одному оголошенню на продавця; назва підходить лише під свій тип."""
    titles = {"DDR5 32GB 2x16GB": "Kingston Fury DDR5 32GB 2x16GB 6000", "DDR5 64GB 2x32GB": "Corsair DDR5 64GB 2x32GB 6000",
              "DDR5 SODIMM 32GB": "Samsung DDR5 SODIMM 32GB 5600", "DDR5 SODIMM 16GB": "Samsung DDR5 SODIMM 16GB 5600",
              "DDR4 32GB 2x16GB": "Kingston DDR4 32GB 2x16GB 3200", "DDR4 64GB 2x32GB": "Crucial DDR4 64GB 2x32GB 3200",
              "DDR4 SODIMM 32GB": "Kingston DDR4 SODIMM 32GB 3200", "DDR4 SODIMM 64GB 2x32GB": "Crucial DDR4 SODIMM 64GB 2x32GB"}

    def fetch(q, cond):
        if cond.startswith("2750"):
            return []
        return [{"title": titles[q], "seller": f"s{i}", "total": p} for i, p in enumerate(prices_by_query.get(q, []))]
    return fetch


class TestNextRatio(unittest.TestCase):
    def test_step_limit_up_and_down(self):
        self.assertAlmostEqual(next_ratio(1.0, 100, 150, 20), 1.15)
        self.assertAlmostEqual(next_ratio(1.0, 100, 50, 20), 0.85)
        self.assertAlmostEqual(next_ratio(1.0, 100, 108, 20), 1.08)

    def test_global_bounds(self):
        self.assertAlmostEqual(next_ratio(1.75, 100, 500, 20), 1.8)
        self.assertAlmostEqual(next_ratio(0.62, 100, 10, 20), 0.6)

    def test_too_few_sellers_keeps_ratio(self):
        self.assertEqual(next_ratio(1.07, 100, 200, MIN_SELLERS - 1), 1.07)
        self.assertEqual(next_ratio(1.07, None, 200, 30), 1.07)


class TestRefresh(unittest.TestCase):
    def test_first_run_sets_anchor_without_moving_prices(self):
        data, changes = refresh({}, fake_fetch({"DDR5 32GB 2x16GB": [300 + i for i in range(15)]}), "2026-09-28")
        e = data["types"][key_str(KEY)]
        self.assertEqual(e["anchor_ask"], 307)
        self.assertEqual(e["ratio"], 1.0)
        self.assertEqual(e["p25"], ram_alert.REAL_BASE[KEY]["p25"])
        self.assertEqual(changes, [])

    def test_market_up_10pct_moves_ceiling(self):
        data, _ = refresh({}, fake_fetch({"DDR5 32GB 2x16GB": [300] * 15}), "2026-09-28")
        data, changes = refresh(data, fake_fetch({"DDR5 32GB 2x16GB": [330] * 15}), "2026-10-05")
        e = data["types"][key_str(KEY)]
        self.assertAlmostEqual(e["ratio"], 1.1)
        self.assertEqual(e["p25"], round(299 * 1.1))
        self.assertTrue(any("DDR5 UDIMM 32" in c for c in changes))

    def test_other_types_without_data_unchanged(self):
        data, _ = refresh({}, fake_fetch({}), "2026-09-28")
        for e in data["types"].values():
            self.assertIsNone(e["anchor_ask"])
            self.assertEqual(e["ratio"], 1.0)


class TestAlertReadsFile(unittest.TestCase):
    def test_with_refresh_applies_file(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "ram_alert.py")
            with open(os.path.join(d, "ram_prices.json"), "w", encoding="utf-8") as fh:
                json.dump({"types": {key_str(KEY): {"p25": 350, "med": 390}}}, fh)
            old_file, old_env = ram_alert.__file__, os.environ.pop("RAM_PRICES_OFF")
            try:
                ram_alert.__file__ = fake
                table = ram_alert._with_refresh(ram_alert.REAL_BASE)
            finally:
                ram_alert.__file__ = old_file
                os.environ["RAM_PRICES_OFF"] = old_env
        self.assertEqual(table[KEY]["p25"], 350)
        self.assertEqual(table[KEY]["med"], 390)
        self.assertEqual(ram_alert.REAL_BASE[KEY]["p25"], 299)   # база не змінюється


if __name__ == "__main__":
    unittest.main()
