"""Сторож сервісів (11.10): Gemini, GitHub, eBay, Telegram — без мережі (підставні відповіді)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, ".")
sys.path.insert(0, "research")
import service_watch as sw


class R:
    def __init__(self, code=200, js=None, text=""):
        self.status_code, self._js, self.text = code, js, text

    def json(self):
        return self._js


def fake_get(models=("gemini-flash-lite-latest", "gemini-flash-latest", "gemini-3.5-flash", "gemini-3.6-flash"),
             annotations=(), tg="Bot API 9.4"):
    def get(url, **kw):
        if url == sw.GEMINI:
            return R(200, {"models": [{"name": "models/" + m} for m in models]})
        if url.startswith(sw.GEMINI + "/"):
            return R(404, {})
        if url.endswith("/actions/runs"):
            return R(200, {"workflow_runs": [
                {"name": "tests", "status": "completed", "jobs_url": "J1"},
                {"name": "tests", "status": "completed", "jobs_url": "J0"},   # старіший запуск того ж процесу — пропускаємо
                {"name": "ebay-watch", "status": "in_progress", "jobs_url": "J2"}]})
        if url == "J1":
            return R(200, {"jobs": [{"check_run_url": "C1"}]})
        if url == "C1/annotations":
            return R(200, [{"message": m} for m in annotations])
        if "telegram" in url:
            return R(200, text=f"<h4>Recent changes</h4><p>{tg}</p><p>Bot API 9.3</p>")
        raise AssertionError(url)
    return get


ENV = {"GEMINI_API_KEY": "k", "GITHUB_REPOSITORY": "o/r", "GH_API_TOKEN": "t"}


class ServiceWatchTest(unittest.TestCase):
    def test_first_run_quiet_and_weekly(self):
        data = {}
        with mock.patch.dict(os.environ, ENV):
            self.assertEqual(sw.update(data, "2026-10-11", fake_get(), lambda: {}), [])
            self.assertEqual(data["services"]["gemini_newest"], "gemini-3.6-flash")
            self.assertEqual(data["services"]["telegram"], "9.4")
            self.assertEqual(sw.update(data, "2026-10-14", fake_get(models=()), lambda: {}), [])   # ще не тиждень
        self.assertIn("нових попереджень немає", sw.lines(data)[0])

    def test_alerts_only_for_new_breaking_things(self):
        data = {}
        ann = ["Node.js 20 actions are deprecated. Please update actions/checkout@v4", "Process completed with exit code 1."]
        with mock.patch.dict(os.environ, ENV):
            sw.update(data, "2026-10-01", fake_get(), lambda: {})
            out = sw.update(data, "2026-10-08", fake_get(models=("gemini-flash-latest", "gemini-3.7-flash"), annotations=ann,
                                                         tg="Bot API 9.5"),
                            lambda: {"warnings": [{"message": "The compatibility level 1100 is deprecated."}]})
        self.assertEqual(len(out), 1)
        t = out[0]
        self.assertIn("gemini-flash-lite-latest", t)          # зник псевдонім — може зламатись
        self.assertIn("Node.js 20 actions are deprecated", t)
        self.assertNotIn("exit code", t)                     # звичайні помилки — не сюди
        self.assertIn("compatibility level 1100", t)
        self.assertNotIn("<b>", t)                           # службові повідомлення — простим текстом
        info = "\n".join(sw.lines(data))
        self.assertIn("gemini-3.7-flash", info)
        self.assertIn("Telegram Bot API 9.5", info)
        with mock.patch.dict(os.environ, ENV):               # те саме через тиждень — без повторного поштовху про GitHub/eBay
            out = sw.update(data, "2026-10-15", fake_get(annotations=ann), lambda: {"warnings": [
                {"message": "The compatibility level 1100 is deprecated."}]})
        self.assertEqual(out, [])

    def test_failures_do_not_crash(self):
        def boom(url, **kw):
            raise ConnectionError
        with mock.patch.dict(os.environ, ENV):
            out = sw.update({}, "2026-10-11", boom, lambda: 1 / 0)
        self.assertEqual(len(out), 1)
        self.assertIn("Gemini: ConnectionError", out[0])


if __name__ == "__main__":
    unittest.main()
