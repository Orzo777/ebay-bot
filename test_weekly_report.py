"""Тижневий звіт (01.10): гроші, точність прогнозу, залежаний товар, підписки. Без мережі."""
import os
import sys
import unittest
from datetime import date, datetime, timezone

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import health
import weekly_report as wr

TODAY = date(2026, 10, 5)


def row(n, title, status, spent, d, sdate="", sprice=None, profit=None, cat="RAM"):
    return dict(n=n, title=title, status=status, spent=spent, date=d, sdate=sdate, sprice=sprice, profit=profit, cat=cat,
                src="eBay", net=None)


ROWS = [row(1, "OWC 2x16GB DDR4 SO-DIMM 2666", "Продано", 50, "2026-09-25", "2026-10-02", 100, 40),
        row(2, "Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200", "Продано", 70, "2026-09-10", "2026-09-20", 120, 45),
        row(3, "Kingston FURY 32GB (2x16GB) DDR4 3200", "Продано", 60, "2026-09-01", "2026-09-12", 118, 50),
        row(4, "Sony PS5 Slim Disc 1TB", "Отримано", 300, "2026-09-28", cat="Консоль"),           # 7 дн., не виставлено
        row(5, "Xbox Series X 1TB", "Виставлено", 380, "2026-09-20", cat="Консоль"),              # 15 дн. на продажу
        row(6, "Nintendo Switch OLED", "В дорозі", 110, "2026-09-22", cat="Консоль"),             # 13 дн. в дорозі
        row(7, "Crucial 32GB DDR5", "Скасовано", 90, "2026-10-01")]


class WeeklyTest(unittest.TestCase):
    def test_money(self):
        t = "\n".join(wr.money(ROWS, TODAY))
        self.assertIn("За тиждень продано: 1 шт на 100 €, прибуток <b>40 €</b>", t)
        self.assertIn("Усього продано: 3 шт, прибуток <b>135 €</b>", t)
        self.assertIn("На руках: 3 шт, у них 790 €", t)

    def test_accuracy_flags_optimistic_ceilings(self):
        lines, mean = wr.accuracy(ROWS, TODAY)
        t = "\n".join(lines)
        self.assertLess(mean, 0.95)          # 100/108, 120/134, 118/134
        self.assertIn("стелі купівлі завищені", t)
        self.assertIn("№1 OWC", t)
        self.assertIn("RAM: 3 шт", t)

    def test_accuracy_without_sales(self):
        lines, mean = wr.accuracy([r for r in ROWS if r["status"] != "Продано"], TODAY)
        self.assertIsNone(mean)
        self.assertIn("Продажів ще не було", "\n".join(lines))

    def test_stale_actions(self):
        calls = []
        t = "\n".join(wr.stale(ROWS, TODAY, lambda r: calls.append(r) or [520, 530, 540]))
        self.assertIn("№4 Sony PS5 Slim Disc 1TB — 7 дн. на руках і ще не виставлено → «продати 4»", t)
        # третій найдешевший 540 → 539 разом; 10.10: ціна товару без доставки 10,49 → 529
        self.assertIn("№5 Xbox Series X 1TB — 15 дн. і не продано → постав <b>529 €</b> + доставка 10,49 €", t)
        self.assertIn("№6 Nintendo Switch OLED — куплено 13 дн. тому, досі «В дорозі»", t)
        self.assertNotIn("№7", t)
        self.assertEqual(len(calls), 1)

    def test_stale_market_failure_still_advises(self):
        def boom(r):
            raise RuntimeError("429")
        self.assertIn("№5 Xbox", "\n".join(wr.stale(ROWS, TODAY, boom)))

    def test_search_and_subscriptions(self):
        ka, ebay = {}, {}
        now = datetime(2026, 10, 5, 8, tzinfo=timezone.utc)
        health.bump(ka, "ka_mails", 300, now)
        health.bump(ka, "ka_cards", 7, now)
        health.bump(ka, "sub_mails|ddr5 32gb", 120, now)
        health.bump(ka, "sub_cards|ddr5 32gb", 5, now)
        health.bump(ka, "sub_mails|switch lite", 80, now)
        health.bump(ebay, "ebay_cards", 4, now)
        t = "\n".join(wr.search(ebay, ka, ROWS, TODAY))
        self.assertIn("листів 300 → карток 7", t)
        self.assertIn("ddr5 32gb — 5 з 120 листів", t)
        self.assertIn("Без жодної картки: switch lite (80 листів)", t)

    def test_build_and_chunks(self):
        text = wr.build(ROWS, TODAY, {}, {}, lambda r: [])
        self.assertTrue(text.startswith("📈 <b>Тижневий звіт</b> · 29.09–05.10"))
        parts = wr.chunks(text + "\n\n" + "\n\n".join(["x" * 1000] * 6))
        self.assertTrue(all(len(p) <= 3900 for p in parts))
        self.assertGreater(len(parts), 1)


if __name__ == "__main__":
    unittest.main()


class TaxLinesTest(unittest.TestCase):
    def test_counter_and_warning(self):
        from datetime import date
        import weekly_report as wr
        sold = [{"sdate": "2026-03-01", "sprice": 100.0, "profit": 30.0, "status": "Продано"} for _ in range(10)]
        sold.append({"sdate": "2025-12-30", "sprice": 999.0, "profit": 500.0, "status": "Продано"})   # минулий рік — не рахуємо
        lines = wr.tax_lines(sold, date(2026, 10, 9))
        self.assertIn("продажів 10/30", lines[0])
        self.assertEqual(len(lines), 1)
        many = sold + [{"sdate": "2026-09-01", "sprice": 100.0, "profit": 10.0, "status": "Продано"} for _ in range(15)]
        self.assertIn("Steuerberater", "\n".join(wr.tax_lines(many, date(2026, 10, 9))))   # 25/30 → попередження


