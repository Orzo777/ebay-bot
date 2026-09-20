"""Тести грошових формул (econ_model.py). Жодної мережі."""
import unittest

import econ_model as E


class TestFeeCategory(unittest.TestCase):
    def test_lego_is_toys_14(self):
        c = E.fee_category("Spielzeug|Bausets & Konstruktion|LEGO (R) Konstruktionsspielzeuge|LEGO (R) Komplette Sets & Packs")
        self.assertEqual(c.group, "toys")
        self.assertAlmostEqual(c.rate, 0.14)
        self.assertTrue(c.known)

    def test_tcg_11(self):
        c = E.fee_category("Sammeln & Seltenes|Sammelkartenspiele/TCGs|TCG OVP")
        self.assertEqual(c.group, "tcg")
        self.assertAlmostEqual(c.rate, 0.11)

    def test_videogames_12(self):
        c = E.fee_category("PC- & Videospiele|Zubehör|Controller")
        self.assertEqual(c.group, "games")
        self.assertAlmostEqual(c.rate, 0.12)

    def test_tech_new_7_used_5(self):
        path = "Computer, Tablets & Netzwerk|Tablets & eBook-Reader"
        self.assertAlmostEqual(E.fee_category(path).rate, 0.07)
        self.assertAlmostEqual(E.fee_category(path, condition_id="3000").rate, 0.05)

    def test_condition_does_not_lower_toy_fee(self):
        c = E.fee_category("Spielzeug|Bausets & Konstruktion", condition_id="3000")
        self.assertAlmostEqual(c.rate, 0.14)

    def test_electric_toothbrush_is_beauty_14_not_tech(self):
        # Oral-B / Sonicare лежать у Beauty & Gesundheit → 14%, а не 7%.
        c = E.fee_category("Beauty & Gesundheit|Mundpflege|Elektrische Zahnbürsten")
        self.assertAlmostEqual(c.rate, 0.14)

    def test_unknown_path_takes_worst_rate_and_is_flagged(self):
        c = E.fee_category("Musikinstrumente|Gitarren & Bässe|Effektgeräte")
        self.assertFalse(c.known)
        self.assertAlmostEqual(c.rate, E.FEE_RATES[E.UNKNOWN_FEE_GROUP])

    def test_empty_path_is_flagged(self):
        self.assertFalse(E.fee_category(None).known)
        self.assertFalse(E.fee_category("").known)


class TestEbayFee(unittest.TestCase):
    def test_order_fee_added(self):
        self.assertAlmostEqual(E.ebay_fee(100.0, 0.14), 14.45)

    def test_fee_is_on_gross_including_shipping(self):
        # товар 80 + доставка 6.19 = 86.19 бази для комісії
        self.assertAlmostEqual(E.ebay_fee(86.19, 0.14), round(86.19 * 0.14 + 0.45, 2))

    def test_tier_above_990(self):
        self.assertAlmostEqual(E.ebay_fee(1990.0, 0.14),
                               round(990 * 0.14 + 1000 * 0.03 + 0.45, 2))

    def test_zero(self):
        self.assertEqual(E.ebay_fee(0, 0.14), 0.0)


class TestShipping(unittest.TestCase):
    def test_table(self):
        self.assertAlmostEqual(E.shipping_cost("dhl_paket_2kg"), 6.19)
        self.assertAlmostEqual(E.shipping_cost("hermes_s2s"), 3.70)
        self.assertAlmostEqual(E.shipping_cost("abholung"), 0.0)

    def test_default_and_override(self):
        self.assertAlmostEqual(E.shipping_cost(None), E.SHIPPING[E.DEFAULT_SHIPPING])
        self.assertAlmostEqual(E.shipping_cost("dhl_paket_2kg", override=2.60), 2.60)

    def test_unknown_carrier_falls_back(self):
        self.assertAlmostEqual(E.shipping_cost("hyperloop"), E.SHIPPING[E.DEFAULT_SHIPPING])


