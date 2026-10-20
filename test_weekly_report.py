"""Тижневий звіт (01.10): гроші, точність прогнозу, залежаний товар, підписки. Без мережі."""
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

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
        self.assertIn("Тиждень: продано 1 на 100 € → <b>+40 €</b> · куплено 1 на 90 €", t)   # 14.10: коротко
        self.assertIn("Усього: продано 3 → <b>+135 €</b> (+75% на вкладене)", t)
        full = wr.build(ROWS, TODAY, {}, {}, lambda r: [], bounds_fn=lambda: [], shortage_fn=lambda: [], services_fn=lambda: [])
        self.assertIn("на руках 3 шт на 790 €", full)                                  # у «Головне»
        self.assertIn("💰 <b>Гроші</b>\n<blockquote>", full)                            # пункти — у цитаті
        self.assertIn("🎯 <b>Точність прогнозу</b> (продане за 90 днів)\n<blockquote expandable>", full)   # деталі — згорнуті

    def test_accuracy_flags_optimistic_ceilings(self):
        lines, mean = wr.accuracy(ROWS, TODAY)
        t = "\n".join(lines)
        self.assertLess(mean, 0.93)          # 10.10: від медіани (OWC SO-DIMM, фірмові DDR4 2×16)
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
        # 20.10: доставка лотів різна — ціна «разом» і обидва варіанти; кнопка «⬇️» — у щоденній перевірці цін
        self.assertIn("№5 Xbox Series X 1TB — 15 дн. і не продано → постав <b>539 €</b> разом з доставкою "
                      "(з безкоштовною — ціна 539 €, з платною — 529 € + 10,49 €)", t)
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



class StaleSheetTest(unittest.TestCase):
    """17.10: товар з порадою — аркуш з фото (номери з stale)."""

    def test_stale_numbers_and_sheet(self):
        from unittest import mock
        import onhand
        today = date(2026, 10, 20)
        rows = [{"n": 1, "title": "Corsair Vengeance LPX 32GB (2x16GB) DDR4-2133", "status": "Перевірено", "date": "2026-10-01", "spent": 96},
                {"n": 2, "title": "Kingston 16GB DDR4", "status": "Отримано", "date": "2026-10-19", "spent": 20}]
        ns = []
        wr.stale(rows, today, market=lambda r: [], ns=ns)
        self.assertEqual(ns, [(1, 19)])                                # лише те, де є порада
        got = {}
        with mock.patch.object(onhand, "build", side_effect=lambda d, *a: got.update(d) or b"jpg"):
            self.assertEqual(wr.stale_sheet({"rows": rows, "photos": {"1": ["f1"]}}, ns), b"jpg")
        self.assertEqual(got["groups"][0]["items"], [{"n": 1, "title": rows[0]["title"], "days": 19, "photos": ["f1"]}])
        self.assertIsNone(wr.stale_sheet({"rows": rows}, []))



class CapitalFunnelTest(unittest.TestCase):
    """18.10 (кроки 2–3 плану): оборот капіталу, ціль 30.11, воронка карток."""

    def test_capital_and_goal(self):
        today = date(2026, 10, 26)
        rows = [{"n": 1, "title": "A", "status": "Продано", "date": "2026-10-01", "sdate": "2026-10-15", "spent": 150, "profit": 40},
                {"n": 2, "title": "B", "status": "Продано", "date": "2026-10-05", "sdate": "2026-10-25", "spent": 170, "profit": 30},
                {"n": 3, "title": "C", "status": "Виставлено", "date": "2026-10-02", "spent": 160, "profit": None},
                {"n": 4, "title": "D", "status": "Отримано", "date": "2026-10-20", "spent": 140, "profit": None}]
        text = "\n".join(wr.capital(rows, today))
        self.assertIn("У товарі зараз: <b>300", text)
        self.assertIn("23%</b> на гроші в товарі", text)            # 70 € за 30 днів / 300 € у товарі
        self.assertIn("<b>17 дн.</b> (2 продажів", text)             # (14 + 20) / 2
        self.assertIn("№3", text)                                     # лежить 24 дн. ≥ 21
        self.assertNotIn("№4", text)
        self.assertIn("продажів 2/15", text)
        self.assertIn("✅ 35 €/шт", text)
        self.assertIn("✅ цикл 17 дн.", text)
        self.assertNotIn("Ціль до", "\n".join(wr.capital(rows, date(2026, 12, 20))))   # після цілі — не показуємо

    def test_funnel(self):
        bought = [{"src": "Kleinanzeigen"}]
        lines = wr.funnel({"ebay_v_buy": 1, "ebay_v_neg": 4}, {"ka_v_buy": 2, "ka_v_neg": 3}, bought)
        self.assertIn("«бери» 3 + «торгуйся» 7 → куплено <b>1</b> (10%)", lines[0])
        self.assertIn("хтось купує швидше", wr.funnel({"ebay_v_buy": 3}, {}, [])[1])
        self.assertEqual(wr.funnel({}, {}, []), [])



