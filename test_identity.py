"""Регресійні тести ідентифікації та шару якості (без мережі).

Кожен кейс — реальна помилка, знайдена під час аудиту знімка ринку DE (вересень 2026).
Запуск:  python -m unittest test_identity -v
"""
import unittest

import identity
import quality
import config

LORC = {"query": "Disney Lorcana Booster Display"}
ONEP = {"query": "One Piece Card Game Display OP"}
MTG = {"query": "Magic The Gathering Booster Box sealed"}
JAB = {"query": "Jabra Evolve2 65"}
RODE = {"query": "Rode NT1"}
KCH = {"query": "Keychron Tastatur"}
ZEL = {"query": "Zelda Tears of the Kingdom Collector's Edition"}
LEG = {"query": "Star Wars Legion"}
UW = {"query": "Warhammer Underworlds"}


def excl(title, cat):
    return identity.describe(title, cat).exclude


def key(title, cat):
    return identity.describe(title, cat).key


class TestExclusions(unittest.TestCase):
    def test_accessories_not_products(self):
        # підставка і амбушюри під ключем «headset» давали б фальшиві −44%/−37%
        self.assertTrue(excl("Jabra Evolve2 65 Deskstand Schwarz", JAB))
        self.assertTrue(excl("Jabra Ohrkissen Schaumstoff für Evolve2 40/Evolve2 65 (6 Stück)", JAB))
        self.assertTrue(excl("Jabra Ladeständer Schwarz für Evolve2 65 MS Mono 14207-63", JAB))
        self.assertTrue(excl("Handballenauflage für Keychron K2 Tastatur", KCH))

    def test_packaging_and_empty(self):
        self.assertTrue(excl("Zelda Tears of the Kingdom Collector's Edition ohne Spiel", ZEL))
        self.assertTrue(excl("Zelda Tears of the Kingdom Collectors Edition Box only", ZEL))
        self.assertTrue(excl("Magic the Gathering Booster Box EMPTY display", MTG))

    def test_graded_only_for_collectibles(self):
        self.assertIn("graded", excl("Disney Reign of Jafar Starter Deck Display PSA CGC", LORC))
        # «PSA-1» у Rode — кронштейн, а не градація
        self.assertNotIn("graded", excl("Rode NT1 Signature Black + PSA-1 Gelenkarm", RODE))

    def test_combo_plus(self):
        self.assertIn("combo+", excl("Star Wars Legion Set + Kodex", LEG))
        # «Neu + OVP» — умови, а не комбо
        self.assertNotIn("combo+", excl("Yltharis Wächter Neu + OVP Warhammer Underworlds", UW))
        self.assertNotIn("combo+", excl("Warhammer Underworlds Starter NEU+OVP", UW))

    def test_lots_variations_painted(self):
        self.assertTrue(excl("2x LEGO Star Wars 75280", LEG))
        self.assertIn("painted", excl("Star wars legion Republic AT-RT pro painted", LEG))
        self.assertIn("variation", excl("Disney Lorcana Display Auswahl Sets", LORC))
        self.assertIn("preorder", excl("Lorcana Display Vorbestellung", LORC))

    def test_out_of_scope_ptype(self):
        self.assertIn("ptype:etb", excl("Pokemon Chaos Rising Elite Trainer Box ETB Sealed", {"query": "Pokemon Booster Box versiegelt"}))
        self.assertIn("ptype:bundle", excl("MTG The Hobbit Bundle Englisch Sealed", MTG))
        self.assertIn("ptype:case", excl("Disney Lorcana Tintenlande Case 4x 24er Display", LORC))


class TestRelevance(unittest.TestCase):
    def test_glued_and_codeonly_titles_accepted(self):
        self.assertFalse(excl("Disney LorcanaTCG - Ursulas Rückkehr Booster Display Deutsch OVP", LORC))
        self.assertFalse(excl("OP15 Sealed Display - NEU & OVP", ONEP))
        self.assertEqual(identity.describe("OP-17 Display Englisch", ONEP).code, "op17")

    def test_names_that_look_like_accessory_words(self):
        # «Echo Base», «jeu de base», «Rote Schalter» — це назви/варіанти, а не аксесуари
        self.assertFalse(excl("Star Wars Legion Echo Base Defenders Army Box Special Edition", LEG))
        self.assertFalse(excl("Warhammer Underworlds Gardebraise jeu de base", UW))
        self.assertFalse(excl("Keychron K10 Ultra 8K Kabellose Mechanische Tastatur DE ISO Rote Schalter", KCH))

    def test_standard_bundle_contents_are_not_accessories(self):
        self.assertFalse(excl("JABRA Evolve2 65 Stereo UC USB-A Bluetooth black, inkl. Tasche", JAB))
        self.assertFalse(excl("Jabra Evolve2 65 Wireless Headset Stereo USB-C Bluetooth Adapter", JAB))
        self.assertFalse(excl("Jabra Evolve2 65 Flex UC Stereo für Microsoft Teams", JAB))

    def test_wrong_model_excluded(self):
        self.assertTrue(excl("Jabra Evolve2 85 Headset", JAB))
        self.assertTrue(excl("Rode NT-USB Mini USB Mikrofon", RODE))


