"""03.11: стан Apps Script ззовні (research/as_health.py) — зашифрований запит, відповідь через перенаправлення 302."""
import json
import os
import sys
import unittest

os.environ["RAM_PRICES_OFF"] = "1"   # як у test_sell: модулі цін — зі статичними цінами (інакше інші тести бачать живі)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "research"))
import as_health  # noqa: E402
import sell  # noqa: E402


class R:
    def __init__(self, status=200, js=None, headers=None):
        self.status_code, self._js, self.headers = status, js, headers or {}

    def json(self):
        if self._js is None:
            raise ValueError("not json")
        return self._js


class HealthTest(unittest.TestCase):
    def test_sealed_request_and_redirect(self):
        posted, got = [], []

        def post(url, data=None, headers=None, timeout=None, allow_redirects=None):
            posted.append(json.loads(data)["sealed"])
            return R(302, headers={"Location": "https://script.googleusercontent.com/echo?x=1"})

        def get(url, timeout=None):
            got.append(url)
            return R(js={"triggers": ["check", "watchdog"], "check": 3, "ledger": 650, "watchdog": -1, "stale": True,
                         "healed": ["check", "processLedger"]})

        res = as_health.ask("https://script.google.com/macros/s/X/exec", "123:ABC", post=post, get=get)
        s = posted[0]
        self.assertEqual(sell.unseal(s["blob"], s["mac"], "123:ABC", s["nonce"]), {"kind": "health", "heal": 1})
        self.assertEqual(got, ["https://script.googleusercontent.com/echo?x=1"])
        self.assertEqual(as_health.line(res), "тригери: check, watchdog; пошта KA (check) — 3 хв, облік — 650 хв, сторож — —; "
                                              "перевстановив: check, processLedger")

    def test_old_version_or_busy(self):
        post = lambda url, **k: R(200)   # стара версія Apps Script: HTML «ok», не JSON
        self.assertIsNone(as_health.ask("https://script.google.com/x", "k", post=post, get=None))
        post = lambda url, **k: R(200, js={"ok": 1})
        self.assertIsNone(as_health.ask("https://script.google.com/x", "k", post=post, get=None))

    def test_webapp_url_only_apps_script(self):
        get = lambda url, timeout=None: R(js={"result": {"url": "https://evil.example/x"}})
        self.assertEqual(as_health.webapp_url("k", get=get), "")
        get = lambda url, timeout=None: R(js={"result": {"url": "https://script.google.com/macros/s/X/exec?bot=office"}})
        self.assertEqual(as_health.webapp_url("k", get=get), "https://script.google.com/macros/s/X/exec?bot=office")


if __name__ == "__main__":
    unittest.main()
