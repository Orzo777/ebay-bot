"""«продати N» (01.10): шифрування в парі з ledger.gs, ціни й пороги, німецькі тексти. Без мережі."""
import itertools
import os
import sys
import unittest

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import sell

# Той самий вектор, що в tools/test_sell.js (зашифрував Apps Script-код ledger.gs) — сумісність JS ↔ Python
VECTOR = ("ckNGkBdsfkbzaDMDdkUQy2CAcFUVcmcL47hP7XagKCLHDNhFMfmX+ODbR4DI3gCFZpjESsJhH5H5",
          "61231ce607032562185d2078ff7a8ccd", "123:ABC", "n0nce")

TITLES = ["OWC 2x16GB DDR4 SO-DIMM 2666MHz für iMac 2019", "RAM ddr4 32GB für iMac",
          "Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200MHz CL16 CMK32GX4M2E3200C16",
          "Kingston FURY Beast 64GB (2x32GB) DDR5 5600MHz CL36 KF556C36BBEK2-64",
          "G.Skill Trident Z5 RGB 32GB 2x16GB DDR5 6000", "Crucial 32GB DDR5 SO-DIMM 5600 CT32G56C46S5",
          "Sony PS5 Slim Disc 1TB mit Controller", "PS5 Digital Edition", "Xbox Series X 1TB", "Nintendo Switch 2",
          "Nintendo Switch OLED weiß", "Nintendo Switch Lite türkis"]


class CryptoTest(unittest.TestCase):
    def test_js_vector(self):
        self.assertEqual(sell.unseal(*VECTOR), {"row": 3, "title": "OWC 2x16GB DDR4 für iMac", "cost": 45.5})

    def test_roundtrip_and_tamper(self):
        d = {"row": 12, "title": "Kingston Fury 2×16 GB – für iMac", "cost": 123.45}
        blob, mac = sell.seal(d, "tok", "abc")
        self.assertEqual(sell.unseal(blob, mac, "tok", "abc"), d)
        self.assertNotIn("123.45", blob)
        with self.assertRaises(ValueError):
            sell.unseal(blob, mac, "other-token", "abc")
        with self.assertRaises(ValueError):
            sell.unseal(blob[:-4] + "AAAA", mac, "tok", "abc")


class PriceTest(unittest.TestCase):
    def test_invariants(self):
        for title, cost, comp in itertools.product(TITLES, (None, 20, 45, 100, 300, 600), ([], [50], [999], [120, 130])):
            r = sell.identify(title)
            self.assertIsNotNone(r, title)
            pr = sell.plan_price(r, cost, comp)
            self.assertLessEqual(pr["decline"], pr["accept"], (title, cost, comp))
            self.assertLessEqual(pr["accept"], pr["list"], (title, cost, comp))
            if cost:   # жоден автоматичний варіант не продає в мінус
                self.assertGreaterEqual(pr["profit_accept"], min(sell.MIN_PROFIT_ABS, 0.1 * cost) - 0.5, (title, cost, comp))
                self.assertGreaterEqual(sell.net_of(r, pr["decline"]) - cost, -0.01, (title, cost, comp))

    def test_undercuts_cheapest_competitor_but_not_below_quick(self):
        r = sell.identify("Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200MHz")
        self.assertEqual(sell.plan_price(r, 50, [145])["list"], 144)         # медіана 154, конкурент 145 → 144
        self.assertEqual(sell.plan_price(r, 50, [120, 125, 129, 131])["list"], 124)   # третій найдешевший 129 → 128 → 124
        low = sell.plan_price(r, 50, [100])                                   # конкурент нижче «швидкої» ціни
        self.assertGreaterEqual(low["list"], r["quick_sale"] * sell.LOW_SHARE - 5)
        self.assertTrue(low["warn"])

    def test_loss_guard(self):
        r = sell.identify("Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200MHz")
        pr = sell.plan_price(r, 200, [])   # переплатили — ціна вища за ринок, але не в мінус
        self.assertGreater(pr["list"], r["median_sale"])
        self.assertIn("в мінус", " ".join(pr["warn"]))

    def test_nice(self):
        self.assertEqual([sell.nice(x) for x in (128, 130, 134, 135.9, 27.5)], [124, 129, 134, 134, 27])


