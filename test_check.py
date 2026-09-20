"""Тести інструмента «перевір» (check.py) на фікстурах. Жодної мережі.

Фікстури повторюють форму реальних відповідей eBay Browse API (перевірено на
живих викликах 20.09.2026): item_summary/search повертає categories,
leafCategoryIds, epid, seller, shippingOptions; getItem — estimatedAvailabilities.
"""
import unittest
from datetime import datetime, timedelta, timezone

import check
import econ_model as E

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
LEGO_CATS = [{"categoryId": "220", "categoryName": "Spielzeug"},
             {"categoryId": "19006", "categoryName": "LEGO (R) Komplette Sets & Packs"}]


def item(title, price, seller="s1", *, ship=0.0, cond="1000", cats=None,
         item_id=None, country="DE", extra=None):
    d = {
        "itemId": item_id or f"v1|{abs(hash((title, price, seller))) % 10**11}|0",
        "title": title,
        "price": {"value": f"{price:.2f}", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": f"{ship:.2f}", "currency": "EUR"},
                             "shippingCostType": "FIXED"}],
        "seller": {"username": seller, "feedbackScore": 500, "feedbackPercentage": "99.5"},
        "conditionId": cond,
        "itemLocation": {"country": country},
        "categories": cats if cats is not None else LEGO_CATS,
        "itemCreationDate": "2026-09-01T10:00:00.000Z",
    }
    if extra:
        d.update(extra)
    return d


def detail(sold, avail, days_old, item_id="x"):
    created = (NOW - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"itemId": item_id, "itemCreationDate": created,
            "estimatedAvailabilities": [{"estimatedSoldQuantity": sold,
                                         "estimatedAvailableQuantity": avail}]}


class FakeFetcher:
    """Підміна мережі: віддає заздалегідь задані відповіді, рахує виклики."""

    def __init__(self, search_results=None, gtin_results=None, items=None):
        self.search_results = search_results or []
        self.gtin_results = gtin_results or []
        self.items = items or {}
        self.calls = 0
        self.queries = []

    def search(self, *, q=None, gtin=None, min_price=1, limit=200):
        self.calls += 1
        self.queries.append({"q": q, "gtin": gtin})
        if gtin:
            return list(self.gtin_results), len(self.gtin_results)
        return list(self.search_results), len(self.search_results)

    def item(self, item_id):
        self.calls += 1
        return self.items.get(item_id, detail(0, 1, 30, item_id))


# --------------------------------------------------------------------------- #
class TestProductFromArgs(unittest.TestCase):
    def test_lego_number(self):
        p = check.product_from_args(lego="10354")
        self.assertEqual(p.query, "LEGO 10354")
        self.assertEqual(p.must, [{"10354"}])

    def test_bad_lego_number_rejected(self):
        with self.assertRaises(ValueError):
            check.product_from_args(lego="abc")

    def test_ean_validated(self):
        self.assertEqual(check.product_from_args(ean="5702017829135").gtin, "5702017829135")
        with self.assertRaises(ValueError):
            check.product_from_args(ean="123")

    def test_query_number_wins_over_words(self):
        p = check.product_from_args(query="LEGO Icons 10354 Auenland")
        self.assertEqual(p.must, [{"10354"}])

    def test_query_without_number_uses_words(self):
        p = check.product_from_args(query="Pokemon Top Trainer Box Karmesin")
        flat = {w for grp in p.must for w in grp}
        self.assertIn("pokemon", flat)
        self.assertIn("karmesin", flat)
        self.assertNotIn("top", flat)      # стоп-слово
        self.assertNotIn("box", flat)

    def test_short_model_number_is_kept(self):
        # Баг, знайдений на реальних даних: «Oral-B iO 6» губив «6» і
        # зіставлявся з «Oral-B iO Kids 6+».
        self.assertIn({"6"}, check.must_from_query("Oral-B iO 6 Zahnbuerste"))
        self.assertIn({"65"}, check.must_from_query("Jabra Evolve2 65"))
        self.assertIn({"nt1"}, check.must_from_query("Rode NT1"))

    def test_must_groups_are_deduped(self):
        must = check.must_from_query("Rode NT1")
        self.assertEqual(len(must), len({frozenset(g) for g in must}))

    def test_manual_must_and_exclude(self):
        p = check.product_from_args(query="Oral-B iO 6 Zahnbuerste", exclude=["kids"])
        self.assertEqual(check.match_reasons("Oral-B iO Kids Zahnbuerste 6+ Stitch", p),
                         ["виключено:kids"])
        p2 = check.product_from_args(query="Pokemon Display", must=["karmesin"])
        self.assertTrue(check.match_reasons("Pokemon Display Silberne Sturmwinde", p2))
        self.assertFalse(check.match_reasons("Pokemon Display Karmesin Purpur", p2))

    def test_needs_some_input(self):
        with self.assertRaises(ValueError):
            check.product_from_args()