class TestDeal(unittest.TestCase):
    def setUp(self):
        self.ch = E.channels()

    def test_lego_80_breakeven_is_22_percent_below_market(self):
        # СТРАТЕГІЯ.md: LEGO за €80 → безбитковість при купівлі на ~22% нижче ринку.
        d = E.deal(80.0, fee_rate=0.14, channel=self.ch["ebay"],
                   ship=E.shipping_cost("dhl_paket_2kg"))
        self.assertAlmostEqual(d.breakeven_buy, round(80 - (80 * 0.14 + 0.45) - 6.19, 2))
        self.assertEqual(round((1 - d.breakeven_buy / 80) * 100), 22)

    def test_tech_200_breakeven_is_10_percent_below_market(self):
        d = E.deal(200.0, fee_rate=0.07, channel=self.ch["ebay"],
                   ship=E.shipping_cost("dhl_paket_2kg"))
        self.assertEqual(round((1 - d.breakeven_buy / 200) * 100), 10)

    def test_net_and_roi(self):
        d = E.deal(100.0, fee_rate=0.14, buy_price=60.0, channel=self.ch["ebay"],
                   ship=6.19)
        self.assertAlmostEqual(d.net, round(100 - 14.45 - 6.19 - 60, 2))
        self.assertAlmostEqual(d.roi, round(d.net / 60, 4))

    def test_max_buy_hits_target_roi_exactly(self):
        d = E.deal(120.0, fee_rate=0.11, channel=self.ch["ebay"], ship=4.39,
                   target_roi=0.25)
        again = E.deal(120.0, fee_rate=0.11, buy_price=d.max_buy,
                       channel=self.ch["ebay"], ship=4.39, target_roi=0.25)
        self.assertAlmostEqual(again.roi, 0.25, places=2)

    def test_kleinanzeigen_beats_ebay_on_14_percent_category(self):
        # СТРАТЕГІЯ.md, LEGO 10354: eBay ≈0, Kleinanzeigen помітно краще.
        eb = E.deal(269.0, fee_rate=0.14, buy_price=214.0, channel=self.ch["ebay"], ship=6.19)
        ka = E.deal(269.0, fee_rate=0.14, buy_price=214.0,
                    channel=self.ch["kleinanzeigen"], ship=6.19)
        self.assertLess(eb.net, 12)
        self.assertGreater(ka.net, eb.net)

    def test_kleinanzeigen_price_is_flagged_as_assumption(self):
        ka = E.deal(100.0, fee_rate=0.14, buy_price=50.0,
                    channel=self.ch["kleinanzeigen"], ship=4.39)
        self.assertFalse(ka.price_measured)
        self.assertTrue(ka.warnings)

    def test_kleinanzeigen_applies_price_factor(self):
        ka = E.deal(100.0, fee_rate=0.14, buy_price=50.0,
                    channel=E.channels(ka_factor=0.8)["kleinanzeigen"], ship=4.39)
        self.assertAlmostEqual(ka.sale_price, 80.0)

    def test_seller_does_not_pay_shipping_off_ebay(self):
        ka = E.deal(100.0, fee_rate=0.14, buy_price=50.0,
                    channel=E.channels(ka_factor=1.0)["kleinanzeigen"], ship=6.19)
        self.assertEqual(ka.shipping, 0.0)
        self.assertAlmostEqual(ka.net, round(100 - 2.00 - 50, 2))

    def test_vinted_has_no_listing_cost(self):
        v = E.deal(100.0, fee_rate=0.14, buy_price=50.0,
                   channel=E.channels(ka_factor=1.0)["vinted"], ship=6.19)
        self.assertAlmostEqual(v.net, 50.0)

    def test_return_rate_reduces_net(self):
        a = E.deal(100.0, fee_rate=0.14, buy_price=50.0, channel=self.ch["ebay"], ship=6.19)
        b = E.deal(100.0, fee_rate=0.14, buy_price=50.0, channel=self.ch["ebay"], ship=6.19,
                   return_rate=0.05)
        self.assertLess(b.net, a.net)

    def test_max_buy_never_negative(self):
        d = E.deal(5.0, fee_rate=0.14, channel=self.ch["ebay"], ship=6.19)
        self.assertGreaterEqual(d.max_buy, 0.0)


if __name__ == "__main__":
    unittest.main()
