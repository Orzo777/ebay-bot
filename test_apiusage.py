"""Лічильник викликів Browse API: рахує кожну спробу (включно з 429/5xx), не рахує OAuth,
зберігається в стані і обрізається до 14 днів. Без мережі."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import main


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.reason = "x"
        self.headers = {}
        self._body = body if body is not None else {}
        self.text = ""

    def json(self):
        return self._body


def reset():
    for k in main.API_USAGE:
        main.API_USAGE[k] = 0


class CounterTests(unittest.TestCase):
    def setUp(self):
        reset()
        self._sleep = mock.patch.object(main.time, "sleep", lambda *_: None)
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()

    def test_counts_each_attempt_including_retries(self):
        seq = [FakeResp(429), FakeResp(503), FakeResp(200, {"ok": 1})]
        with mock.patch.object(main.requests, "request", side_effect=seq):
            out = main._request_with_backoff("GET", "https://api.ebay.com/buy/browse/v1/item_summary/search")
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(main.API_USAGE, {"browse": 3, "429": 1, "5xx": 1})

    def test_oauth_and_other_hosts_not_counted(self):
        with mock.patch.object(main.requests, "request", return_value=FakeResp(200, {})):
            main._request_with_backoff("POST", "https://api.ebay.com/identity/v1/oauth2/token")
            main._request_with_backoff("POST", "https://api.telegram.org/botX/sendMessage")
        self.assertEqual(main.API_USAGE["browse"], 0)

    def test_failed_request_still_counted(self):
        with mock.patch.object(main.requests, "request", return_value=FakeResp(400)):
            with self.assertRaises(RuntimeError):
                main._request_with_backoff("GET", "https://api.ebay.com/buy/browse/v1/item/1")
        self.assertEqual(main.API_USAGE["browse"], 1)


class StateTests(unittest.TestCase):
    def test_usage_persists_prunes_and_survives_old_state(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "price_history.json")
            now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
            st = main.HistoryStore(path)
            st.api_usage = {"2026-08-01": 999}                 # застаріле — має зникнути
            st.record_api_usage(now, 120)
            st.record_api_usage(now, 30)                       # накопичується за день
            st.record_api_usage(now - timedelta(days=1), 40)
            st.save()
            st2 = main.HistoryStore(path)
            self.assertEqual(st2.api_usage, {"2026-09-20": 150, "2026-09-19": 40})
            # старий файл стану без поля api_usage завантажується без помилок
            data = json.load(open(path, encoding="utf-8"))
            data.pop("api_usage")
            json.dump(data, open(path, "w", encoding="utf-8"))
            self.assertEqual(main.HistoryStore(path).api_usage, {})

    def test_state_schema_unchanged(self):
        self.assertEqual(main.config.STATE_SCHEMA, 2)


if __name__ == "__main__":
    unittest.main()
