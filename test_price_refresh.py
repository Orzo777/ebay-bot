"""Сторож ринкових цін (research/price_refresh.py) без мережі: фейковий fetch. З 01.10 — щодня, RAM і консолі,
вниз до −10% за раз, вгору до +5%, згладжування медіаною 3 замірів, сповіщення, місячний замір нових категорій."""
import json
import os
import sys
import tempfile
import unittest

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import console_alert
import price_refresh
import ram_alert
from price_refresh import MIN_SELLERS, key_str, next_ratio, refresh, scan

KEY = ("ddr5", "udimm", False, 32, 2)


def fake_fetch(prices_by_query):
    """Повертає по одному оголошенню на продавця; назва підходить лише під свій тип."""
    titles = {"DDR5 32GB 2x16GB": "Kingston Fury DDR5 32GB 2x16GB 6000", "DDR5 64GB 2x32GB": "Corsair DDR5 64GB 2x32GB 6000",
              "DDR5 SODIMM 32GB": "Samsung DDR5 SODIMM 32GB 5600", "DDR5 SODIMM 16GB": "Samsung DDR5 SODIMM 16GB 5600",
              "DDR4 32GB 2x16GB": "Kingston DDR4 32GB 2x16GB 3200", "DDR4 64GB 2x32GB": "Crucial DDR4 64GB 2x32GB 3200",
              "DDR4 SODIMM 32GB": "Kingston DDR4 SODIMM 32GB 3200", "DDR4 SODIMM 64GB 2x32GB": "Crucial DDR4 SODIMM 64GB 2x32GB",
              "DDR5 16GB": "Kingston FURY Beast DDR5 16GB 5200 DIMM", "DDR5 SODIMM 32GB 2x16GB": "Crucial DDR5 SODIMM 32GB 2x16GB 5600"}

    def fetch(q, cond, cat=None):
        if cond.startswith("2750") or q not in titles:
            return []
        return [{"title": titles[q], "seller": f"s{i}", "total": p} for i, p in enumerate(prices_by_query.get(q, []))]
    return fetch


def days(start, n):
    from datetime import date, timedelta
    d = date.fromisoformat(start)
    return [(d + timedelta(days=i)).isoformat() for i in range(n)]


class TestNextRatio(unittest.TestCase):
    def test_step_limit_up_and_down(self):
        self.assertAlmostEqual(next_ratio(1.0, 100, 150, 20), 1.05)    # вгору обережно
        self.assertAlmostEqual(next_ratio(1.0, 100, 50, 20), 0.90)     # вниз швидко
        self.assertAlmostEqual(next_ratio(1.0, 100, 104, 20), 1.04)
        self.assertAlmostEqual(next_ratio(1.0, 100, 93, 20), 0.93)

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

    def test_market_up_10pct_moves_ceiling_gradually(self):
        data, _ = refresh({}, fake_fetch({"DDR5 32GB 2x16GB": [300] * 15}), "2026-09-28")
        changes = []
        for d in days("2026-09-29", 7):
            data, c = refresh(data, fake_fetch({"DDR5 32GB 2x16GB": [330] * 15}), d)
            changes += c
        e = data["types"][key_str(KEY)]
        self.assertAlmostEqual(e["ratio"], 1.1)                       # згладжено і кроками ≤5% — за кілька днів
        self.assertEqual(e["p25"], round(ram_alert.REAL_BASE[KEY]["p25"] * 1.1))
        self.assertTrue(any("📈 DDR5 UDIMM 32" in c for c in changes))

    def test_drop_tightens_fast_and_alerts_once(self):
        data, _ = refresh({}, fake_fetch({"DDR5 32GB 2x16GB": [300] * 15}), "2026-09-28")
        changes = []
        for d in days("2026-09-29", 5):
            data, c = refresh(data, fake_fetch({"DDR5 32GB 2x16GB": [255] * 15}), d)
            changes += c
        e = data["types"][key_str(KEY)]
        self.assertAlmostEqual(e["ratio"], 0.85)                      # −15% уже за 2–3 дні
        drops = [c for c in changes if c.startswith("📉 DDR5 UDIMM 32")]
        self.assertEqual(len(drops), 1)                               # один зсув — одне сповіщення
        self.assertIn("стеля купівлі", drops[0])

    def test_one_day_spike_smoothed(self):
        data, _ = refresh({}, fake_fetch({"DDR5 32GB 2x16GB": [300] * 15}), "2026-09-28")
        data, _ = refresh(data, fake_fetch({"DDR5 32GB 2x16GB": [300] * 15}), "2026-09-29")
        data, _ = refresh(data, fake_fetch({"DDR5 32GB 2x16GB": [200] * 15}), "2026-09-30")   # один дивний день
        self.assertAlmostEqual(data["types"][key_str(KEY)]["ratio"], 1.0)

    def test_consoles_refreshed(self):
        def fetch(q, cond, cat=None):
            if q == "ps5 konsole":
                return [{"title": "Sony PS5 Slim Disc 1TB Konsole", "seller": f"s{i}", "total": 500} for i in range(10)] + \
                       [{"title": "PS5 Controller DualSense", "seller": "x", "total": 50}]
            return []
        data, _ = refresh({}, fetch, "2026-09-28")
        e = data["consoles"]["PS5_DISC"]
        self.assertEqual((e["anchor_ask"], e["sellers"]), (500, 10))   # контролер не рахується
        for d in days("2026-09-29", 3):
            data, _ = refresh(data, lambda q, c, cat=None: [dict(it, total=450) for it in fetch(q, c)], d)
        self.assertLess(data["consoles"]["PS5_DISC"]["p25"], console_alert.CONSOLE_BASE["PS5_DISC"][0])

    def test_scan_monthly_finds_rising_category(self):
        def mk(price):
            def fetch(q, cond, cat=None):
                if q != "DDR4 16GB 2x8GB" or cond.startswith("2750"):
                    return []
                return [{"title": "Kingston DDR4 16GB 2x8GB 3200", "seller": f"s{i}", "total": price} for i in range(15)]
            return fetch
        data = {}
        self.assertEqual(scan(data, mk(40), "2026-10-01"), [])          # перший замір — база
        self.assertEqual(scan(data, mk(60), "2026-10-15"), [])          # ще не місяць
        out = scan(data, mk(50), "2026-10-30")
        self.assertIn("DDR4 DIMM 16 ГБ (2×8): оголошення 40 → 50 € (+25%", "\n".join(out))

    def test_every_type_has_a_query(self):
        from price_refresh import CANDIDATES, CONSOLE_QUERIES, QUERIES
        self.assertEqual(set(QUERIES), set(ram_alert.REAL_BASE))
        self.assertEqual(set(CONSOLE_QUERIES), set(console_alert.CONSOLE_TYPES))
        self.assertFalse(set(CANDIDATES) & set(ram_alert.REAL_BASE))

    def test_new_terapeak_base_resets_anchor(self):
        data, _ = refresh({}, fake_fetch({"DDR5 32GB 2x16GB": [300] * 15}), "2026-09-28")
        data, _ = refresh(data, fake_fetch({"DDR5 32GB 2x16GB": [330] * 15}), "2026-10-05")
        e = data["types"][key_str(KEY)]
        e["p25_tp"], e["med_tp"] = 1, 2          # ніби в JSON лишився старий знімок Terapeak
        data, _ = refresh(data, fake_fetch({"DDR5 32GB 2x16GB": [400] * 15}), "2026-10-12")
        e = data["types"][key_str(KEY)]
        self.assertEqual(e["ratio"], 1.0)
        self.assertEqual(e["anchor_ask"], 400)
        self.assertEqual(e["p25"], ram_alert.REAL_BASE[KEY]["p25"])

    def test_other_types_without_data_unchanged(self):
        data, _ = refresh({}, fake_fetch({}), "2026-09-28")
        for e in data["types"].values():
            self.assertIsNone(e["anchor_ask"])
            self.assertEqual(e["ratio"], 1.0)