class TestKeys(unittest.TestCase):
    def test_same_product_same_key(self):
        a = key("Disney Lorcana: Aufstieg der Flutgestalten DEUTSCH Display mit 24 Booster Packs", LORC)
        b = key("Disney Lorcana Aufstieg der Flutgestalten - 24 Booster Display NEU & OVP Deutsch", LORC)
        self.assertEqual(a, b)

    def test_language_and_set_split(self):
        de = key("Disney Lorcana Unbekannte Wildnis Display Deutsch", LORC)
        en = key("Disney Lorcana Wilds Unknown Display Englisch", LORC)
        other = key("Disney Lorcana Angriff der Ranke Display Deutsch", LORC)
        self.assertEqual(len({de, en, other}), 3)

    def test_accents_and_glued_variants(self):
        self.assertEqual(key("RØDE NT1-A Complete Set neu", RODE), key("Rode NT1 A Complete Set NEU", RODE))
        self.assertEqual(key("Jabra Evolve 2 65 MS Stereo", JAB), key("Jabra Evolve2 65 MS Stereo", JAB))

    def test_variants_do_not_merge(self):
        nt1a = key("Rode NT1-A Kit Kondensatormikrofon", RODE)
        gen5 = key("Rode NT1 5th Generation XLR USB Mikrofon", RODE)
        sig = key("Rode NT1 Signature Series Kondensatormikrofon", RODE)
        self.assertEqual(len({nt1a, gen5, sig}), 3)
        # набір з аксесуаром («+ Gelenkarm») — інший товар, ніж мікрофон окремо
        self.assertNotEqual(key("Rode NT1 Signature Mikrofon + PSA-1 Gelenkarm", RODE),
                            key("Rode NT1 Signature Mikrofon", RODE))

    def test_spec_missing_flag(self):
        self.assertFalse(identity.describe("Jabra Evolve2 65 Headset - Schwarz (26599-989-999)", JAB).spec_ok)
        self.assertTrue(identity.describe("Jabra Evolve2 65 MS Stereo Headset", JAB).spec_ok)

    def test_deterministic(self):
        t = "Disney Lorcana TCG - Unbekannte Wildnis Booster Display [DE]"
        self.assertEqual(identity.describe(t, LORC), identity.describe(t, LORC))


class TestQuality(unittest.TestCase):
    def comps(self, prices, sellers=None):
        sellers = sellers or [f"s{i}" for i in range(len(prices))]
        return [(p, s, i) for i, (p, s) in enumerate(zip(prices, sellers))]

    def test_clear_deal(self):
        v = quality.assess(110, self.comps([200, 210, 190, 205, 195]))
        self.assertTrue(v.ok, v.reasons)
        self.assertEqual(v.median, 200)

    def test_thin_reference_blocks(self):
        v = quality.assess(110, self.comps([200, 210, 190]))
        self.assertEqual(v.reasons, ["thin-ref"])

    def test_one_seller_cannot_make_market(self):
        v = quality.assess(110, self.comps([200] * 8, ["same"] * 8))
        self.assertEqual(v.reasons, ["thin-ref"])

    def test_blended_reference_blocks(self):
        v = quality.assess(60, self.comps([90, 150, 400, 700, 120, 2000]))
        self.assertIn("blended-ref", v.reasons)

    def test_weak_reference_needs_tight_market(self):
        # 4 лоти/3 продавці: лише якщо розкид ≤ 0.12
        tight = quality.assess(100, self.comps([200, 205, 198, 202], ["a", "b", "c", "a"]))
        loose = quality.assess(100, self.comps([150, 210, 180, 250], ["a", "b", "c", "d"]))
        self.assertTrue(tight.ok, tight.reasons)
        self.assertIn("weak-ref", loose.reasons)

    def test_price_cluster_is_market_price(self):
        # ≥2 інших поруч із ціною кандидата → це ринок, а не знахідка
        v = quality.assess(110, self.comps([200, 210, 190, 205, 108, 112]))
        self.assertIn("price-cluster", v.reasons)

    def test_too_deep_and_small_saving(self):
        self.assertIn("too-deep", quality.assess(40, self.comps([200, 210, 190, 205, 195])).reasons)
        self.assertIn("small-saving", quality.assess(29, self.comps([50, 51, 49, 50, 50])).reasons)

    def test_own_seller_excluded_from_reference(self):
        comps = self.comps([200, 210, 190, 205, 195, 60], ["a", "b", "c", "d", "e", "me"])
        v = quality.assess(60, comps, self_seller="me")
        self.assertEqual(v.n_comps, 5)                 # власний лот не входить в еталон
        self.assertTrue(v.ok, v.reasons)

    def test_seller_clones_are_suspicious(self):
        comps = self.comps([200, 210, 190, 205, 195, 60, 61], ["a", "b", "c", "d", "e", "me", "me"])
        v = quality.assess(60, comps, self_seller="me")
        self.assertIn("seller-dupes", v.reasons)


class TestListingFlags(unittest.TestCase):
    base = {"itemId": "1", "price": {"value": "100", "currency": "EUR"}, "conditionId": "1000",
            "itemLocation": {"country": "DE"},
            "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": "4.9"}}]}

    def test_ok(self):
        self.assertEqual(quality.listing_flags(dict(self.base)), [])

    def test_bad(self):
        b = dict(self.base, itemGroupType="SELLER_DEFINED_VARIATIONS")
        self.assertIn("variation-group", quality.listing_flags(b))
        b = dict(self.base, itemLocation={"country": "GB"})
        self.assertIn("foreign:GB", quality.listing_flags(b))
        b = dict(self.base, shippingOptions=[])
        self.assertIn("no-shipping", quality.listing_flags(b))
        b = dict(self.base, shippingOptions=[{"shippingCostType": "CALCULATED"}])
        self.assertIn("calc-shipping", quality.listing_flags(b))
        b = dict(self.base, conditionId="1500")
        self.assertIn("cond:1500", quality.listing_flags(b))


if __name__ == "__main__":
    unittest.main()