class OwnPricesTest(unittest.TestCase):
    """18.10 (крок 1): ціни з наших продажів — поправка для типу з ≥5 продажами, менше — лише лічильник."""

    def test_factor_after_five_sales(self):
        import tempfile
        import ram_alert
        import sell
        title = "Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200"
        med = sell.identify(title)["median_sale"]
        rows = [{"n": i, "title": title, "status": "Продано", "date": "2026-10-01", "sdate": "2026-10-15", "sprice": round(med * 0.9, 2),
                 "spent": 120, "profit": 30} for i in range(5)]
        factors, lines = wr.own_prices(rows, date(2026, 10, 20))
        self.assertEqual(list(factors.values()), [0.9])
        self.assertEqual(ram_alert.key_of(sell.identify(title)["type"]), ("ddr4", "udimm", False, 32, 2))
        self.assertIn("бот рахує ×0.90", "\n".join(lines))
        few, lines = wr.own_prices(rows[:3], date(2026, 10, 20))
        self.assertEqual(few, {})
        self.assertIn("3/5 продажів", "\n".join(lines))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "own.json")
            self.assertTrue(wr.save_own_factors(factors, path))
            self.assertFalse(wr.save_own_factors(factors, path))        # без змін — не комітимо

    def test_factor_applied_to_prices(self):
        import ram_alert
        key = ("ddr4", "udimm", False, 32, 2)
        t = ram_alert._with_own({key: dict(p25=100, med=200)})
        self.assertEqual(t[key]["med"], 200)                            # файл порожній — без змін
        from unittest import mock
        with mock.patch.object(ram_alert, "own_factors", return_value={"ddr4|udimm|0|32|2": 0.5}),                 mock.patch.dict(os.environ, {"RAM_PRICES_OFF": "0"}):
            t = ram_alert._with_own({key: dict(p25=100, med=200)})
        self.assertEqual((t[key]["p25"], t[key]["med"], t[key]["own"]), (85, 170, 0.85))   # не нижче −15%


class MarketTrendTest(unittest.TestCase):
    """18.10: тренд найдешевших пропозицій за ~2 тижні; ринок дешевшає ≥8% — попередження і товар на руках цього типу."""

    def prices(self, start, end):
        days = [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(18)]
        step = (end - start) / 17
        return {"types": {"ddr4|udimm|0|32|2": {"name": "DDR4 UDIMM 32 ГБ (2×16) кіт",
                                                "ask_hist": [[d, round(start + i * step, 2)] for i, d in enumerate(days)]},
                          "ddr5|udimm|0|32|2": {"name": "DDR5 UDIMM 32 ГБ (2×16) кіт",
                                                "ask_hist": [[d, round(start * 1.5 + i * step * 1.5, 2)] for i, d in enumerate(days)]}}}

    def test_falling_market_warns_and_flags_stock(self):
        rows = [{"n": 4, "title": "Corsair Vengeance LPX 32GB (2x16GB) DDR4-2133", "status": "Виставлено"},
                {"n": 9, "title": "Corsair Vengeance LPX 32GB (2x16GB) DDR4-2133", "status": "Продано"}]
        lines, warn = wr.market_trend(rows, date(2026, 10, 18), self.prices(220, 190))
        text = "\n".join(lines)
        self.assertTrue(warn)
        self.assertIn("Ринок пам'яті дешевшає", text)
        self.assertIn("№4 (", text)
        self.assertNotIn("№9", text)                                   # продане — ні

    def test_stable_and_too_little_data(self):
        lines, warn = wr.market_trend([], date(2026, 10, 18), self.prices(200, 202))
        self.assertFalse(warn)
        self.assertIn("стабільно", lines[0])
        lines, warn = wr.market_trend([], date(2026, 10, 18), {"types": {"x": {"name": "X", "ask_hist": [["2026-10-17", 1]]}}})
        self.assertIn("замало замірів", lines[0])