class TestMatchReasons(unittest.TestCase):
    def setUp(self):
        self.p = check.product_from_args(lego="10354")

    def ok(self, title):
        self.assertEqual(check.match_reasons(title, self.p), [], title)

    def bad(self, title):
        self.assertNotEqual(check.match_reasons(title, self.p), [], title)

    def test_accepts_the_real_set(self):
        self.ok("LEGO Icons 10354 Der Herr der Ringe: Das Auenland - neu & OVP")
        self.ok("LEGO® Icons 10354 Der Herr der Ringe Das Auenland EXKLUSIV!")

    def test_rejects_other_set_number(self):
        self.bad("LEGO Icons 10316 Der Herr der Ringe Bruchtal neu OVP")

    def test_rejects_parts_and_instructions(self):
        self.bad("LEGO 10354 Bauanleitung / Anleitung ohne Steine")
        self.bad("LEGO 10354 Aufkleber Sticker Ersatz")
        self.bad("Minifigur aus LEGO 10354 Set Frodo neu")
        self.bad("LEGO 10354 Ersatzteile Steine einzeln")

    def test_rejects_empty_box(self):
        self.bad("LEGO 10354 nur Box / leere OVP Karton")
        self.bad("LEGO 10354 Das Auenland OHNE Box")

    def test_rejects_accessory_for(self):
        self.bad("Acryl Vitrine für LEGO 10354 Auenland Staubschutz")

    def test_rejects_multipack(self):
        self.bad("LEGO 10354 Auenland 2x Sets Bundle neu")
        self.bad("LEGO 10354 3er Set neu OVP")

    def test_rejects_damaged_and_custom(self):
        self.bad("LEGO 10354 Auenland defekt Karton beschädigt")
        self.bad("LEGO 10354 Auenland custom nachbau MOC")

    def test_rejects_preorder(self):
        self.bad("LEGO 10354 Auenland Vorbestellung erscheint Oktober")

    def test_rejects_bag_containing_the_product(self):
        # Реальний лот на tiptoi: «Tasche mit Ravensburger tiptoi Stift Starterset».
        p = check.product_from_args(query="Ravensburger tiptoi Starterset")
        self.assertEqual(check.match_reasons(
            "Tasche mit Ravensburger tiptoi Stift Starterset Blau", p),
            ["тара/сумка, не товар"])
        self.assertEqual(check.match_reasons(
            "Ravensburger tiptoi Starterset Bauernhof neu OVP", p), [])

    def test_rejects_poster_and_light_kit(self):
        self.bad("LEGO® Poster zu Herr der Ringe Auenland Set 10354")
        self.bad("LIGHTAILING LichtSet Für Lego10354 Der Herr der Ringe")

    def test_umlaut_and_case_insensitive(self):
        self.ok("lego icons 10354 der herr der ringe das auenland")

    def test_number_glued_to_text_still_matches(self):
        self.ok("LEGO Icons Nr.10354 Auenland neu OVP")