class TestAlertReadsFile(unittest.TestCase):
    def test_stale_anchor_ignored_after_new_terapeak(self):
        # 26.09: REAL_BASE оновлено новим знімком, а файл ще рахує поправку від старого — брати свіжий знімок
        base = ram_alert.REAL_BASE[KEY]
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "ram_prices.json"), "w", encoding="utf-8") as fh:
                json.dump({"types": {key_str(KEY): {"p25_tp": base["p25"] - 50, "med_tp": base["med"], "p25": 1, "med": 2}}}, fh)
            old_file, old_env = ram_alert.__file__, os.environ.pop("RAM_PRICES_OFF")
            try:
                ram_alert.__file__ = os.path.join(d, "ram_alert.py")
                table = ram_alert._with_refresh(ram_alert.REAL_BASE)
            finally:
                ram_alert.__file__ = old_file
                os.environ["RAM_PRICES_OFF"] = old_env
        self.assertEqual(table[KEY]["p25"], base["p25"])

    def test_with_refresh_applies_file(self):
        base_p25 = ram_alert.REAL_BASE[KEY]["p25"]
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
        self.assertEqual(ram_alert.REAL_BASE[KEY]["p25"], base_p25)   # база не змінюється


if __name__ == "__main__":
    unittest.main()


class TestConsoleFile(unittest.TestCase):
    def test_console_prices_from_file_in_place(self):
        t = console_alert.SWITCH2
        old = (t["p25"], t["med"])
        base = console_alert.CONSOLE_BASE["SWITCH2"]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"consoles": {"SWITCH2": {"p25_tp": base[0], "med_tp": base[1], "p25": 300, "med": 320},
                                        "PS5_DISC": {"p25_tp": 1, "med_tp": 2, "p25": 10, "med": 20}}}, fh)
            old_env = os.environ.pop("RAM_PRICES_OFF")
            try:
                console_alert._apply_refresh(path)
                self.assertEqual((t["p25"], t["med"]), (300, 320))
                self.assertEqual(console_alert.evaluate_console("Nintendo Switch 2 Konsole", 200)["quick_sale"], 300)
                self.assertEqual(console_alert.PS5_DISC["p25"], console_alert.CONSOLE_BASE["PS5_DISC"][0])   # старий знімок
            finally:
                t["p25"], t["med"] = old
                os.environ["RAM_PRICES_OFF"] = old_env


class TestDailyDispatch(unittest.TestCase):
    def test_once_a_day_after_5_utc(self):
        from datetime import datetime, timezone
        import ebay_watch
        calls, st = [], {}
        disp = lambda wf: calls.append(wf) or 204
        early = datetime(2026, 10, 2, 4, 30, tzinfo=timezone.utc)
        self.assertFalse(ebay_watch.price_refresh_check(st, early, lambda: "2026-10-01", disp))
        now = datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)
        self.assertTrue(ebay_watch.price_refresh_check(st, now, lambda: "2026-10-01", disp))
        self.assertFalse(ebay_watch.price_refresh_check(st, now, lambda: "2026-10-01", disp))   # уже запускав
        self.assertFalse(ebay_watch.price_refresh_check({}, now, lambda: "2026-10-02", disp))   # уже оновлено
        self.assertEqual(calls, ["price_refresh.yml"])