class TextTest(unittest.TestCase):
    def test_titles_short_and_clean(self):
        for t in TITLES:
            r = sell.identify(t)
            tx = sell.ram_texts(t, r) if r["kind"] == "ram" else sell.console_texts(t, r)
            self.assertLessEqual(len(tx["title"]), 80, t)
            self.assertNotRegex(tx["title"], r"невідом|сумнівн|x16GB MHz|  ", t)
            self.assertNotRegex(tx["desc"], r"[а-яіїє]", t)   # опис — лише німецькою

    def test_ram_specs(self):
        r = sell.identify(TITLES[0])
        tx = sell.ram_texts(TITLES[0], r)
        self.assertEqual(tx["title"], "OWC 32GB (2x16GB) DDR4-2666 SO-DIMM Laptop RAM für iMac 2019 – getestet")
        self.assertIn("PC4-21300", tx["desc"])
        r = sell.identify(TITLES[2])
        self.assertIn("Teilenummer: CMK32GX4M2E3200C16", sell.ram_texts(TITLES[2], r)["desc"])

    def test_console_names(self):
        r = sell.identify("Sony PS5 Slim Disc 1TB mit Controller")
        self.assertTrue(sell.console_texts("Sony PS5 Slim Disc 1TB mit Controller", r)["title"]
                        .startswith("Sony PlayStation 5 Slim Disc Edition 1TB Konsole + Controller"))
        r = sell.identify("PS5 Digital Edition")
        self.assertIn("Digital Edition", sell.console_texts("PS5 Digital Edition", r)["title"])


class MakeTest(unittest.TestCase):
    def test_unknown_item(self):
        text, kb = sell.make("Lampe", 5, 4, lambda r: [])
        self.assertIn("Не впізнав", text)
        self.assertIsNone(kb)

    def test_single_module_warning_and_card(self):
        text, kb = sell.make("RAM ddr4 32GB für iMac", 45, 3, lambda r: [])
        self.assertIn("ОДНУ на 32 ГБ", text)
        self.assertIn("продати 3", text)
        self.assertEqual(kb["inline_keyboard"][0][0]["copy_text"]["text"], "32GB DDR4 SO-DIMM Laptop RAM für iMac – getestet")

    def test_competitor_api_failure_still_gives_card(self):
        def boom(r):
            raise RuntimeError("429")
        text, _ = sell.make("Xbox Series X 1TB", 380, 5, boom)
        self.assertIn("схожих не знайшов", text)

    def test_competitor_filter(self):
        import main
        items = [{"title": "Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200", "price": {"value": "129"},
                  "shippingOptions": [{"shippingCost": {"value": "0"}}], "seller": {"feedbackScore": 12}},
                 {"title": "Corsair Vengeance 32GB (2x16GB) DDR4 3200", "price": {"value": "99"},
                  "seller": {"feedbackScore": 0}},                                                # 0 відгуків
                 {"title": "Corsair 16GB (2x8GB) DDR4 3200", "price": {"value": "60"}, "seller": {"feedbackScore": 5}},          # інша ємність
                 {"title": "DDR4 32GB 2x16 defekt", "price": {"value": "40"}, "seller": {"feedbackScore": 5}},                   # дефект
                 {"title": "G.Skill 32GB (2x16GB) DDR4 3600", "price": {"value": "140"},
                  "shippingOptions": [{"shippingCost": {"value": "4.99"}}], "seller": {"feedbackScore": 300}}]
        old = main._request_with_backoff
        main._request_with_backoff = lambda *a, **k: {"itemSummaries": items}
        try:
            r = sell.identify("Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200MHz")
            self.assertEqual(sell.competitors(r, client=type("C", (), {"_headers": lambda self: {}})()), [129.0, 144.99])
        finally:
            main._request_with_backoff = old


if __name__ == "__main__":
    unittest.main()