class TestBuildMarket(unittest.TestCase):
    def setUp(self):
        self.p = check.product_from_args(lego="10354")

    def test_basic_stats_and_per_seller_dedup(self):
        items = [
            item("LEGO Icons 10354 Auenland neu OVP", 200.0, "a"),
            item("LEGO Icons 10354 Auenland neu OVP", 210.0, "a"),   # той самий продавець
            item("LEGO Icons 10354 Auenland neu OVP", 220.0, "b"),
            item("LEGO Icons 10354 Auenland neu OVP", 240.0, "c"),
            item("LEGO Icons 10354 Auenland neu OVP", 260.0, "d"),
            item("LEGO Icons 10354 Auenland neu OVP", 280.0, "e"),
        ]
        m = check.build_market(items, self.p)
        self.assertEqual(m.n_kept, 6)
        self.assertEqual(m.n_sellers, 5)          # a зведений в одну ціну
        self.assertEqual(m.prices[0], 205.0)      # медіана двох лотів a
        self.assertEqual(m.median, 240.0)

    def test_shipping_included_in_price(self):
        m = check.build_market([item("LEGO 10354 Auenland", 200.0, "a", ship=6.19)], self.p)
        self.assertAlmostEqual(m.minimum, 206.19)

    def test_junk_is_dropped_with_reasons(self):
        items = [
            item("LEGO Icons 10354 Auenland neu OVP", 215.0, "a"),
            item("LEGO 10354 Bauanleitung", 9.0, "b"),
            item("LEGO 10316 Bruchtal", 300.0, "c"),
            item("Minifigur aus LEGO 10354", 12.0, "d"),
        ]
        m = check.build_market(items, self.p)
        self.assertEqual(m.n_kept, 1)
        self.assertEqual(sum(m.dropped.values()), 3)

    def test_listing_flags_applied(self):
        bad = item("LEGO Icons 10354 Auenland", 200.0, "a", cond="3000")
        foreign = item("LEGO Icons 10354 Auenland", 200.0, "b", country="GB")
        nogroup = item("LEGO Icons 10354 Auenland", 200.0, "c",
                       extra={"itemGroupType": "SELLER_DEFINED_VARIATIONS"})
        m = check.build_market([bad, foreign, nogroup], self.p)
        self.assertEqual(m.n_kept, 0)
        self.assertEqual(sorted(m.dropped), ["cond:3000", "foreign:GB", "variation-group"])

    def test_velocity_sample_spreads_over_price_range(self):
        # Замір швидкості не має брати лише найдешевші лоти: обсяги продажів
        # сидять у дилерів вище по ціні (знайдено на Oral-B iO 6).
        lots = [item(f"LEGO Icons 10354 Auenland {i}", 200.0 + i * 5, f"s{i}")
                for i in range(20)]
        m = check.build_market(lots, self.p)
        first4 = [it["title"] for it in m.velocity_items[:4]]
        prices = [float(next(x for x in lots if x["title"] == t)["price"]["value"])
                  for t in first4]
        self.assertLess(min(prices), 210)     # є дешеві
        self.assertGreater(max(prices), 270)  # і є дорогі
        self.assertEqual(len(m.velocity_items), m.n_kept)

    def test_category_path_from_summary(self):
        m = check.build_market([item("LEGO Icons 10354 Auenland", 200.0)], self.p)
        self.assertIn("Spielzeug", m.category_path)

    def test_empty_market(self):
        m = check.build_market([], self.p)
        self.assertEqual(m.n_kept, 0)
        self.assertIsNone(m.low5)