class DocsMissingTest(unittest.TestCase):
    def test_pickup_without_receipt(self):
        # 18.10: самовивіз за готівку без «чек N» — нагадати; eBay / KA (є лист) і з документом — ні
        rows = [{"n": 9, "src": "Самовивіз Гамбург", "date": "2026-10-18", "status": "Виставлено"},
                {"n": 10, "src": "Самовивіз Гамбург", "date": "2026-10-18", "status": "Отримано", "doc": True},
                {"n": 11, "src": "eBay", "date": "2026-10-18", "status": "Отримано"}]
        self.assertEqual(wr.docs_missing(rows, date(2026, 10, 20)),
                         ["🧾 Без документа покупки (самовивіз): №9 — фото чека / переписки з підписом «чек N»"])
        self.assertEqual(wr.docs_missing(rows[1:], date(2026, 10, 20)), [])


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
        self.assertIn("продано 2 → <b>+", lines[0])
        self.assertIn("лежав у середньому 8 дн.", lines[0])
        self.assertIn("· Консоль +95 €, RAM +75 €", lines[0])   # 14.10: категорії — у тому ж рядку
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
        self.assertEqual([d for d in datas if d.startswith("p|")], [])   # 20.10: «⬇️» — у щоденній перевірці (там видно доставку)
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
        import ram_mail_check as rmc   # 10.10: межі в коді = рекомендованим за базою Terapeak
        self.assertEqual(rec["console"]["xbox series x"], rmc.CONSOLE_MAX["xbox series x"])
        self.assertEqual(rec["console"]["ps5"], rmc.CONSOLE_MAX["ps5"])
        self.assertEqual(rec["hamburg"], rmc.HAMBURG_MAX)



class HeadlineTest(unittest.TestCase):
    def test_headline_on_top(self):
        import weekly_report as wr
        text = wr.build(ROWS, TODAY, {}, {}, lambda r: [520, 530, 540], bounds_fn=lambda: ["🔎 x", "• ddr5 2x16gb: 276 → <b>300 €</b>"])
        head = text.split("\n\n")[1]
        self.assertTrue(head.startswith("⭐ <b>Головне</b>"))
        self.assertIn("• Тиждень: продано", head)
        self.assertIn("• Зробити:", head)
        self.assertIn("змінити межі в 1", head)
        self.assertLess(text.index("Що зробити з товаром"), text.index("💰 <b>Гроші</b>"))   # дії — одразу під головним



class ShortageTest(unittest.TestCase):
    """10.10: сторож дефіциту — замір раз на 6 днів, стрибок ≥15% за 10–24 дні → рядок у тижневому звіті."""

    def test_measure_filters_and_update(self):
        import shortage_watch as sw
        rows = [{"title": f"Sony PS5 Pro Konsole 2TB #{i}", "total": 900.0 + 20 * i} for i in range(8)] + \
               [{"title": "PS5 Pro Hülle", "total": 30.0}, {"title": "PS5 Slim", "total": 450.0}, {"title": "PS5 Pro defekt", "total": 500.0}]
        m = sw.measure(lambda q, c, cat: rows, sw.WATCH[0])
        self.assertEqual(m, {"p25": 940, "med": 970, "n": 8})
        data, calls = {}, []
        fetch = lambda q, c, cat: (calls.append(q), rows)[1]   # noqa: E731
        sw.update(data, fetch, "2026-10-10")
        self.assertEqual(data["shortage"]["hist"]["PS5 Pro"], [["2026-10-10", 940, 970, 8]])
        n = len(calls)
        self.assertEqual(sw.update(data, fetch, "2026-10-14"), 0)   # раніше ніж за 6 днів — без запитів
        self.assertEqual(len(calls), n)

    def test_risers_and_lines(self):
        import shortage_watch as sw
        data = {"shortage": {"last": "2026-10-24", "hist": {
            "PS5 Pro": [["2026-10-10", 900, 980, 30], ["2026-10-17", 960, 1000, 30], ["2026-10-24", 1080, 1150, 30]],
            "Steam Deck OLED": [["2026-10-10", 400, 450, 20], ["2026-10-24", 410, 460, 20]]}}}
        r = sw.risers(data, "2026-10-24")
        self.assertEqual([(x[0], round(x[1], 2), x[2], x[3], x[4]) for x in r], [("PS5 Pro", 0.2, 1080, 900, 14)])
        text = "\n".join(sw.lines(data, "2026-10-24"))
        self.assertIn("PS5 Pro: <b>+20%</b> (900 → 1080 € за 14 дн.)", text)
        self.assertNotIn("Steam Deck", text)
        self.assertIn("збирає базу", sw.lines({"shortage": {"hist": {"X": [["2026-10-10", 1, 1, 9]]}}}, "2026-10-12")[0])


class TerapeakReminderTest(unittest.TestCase):
    def test_reminder_after_two_weeks(self):
        import ram_alert
        d0 = date.fromisoformat(ram_alert.TERAPEAK_DATE)
        from datetime import timedelta as td
        self.assertNotIn("терапік", "\n".join(wr.headline([], d0 + td(days=13), ["x"], [])))
        self.assertIn("терапік", "\n".join(wr.headline([], d0 + td(days=14), ["x"], [])))
