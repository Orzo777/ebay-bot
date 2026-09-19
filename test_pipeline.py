"""Наскрізні тести конвеєра на синтетичних даних (без мережі).

    python -m unittest test_pipeline -v
"""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import config
import main

CAT = {"query": "Disney Lorcana Booster Display", "min_price": 80}
T0 = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def listing(iid, price, seller="s0", country="DE", title=None, fb=500, pct="99.5", **extra):
    it = {
        "itemId": iid,
        "title": title or "Disney Lorcana Unbekannte Wildnis Booster Display Deutsch 24 Booster",
        "price": {"value": f"{price:.2f}", "currency": "EUR"},
        "conditionId": "1000", "condition": "Neu",
        "itemLocation": {"country": country},
        "seller": {"username": seller, "feedbackScore": fb, "feedbackPercentage": pct},
        "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": "0.00"}}],
        "buyingOptions": ["FIXED_PRICE"],
        "itemCreationDate": "2026-09-20T09:00:00.000Z",
        "itemWebUrl": f"https://www.ebay.de/itm/{iid}",
    }
    it.update(extra)
    return it


class FakeClient:
    def __init__(self, world):
        self.world = world
        self.total_override = None

    def search_ex(self, query, min_price, sort=None, country=None):
        its = [i for i in self.world
               if (i.get("itemLocation") or {}).get("country") == country
               and float(i["price"]["value"]) >= min_price]
        total = self.total_override if self.total_override is not None else len(its)
        return its[:200], total


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = {k: getattr(config, k) for k in (
            "PRICE_HISTORY_FILE", "DISCOVERY_LOG_FILE", "CATEGORIES", "ALERT_MODE",
            "ALERT_TIERS", "EBAY_CREDS_OK", "MARKET_COUNTRIES")}
        config.PRICE_HISTORY_FILE = os.path.join(self.tmp, "ph.json")
        config.DISCOVERY_LOG_FILE = os.path.join(self.tmp, "dl.jsonl")
        config.CATEGORIES = [CAT]
        config.EBAY_CREDS_OK = True
        config.MARKET_COUNTRIES = ["DE", "AT"]
        config.ALERT_TIERS = ["OK", "MEDIUM", "UNKNOWN"]      # ліквідність тут не предмет тесту
        self.sent = []
        self._deliver = main.deliver
        self.fail = False

        def fake(a):
            if self.fail:
                raise RuntimeError("Telegram 500")
            self.sent.append(main.format_message(a))
        main.deliver = fake
        self.world = [listing(f"v1|m{i}", 150 + d, seller=f"seller{i}")
                      for i, d in enumerate([-8, -4, 0, 3, 6, 9, -2, 5])]

    def tearDown(self):
        main.deliver = self._deliver
        for k, v in self._saved.items():
            setattr(config, k, v)

    def run_pass(self, when, mode="shadow", complete_total=None):
        config.ALERT_MODE = mode
        store = main.HistoryStore(config.PRICE_HISTORY_FILE)
        client = FakeClient(self.world)
        client.total_override = complete_total
        liq = main.SelfTrackedLiquidity(store)
        st = main.run_pass(client, [dict(CAT)], store, liq, when, quiet=True)
        return store, st

    # --- сідінг ---------------------------------------------------------------
    def test_seed_pass_records_but_never_evaluates(self):
        store, st = self.run_pass(T0)
        self.assertEqual(st["verdicts"], {"ALERT": 0, "HOLD": 0})
        self.assertEqual(sum(len(v) for v in store.history.values()), 8)
        self.assertIn(CAT["query"], store.seeded)

    def test_foreign_and_variation_listings_never_enter_history(self):
        self.world += [listing("v1|gb", 60, seller="uk", country="GB"),
                       listing("v1|grp", 95, seller="grp", itemGroupType="SELLER_DEFINED_VARIATIONS")]
        store, st = self.run_pass(T0)
        ids = {p["item_id"] for pts in store.history.values() for p in pts}
        self.assertNotIn("v1|gb", ids)
        self.assertNotIn("v1|grp", ids)
        self.assertEqual(st["skipped_flags"].get("variation-group"), 1)

    # --- вердикти ---------------------------------------------------------------
    def test_shadow_alert_is_logged_not_sent(self):
        self.run_pass(T0)
        self.world.append(listing("v1|deal", 80, seller="newbie"))
        store, st = self.run_pass(T0 + timedelta(minutes=30), "shadow")
        self.assertEqual(st["verdicts"]["ALERT"], 1)
        self.assertEqual(self.sent, [])
        with open(config.DISCOVERY_LOG_FILE, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh]
        self.assertEqual(rows[-1]["kind"], "shadow")
        self.assertEqual(rows[-1]["verdict"], "ALERT")
        self.assertIn("Unbekannte", rows[-1]["title"])

    def test_liquidity_unknown_holds_alert(self):
        config.ALERT_TIERS = ["OK", "MEDIUM"]
        self.run_pass(T0)
        self.world.append(listing("v1|deal", 80, seller="newbie"))
        store, st = self.run_pass(T0 + timedelta(minutes=30), "live")
        self.assertEqual(st["verdicts"], {"ALERT": 0, "HOLD": 1})
        self.assertEqual(st["hold_reasons"], {"liq-unknown": 1})
        self.assertEqual(self.sent, [])

    def test_stale_listing_is_held(self):
        # «знахідка», що висить роками, — пастка/привид, а не угода
        self.run_pass(T0)
        self.world.append(listing("v1|old", 80, seller="ghost", itemCreationDate="2023-11-01T09:00:00.000Z"))
        store, st = self.run_pass(T0 + timedelta(minutes=30), "live")
        self.assertEqual(st["verdicts"]["ALERT"], 0)
        self.assertIn("stale-listing", st["hold_reasons"])
        self.assertEqual(self.sent, [])

    def test_weak_seller_is_held(self):
        self.run_pass(T0)
        self.world.append(listing("v1|deal", 80, seller="risky", fb=12, pct="96.0"))
        store, st = self.run_pass(T0 + timedelta(minutes=30), "live")
        self.assertEqual(st["verdicts"]["ALERT"], 0)
        self.assertIn("seller", st["hold_reasons"])

    # --- доставка ---------------------------------------------------------------
    def test_delivery_failure_is_retried_and_never_duplicated(self):
        self.run_pass(T0)
        self.world.append(listing("v1|deal", 80, seller="newbie"))
        self.fail = True
        store, st = self.run_pass(T0 + timedelta(minutes=30), "live")
        self.assertEqual(len(self.sent), 0)
        self.assertEqual(len(store.alerted), 0)
        self.fail = False
        store, st = self.run_pass(T0 + timedelta(minutes=60), "live")
        self.assertEqual(len(self.sent), 1, "знахідка не має губитись після збою Telegram")
        store, st = self.run_pass(T0 + timedelta(minutes=90), "live")
        self.assertEqual(len(self.sent), 1, "повторних сповіщень бути не має")

    def test_message_contains_verification_data(self):
        self.run_pass(T0)
        self.world.append(listing("v1|deal", 80, seller="newbie"))
        self.run_pass(T0 + timedelta(minutes=30), "live")
        msg = self.sent[0]
        for needle in ("ЗНАХІДКА", "Еталон", "медіана", "економія", "Ліквідність", "Продавець",
                       "ПЕРЕВІР ПЕРЕД КУПІВЛЕЮ", "LH_Sold=1", "https://www.ebay.de/itm/"):
            self.assertIn(needle, msg)

    # --- знімок ринку / зникнення --------------------------------------------------
    def test_gone_only_counted_when_snapshot_complete(self):
        self.run_pass(T0)
        gone_id = self.world[0]["itemId"]
        self.world = self.world[1:]
        # неповний знімок (total > отримано): зникнення НЕ рахуємо
        store, st = self.run_pass(T0 + timedelta(days=3), complete_total=999)
        self.assertEqual(st["gone"], 0)
        self.assertFalse(st["complete"][CAT["query"]])
        # повний знімок: лот зник >= LISTING_GONE_AFTER_DAYS → gone
        store, st = self.run_pass(T0 + timedelta(days=4))
        self.assertGreaterEqual(st["gone"], 1)
        book = next(b for b in store.listings.values() if gone_id in b)
        self.assertEqual(book[gone_id]["status"], "gone")

    # --- міграція стану -------------------------------------------------------------
    def test_v1_state_is_migrated_and_legacy_preserved(self):
        old = {"history": {"t:new:pokemon-tcg": [{"date": "2026-09-01", "price_total": 100.0,
                                                  "item_id": "x", "currency": "EUR"}]},
               "listings": {"t:new:pokemon-tcg": {"x": {"first_seen": "2026-09-01", "status": "active"}}},
               "alerted_item_ids": ["a1"], "discovery_seen": ["d1", "d2"],
               "key_map": {}, "key_query": {"t:new:pokemon-tcg": "Pokemon Booster Box versiegelt"},
               "key_conf": {}}
        with open(config.PRICE_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(old, f)
        store = main.HistoryStore(config.PRICE_HISTORY_FILE)
        self.assertTrue(store.migrated)
        self.assertEqual(store.history, {})
        self.assertEqual(store.alerted, {"a1"})
        self.assertIn("t:new:pokemon-tcg", store.legacy_v1["history"])
        store.save()
        again = main.HistoryStore(config.PRICE_HISTORY_FILE)
        self.assertFalse(again.migrated)
        self.assertIn("t:new:pokemon-tcg", again.legacy_v1["history"])

    def test_state_roundtrip_is_lossless(self):
        store, _ = self.run_pass(T0)
        again = main.HistoryStore(config.PRICE_HISTORY_FILE)
        self.assertEqual(again.history, store.history)
        self.assertEqual(again.seeded, store.seeded)
        self.assertEqual(again.listings, store.listings)


if __name__ == "__main__":
    unittest.main()