class TestVelocity(unittest.TestCase):
    def test_per_listing_rate_and_weeks_to_sell(self):
        # 4 лоти, кожен продав 10 шт за 10 днів = 7 шт/тиж на лот → ~0.14 тиж
        v = check.build_velocity([detail(10, 5, 10)] * 4, now=NOW)
        self.assertAlmostEqual(v.per_listing_week, 7.0)
        self.assertAlmostEqual(v.weeks_to_sell, round(1 / 7, 1))
        self.assertEqual(v.sold_total, 40)

    def test_metric_is_invariant_to_sample_size(self):
        # Головний баг, знайдений на реальних даних (Oral-B iO 6): сума продажів
        # по вибірці росла з її розміром і перевертала вердикт.
        small = check.build_velocity([detail(10, 5, 10)] * 4, now=NOW)
        big = check.build_velocity([detail(10, 5, 10)] * 14, now=NOW)
        self.assertAlmostEqual(small.per_listing_week, big.per_listing_week)
        self.assertAlmostEqual(small.weeks_to_sell, big.weeks_to_sell)
        self.assertNotAlmostEqual(small.per_week, big.per_week)   # сира сума — росте

    def test_market_estimate_scales_with_market_size(self):
        v = check.build_velocity([detail(10, 5, 10)] * 4, now=NOW, n_market_lots=30)
        self.assertAlmostEqual(v.market_week, round(7.0 * 30, 1))

    def test_lots_with_stock_but_no_sales_are_slow_not_missing(self):
        v = check.build_velocity([detail(0, 5, 30)] * 5, now=NOW)
        self.assertEqual(v.per_listing_week, 0.0)
        self.assertIsNone(v.weeks_to_sell)
        self.assertEqual(v.confidence, "низька")

    def test_note_warns_that_measurement_is_from_dealer_lots(self):
        v = check.build_velocity([detail(10, 5, 10)] * 5, now=NOW)
        self.assertIn("поштучний", v.note)

    def test_single_quantity_lots_give_no_data(self):
        v = check.build_velocity([detail(0, 1, 30), detail(0, 1, 40)], now=NOW)
        self.assertIsNone(v.per_listing_week)
        self.assertEqual(v.confidence, "не виміряно")

    def test_thin_data_is_low_confidence(self):
        v = check.build_velocity([detail(2, 3, 20), detail(0, 1, 20)], now=NOW)
        self.assertEqual(v.confidence, "низька")

    def test_young_lot_flagged_as_noisy(self):
        v = check.build_velocity([detail(10, 5, 3)] * 5, now=NOW)
        self.assertEqual(v.confidence, "середня")
        self.assertIn("шумна", v.note)

    def test_empty(self):
        self.assertIsNone(check.build_velocity([], now=NOW).per_listing_week)


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        self.p = check.product_from_args(lego="10354")
        self.items = [item(f"LEGO Icons 10354 Auenland neu OVP {i}", 260.0 + i * 3, f"s{i}")
                      for i in range(8)]
        self.market = check.build_market(self.items, self.p)
        self.fast = check.build_velocity([detail(20, 5, 10)] * 4, now=NOW)
        self.none_v = check.build_velocity([detail(0, 1, 30)] * 4, now=NOW)

    def test_thin_market_is_unknown(self):
        thin = check.build_market(self.items[:2], self.p)
        r = check.evaluate(self.p, thin, self.fast, buy_price=100.0)
        self.assertEqual(r.verdict, "UNKNOWN")
        self.assertTrue(any("мало лотів" in x for x in r.reasons))

    def test_no_buy_price_is_unknown_but_shows_max_buy(self):
        r = check.evaluate(self.p, self.market, self.fast)
        self.assertEqual(r.verdict, "UNKNOWN")
        self.assertGreater(r.deals["ebay"].max_buy, 0)

    def test_good_deal_is_buy(self):
        r = check.evaluate(self.p, self.market, self.fast, buy_price=150.0)
        self.assertEqual(r.verdict, "BUY")
        self.assertGreater(r.deals["ebay"].net, 15)

    def test_expensive_buy_is_skip(self):
        r = check.evaluate(self.p, self.market, self.fast, buy_price=250.0)
        self.assertEqual(r.verdict, "SKIP")

    def test_skip_reason_names_the_threshold_that_failed(self):
        # Прибуток великий, але ROI малий — причина має називати саме ROI.
        r = check.evaluate(self.p, self.market, self.fast, buy_price=195.0,
                           min_profit=15.0, min_roi=0.15)
        self.assertEqual(r.verdict, "SKIP")
        joined = " ".join(r.reasons)
        self.assertIn("ROI", joined)
        self.assertNotIn("прибуток", joined)

    def test_unmeasured_velocity_blocks_buy(self):
        r = check.evaluate(self.p, self.market, self.none_v, buy_price=150.0)
        self.assertEqual(r.verdict, "UNKNOWN")
        self.assertTrue(any("швидкість" in x for x in r.reasons))

    def test_slow_seller_is_skip(self):
        slow = check.build_velocity([detail(1, 2, 300)] * 4, now=NOW)
        r = check.evaluate(self.p, self.market, slow, buy_price=150.0)
        self.assertEqual(r.verdict, "SKIP")

    def test_patient_price_uses_median_and_beats_fast_price(self):
        r = check.evaluate(self.p, self.market, self.fast, buy_price=150.0)
        self.assertAlmostEqual(r.patient.sale_price, self.market.median)
        self.assertGreater(r.patient.net, r.deals["ebay"].net)

    def test_unspecific_query_is_warned(self):
        p = check.product_from_args(query="Pokemon Box")
        m = check.build_market(
            [item(f"Pokemon Box {i}", 40.0, f"s{i}") for i in range(8)], p)
        r = check.evaluate(p, m, self.fast, buy_price=20.0)
        self.assertTrue(any("неспецифічний" in w for w in r.warnings))

    def test_specific_query_not_warned(self):
        r = check.evaluate(self.p, self.market, self.fast, buy_price=150.0)
        self.assertFalse(any("неспецифічний" in w for w in r.warnings))

    def test_consumables_are_rejected_anywhere_in_title(self):
        # Акумулятори/насадки не бувають нашим товаром — відсіваємо завжди.
        p = check.product_from_args(query="Oral-B iO 6 Zahnbuerste")
        for t in ["Original Oral B iO 6 7 8 9 10 Akku Batterie Zahnbuerste",
                  "Oral-B iO 6 Aufsteckbuersten 4er Zahnbuerste",
                  "Oral-B iO 6 Zahnbuerste Ladestation Ersatz"]:
            self.assertTrue(check.match_reasons(t, p), t)

    def test_bundle_words_do_not_exclude_the_product_itself(self):
        # Реальний лот за €90,01 випадав із ринку лише через «Reiseetui» в кінці.
        p = check.product_from_args(query="Oral-B iO 6 Zahnbuerste")
        self.assertEqual(check.match_reasons(
            "Oral-B iO Series 6N Elektrische Zahnbuerste Grau 5 Modi Bluetooth Reiseetui",
            p), [])
        self.assertTrue(check.match_reasons(
            "Reiseetui fuer Oral-B iO 6 Zahnbuerste Hartschale", p))

    def test_truncated_market_is_warned(self):
        m = check.build_market(self.items, self.p, total_reported=805)
        r = check.evaluate(self.p, m, self.fast, buy_price=150.0)
        self.assertTrue(any("завеликий" in w for w in r.warnings))

    def test_lego_uses_14_percent(self):
        r = check.evaluate(self.p, self.market, self.fast, buy_price=150.0)
        self.assertAlmostEqual(r.fee.rate, 0.14)

    def test_unknown_category_warns(self):
        items = [item(f"LEGO Icons 10354 Auenland {i}", 260.0, f"s{i}",
                      cats=[{"categoryId": "9", "categoryName": "Musikinstrumente"}])
                 for i in range(8)]
        m = check.build_market(items, self.p)
        r = check.evaluate(self.p, m, self.fast, buy_price=150.0)
        self.assertTrue(any("комісія" in w for w in r.warnings))

    def test_bimodal_market_is_unknown_not_silently_trimmed(self):
        # Під одним запитом два різні товари (постери ~€90 і набір ~€260).
        # Ціновий діапазон їх обріже — але мовчки цього робити не можна.
        mixed = [item(f"LEGO Icons 10354 Auenland {i}", p, f"s{i}")
                 for i, p in enumerate([80, 90, 100, 250, 260, 270, 500, 520])]
        m = check.build_market(mixed, self.p)
        self.assertGreater(m.band_dropped, 0)
        r = check.evaluate(self.p, m, self.fast, buy_price=60.0)
        self.assertEqual(r.verdict, "UNKNOWN")
        self.assertTrue(any("неоднорідна" in x for x in r.reasons))

    def test_price_band_removes_junk_tail(self):
        # Реальний випадок LEGO 10354: постери €18–30 поряд із набором €215–270.
        lots = [item(f"LEGO Icons 10354 Auenland {i}", p, f"s{i}") for i, p in
                enumerate([215, 225, 229, 230, 240, 250, 260, 268, 270, 280, 290, 300])]
        junk = [item("LEGO 10354 Auenland Schmuckstueck", 18.6, "j1"),
                item("LEGO 10354 Auenland Mini", 30.7, "j2")]
        m = check.build_market(lots + junk, self.p)
        self.assertEqual(m.band_dropped, 2)
        self.assertGreater(m.low5, 200)

    def test_wide_dispersion_still_flagged_when_band_keeps_everything(self):
        spread = [item(f"LEGO Icons 10354 Auenland {i}", p, f"s{i}")
                  for i, p in enumerate([120, 130, 140, 200, 260, 300, 320, 340])]
        m = check.build_market(spread, self.p)
        self.assertEqual(m.band_dropped, 0)
        self.assertGreater(m.dispersion, check.MAX_DISPERSION)
        r = check.evaluate(self.p, m, self.fast, buy_price=60.0)
        self.assertEqual(r.verdict, "UNKNOWN")
        self.assertTrue(any("розкидані" in x for x in r.reasons))

    def test_empty_market_is_unknown(self):
        r = check.evaluate(self.p, check.build_market([], self.p), self.fast, buy_price=10.0)
        self.assertEqual(r.verdict, "UNKNOWN")

    def test_carrier_changes_max_buy(self):
        a = check.evaluate(self.p, self.market, self.fast, carrier="dhl_paket_2kg")
        b = check.evaluate(self.p, self.market, self.fast, carrier="hermes_s2s")
        self.assertGreater(b.deals["ebay"].max_buy, a.deals["ebay"].max_buy)

    def test_offchannel_upside_is_only_a_warning_not_a_buy(self):
        # Ціна, за якої eBay не тягне, а Kleinanzeigen формально тягне.
        r = check.evaluate(self.p, self.market, self.fast, buy_price=215.0, ka_factor=1.0)
        self.assertEqual(r.verdict, "SKIP")
        self.assertTrue(any("поза eBay" in w for w in r.warnings))