class MonthLinesTest(unittest.TestCase):
    def test_month(self):
        from datetime import date
        import weekly_report as wr
        sold = [{"sdate": "2026-10-05", "date": "2026-09-30", "sprice": 164.0, "profit": 75.0, "cat": "RAM", "status": "Продано"},
                {"sdate": "2026-10-01", "date": "2026-09-21", "sprice": 420.0, "profit": 95.0, "cat": "Консоль", "status": "Продано"},
                {"sdate": "2026-08-01", "date": "2026-07-21", "sprice": 99.0, "profit": 9.0, "cat": "RAM", "status": "Продано"}]
        lines = wr.month_lines(sold, date(2026, 10, 9))
        self.assertIn("продано 2 шт", lines[0])
        self.assertIn("лежав у середньому 8 дн.", lines[0])
        self.assertTrue(lines[1].strip().startswith("по категоріях: Консоль"))
        self.assertEqual(wr.month_lines([], date(2026, 10, 9)), [])



class AuctionsTest(unittest.TestCase):
    def test_auction_line(self):
        from datetime import date
        import weekly_report as wr
        ebay = {"auction_results": [{"d": "2026-10-08", "final": 150, "max": 190}, {"d": "2026-10-07", "final": 230, "max": 190},
                                    {"d": "2026-10-06", "final": 200, "max": 160}, {"d": "2026-09-01", "final": 10, "max": 100}]}
        line = wr.auctions(ebay, date(2026, 10, 9))[0]
        self.assertIn("3 з картками, у 1 фінал", line)
        self.assertIn("дорожче на 25%", line)   # медіана з 21% і 25%
        self.assertEqual(wr.auctions({}, date(2026, 10, 9)), [])



class ButtonsTest(unittest.TestCase):
    """10.10: «⬇️ Знизити» (лише виставлене і лише з підключеним eBay) і «✅ Межі оновив», якщо межі KA відстали."""

    def test_cut_and_bounds_buttons(self):
        import weekly_report as wr
        kb = []
        text = wr.build(ROWS, TODAY, {}, {}, lambda r: [520, 530, 540], kb=kb, ebay_on=True,
                        bounds_fn=lambda: ["🔎 <b>Межі підписок KA</b>", "• ddr5 2x16gb: 276 → <b>300 €</b>"])
        datas = [b["callback_data"] for row in kb for b in row]
        self.assertIn("c|межі|1", datas)
        self.assertIn("Межі підписок KA", text)
        cuts = [d for d in datas if d.startswith("p|")]
        self.assertTrue(cuts)
        for d in cuts:
            n = int(d.split("|")[1])
            self.assertEqual(next(r for r in ROWS if r["n"] == n)["status"], "Виставлено")
        kb2 = []
        wr.build(ROWS, TODAY, {}, {}, lambda r: [520, 530, 540], kb=kb2, ebay_on=False, bounds_fn=lambda: [])
        self.assertEqual(kb2, [])


class KaBoundsTest(unittest.TestCase):
    def test_drift_and_accept(self):
        import json
        import tempfile
        import ka_bounds as kb
        rec = {"ram": {"ddr5 2x16gb": 300, "ddr4 2x16gb": 95}, "console": {"ps5": 347}, "hamburg": {"xbox": 500}}
        cur = {"ram": {"ddr5 2x16gb": 276, "ddr4 2x16gb": 94}, "console": {"ps5": 347}, "hamburg": {"xbox": 457}}
        self.assertEqual(kb.drift(rec, cur), [("ram", "ddr5 2x16gb", 276, 300), ("hamburg", "xbox", 457, 500)])
        ls = kb.lines(rec, cur)
        self.assertIn("ddr5 2x16gb: 276 → <b>300 €</b>", "\n".join(ls))
        self.assertIn("xbox series x / xbox series in Hamburg: 457 → <b>500 €</b>", "\n".join(ls))
        self.assertEqual(kb.lines(cur, cur), [])
        old = kb.PATH
        with tempfile.TemporaryDirectory() as d:
            kb.PATH = d + "/b.json"
            try:
                kb.accept(rec)
                with open(kb.PATH, encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["hamburg"]["xbox"], 500)
            finally:
                kb.PATH = old

    def test_show_text(self):
        import ka_bounds as kb
        rec = {"ram": {"ddr5 2x16gb": 300, "ddr4 2x16gb": 95}, "console": {}, "hamburg": {}}
        text, markup = kb.show_text(rec, {"ram": {"ddr5 2x16gb": 276, "ddr4 2x16gb": 94}, "console": {}, "hamburg": {}})
        self.assertIn("ddr5 2x16gb: 276 → <b>300 €</b>", text)
        self.assertIn("ddr4 2x16gb: 94 € ✓", text)   # як зараз у KA (розбіжність < 5%)
        self.assertEqual(markup["inline_keyboard"][0][0]["callback_data"], "c|межі|1")
        self.assertIsNone(kb.show_text(rec, rec)[1])

    def test_recommended_matches_code_bounds_with_static_prices(self):
        # статичні ціни (RAM_PRICES_OFF): рекомендовані межі консолей і Гамбурга = значенням у коді (порахованим 09.10)
        import ka_bounds as kb
        rec = kb.recommended()
        self.assertEqual(rec["console"]["xbox series x"], 432)
        self.assertEqual(rec["console"]["ps5"], 347)
        self.assertEqual(rec["hamburg"], {"ddr5": 462, "ddr4": 223, "xbox": 457, "ps5": 368, "switch2": 311})