class TestRunCheck(unittest.TestCase):
    def test_end_to_end_with_fake_fetcher(self):
        items = [item(f"LEGO Icons 10354 Auenland neu OVP {i}", 260.0 + i * 3, f"s{i}")
                 for i in range(8)]
        details = {it["itemId"]: detail(20, 5, 10, it["itemId"]) for it in items}
        f = FakeFetcher(search_results=items, items=details)
        p = check.product_from_args(lego="10354")
        r = check.run_check(f, p, buy_price=150.0)
        self.assertEqual(r.verdict, "BUY")
        self.assertEqual(f.queries[0]["q"], "LEGO 10354")
        self.assertEqual(f.calls, 1 + min(check.VELOCITY_SAMPLE, len(items)))

    def test_no_velocity_saves_calls(self):
        items = [item(f"LEGO Icons 10354 Auenland {i}", 260.0, f"s{i}") for i in range(8)]
        f = FakeFetcher(search_results=items)
        r = check.run_check(f, check.product_from_args(lego="10354"),
                            velocity_sample=0, buy_price=150.0)
        self.assertEqual(f.calls, 1)
        self.assertEqual(r.verdict, "UNKNOWN")      # без швидкості — не BUY

    def test_ean_resolves_via_gtin_then_searches_by_name(self):
        gtin_hit = item("LEGO Icons 10354 Auenland", 265.0, "z",
                        item_id="v1|GTINHIT|0")
        market = [item(f"LEGO Icons 10354 Auenland {i}", 260.0 + i, f"s{i}") for i in range(8)]
        f = FakeFetcher(search_results=market, gtin_results=[gtin_hit],
                        items={"v1|GTINHIT|0": {"brand": "LEGO", "mpn": "10354",
                                                "title": "LEGO Icons 10354 Auenland"}})
        p = check.product_from_args(ean="5702017829135")
        r = check.run_check(f, p, velocity_sample=0)
        self.assertEqual(f.queries[0]["gtin"], "5702017829135")
        self.assertEqual(f.queries[1]["q"], "LEGO 10354")     # ринок — звичайним пошуком
        self.assertIn("gtin", r.product.source)

    def test_unknown_gtin_raises_with_hint(self):
        f = FakeFetcher(search_results=[], gtin_results=[])
        with self.assertRaises(LookupError) as cm:
            check.run_check(f, check.product_from_args(ean="5702017829135"))
        self.assertIn("--query", str(cm.exception))

    def test_format_result_does_not_crash(self):
        items = [item(f"LEGO Icons 10354 Auenland {i}", 260.0, f"s{i}") for i in range(8)]
        f = FakeFetcher(search_results=items)
        r = check.run_check(f, check.product_from_args(lego="10354"),
                            velocity_sample=0, buy_price=150.0)
        txt = check.format_result(r, calls=f.calls, verbose=True)
        self.assertIn("РИНОК", txt)
        self.assertIn("kleinanzeigen", txt)
        self.assertIsInstance(check._as_dict(r, f.calls)["market"]["kept"], int)


if __name__ == "__main__":
    unittest.main()
