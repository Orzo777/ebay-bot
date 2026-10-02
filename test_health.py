"""Сторож ботів: лічильники, службові сповіщення, щоденний звіт (30.09)."""
import os as _os
_os.environ.setdefault("RAM_PRICES_OFF", "1")   # цифри Terapeak, а не щоденні ціни сторожа (research/ram_prices.json)
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "research"))
import daily_report
import ebay_watch
import health

NOW = datetime(2026, 9, 30, 7, 30, tzinfo=timezone.utc)


class HealthTest(unittest.TestCase):
    def test_bump_totals_prune(self):
        st = {}
        health.bump(st, "cards", 2, NOW - timedelta(hours=30))
        health.bump(st, "cards", 1, NOW - timedelta(hours=3))
        health.bump(st, "cards", 1, NOW)
        health.bump(st, "zero", 0, NOW)
        self.assertEqual(health.totals(st["stats"], 24, NOW), {"cards": 2})
        self.assertEqual(health.totals(st["stats"], 48, NOW), {"cards": 4})
        health.bump(st, "cards", 1, NOW + timedelta(days=9))
        self.assertEqual(len(st["stats"]), 1)

    def test_alert_rate_limit(self):
        st, sent = {}, []
        self.assertTrue(health.alert(st, "k", "a", 6, NOW, sent.append))
        self.assertFalse(health.alert(st, "k", "b", 6, NOW + timedelta(hours=5), sent.append))
        self.assertTrue(health.alert(st, "k", "c", 6, NOW + timedelta(hours=7), sent.append))
        self.assertEqual(sent, ["a", "c"])

    def test_gemini_alert_after_five_failures_in_a_row(self):
        st, sent = {}, []
        for i in range(4):
            health.track_photo(st, {"ok": 0, "fail": i}, {"ok": 0, "fail": i + 1}, NOW, sent.append)
        self.assertEqual(sent, [])
        health.track_photo(st, {"ok": 0, "fail": 0}, {"ok": 1, "fail": 0}, NOW, sent.append)   # успіх обнуляє
        for i in range(5):
            health.track_photo(st, {"ok": 1, "fail": i}, {"ok": 1, "fail": i + 1}, NOW, sent.append)
        self.assertEqual(len(sent), 1)
        self.assertIn("Gemini", sent[0])
        self.assertEqual(health.totals(st["stats"], 24, NOW), {"photo_ok": 1, "photo_fail": 9})


class EbayWatchHealthTest(unittest.TestCase):
    def test_quota_error_alerts_once(self):
        st, sent = {}, []
        ebay_watch.quota_error(st, RuntimeError("Запит … не вдався після 5 спроб (429 Too Many Requests)"), NOW, sent.append)
        ebay_watch.quota_error(st, RuntimeError("429 Too Many Requests"), NOW, sent.append)
        ebay_watch.quota_error(st, RuntimeError("HTTP 404: not found"), NOW, sent.append)
        self.assertEqual(len(sent), 1)
        self.assertIn("квота", sent[0])

    def test_api_down_three_rounds(self):
        st, sent = {}, []
        p = dict(ebay_watch.PHOTO_STATS)
        for _ in range(2):
            ebay_watch.track_round(st, 6, 6, 0, 0, 6, p, NOW, sent.append)
        ebay_watch.track_round(st, 6, 2, 1, 0, 8, p, NOW, sent.append)   # частина запитів пройшла — не збій
        ebay_watch.track_round(st, 6, 6, 0, 0, 6, p, NOW, sent.append)
        self.assertEqual(sent, [])
        ebay_watch.track_round(st, 6, 6, 0, 0, 6, p, NOW, sent.append)
        ebay_watch.track_round(st, 6, 6, 0, 0, 6, p, NOW, sent.append)
        self.assertEqual(len(sent), 1)
        t = health.totals(st["stats"], 24, NOW)
        self.assertEqual((t["ebay_rounds"], t["ebay_cards"], t["ebay_calls"], t["ebay_api_errors"]), (6, 1, 38, 32))

    def test_ka_dispatch_dead_man(self):
        st, sent = {}, []
        day = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)   # 12:00 за Берліном
        ebay_watch.ka_dispatch_check(st, day, sent.append, lambda: day - timedelta(hours=1))
        self.assertEqual(sent, [])
        ebay_watch.ka_dispatch_check(st, day, sent.append, lambda: day - timedelta(hours=5))
        self.assertEqual(sent, [])                       # один раз — ще не тривога (02.10: хибна від застарілого API)
        ebay_watch.ka_dispatch_check(st, day, sent.append, lambda: day - timedelta(minutes=2))
        ebay_watch.ka_dispatch_check(st, day, sent.append, lambda: day - timedelta(hours=5))
        self.assertEqual(sent, [])                       # між ними був свіжий запуск — лічильник скинувся
        ebay_watch.ka_dispatch_check(st, day + timedelta(hours=1), sent.append, lambda: day - timedelta(hours=5))
        self.assertEqual(len(sent), 1)                   # дві перевірки поспіль — тривога
        self.assertIn("6 год", sent[0])
        st2, sent2 = {}, []
        night = datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc)
        ebay_watch.ka_dispatch_check(st2, night, sent2.append, lambda: night - timedelta(hours=8))
        ebay_watch.ka_dispatch_check(st2, day, sent2.append, lambda: None)   # GitHub API недоступний — мовчимо
        self.assertEqual(sent2, [])


def _run(name, start_h_ago, end_h_ago=None, conclusion="success", status="completed", url="u"):
    iso = lambda h: (NOW - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"name": name, "created_at": iso(start_h_ago), "updated_at": iso(end_h_ago if end_h_ago is not None else start_h_ago),
            "conclusion": conclusion, "status": status, "html_url": url}


class DailyReportTest(unittest.TestCase):
    def setUp(self):
        self.ebay, self.ka = {}, {}
        health.bump(self.ebay, "ebay_rounds", 1, NOW - timedelta(hours=23))   # лічильники старші за добу
        health.bump(self.ebay, "ebay_rounds", 450, NOW)
        health.bump(self.ebay, "ebay_cards", 2, NOW)
        health.bump(self.ebay, "ebay_calls", 2300, NOW)
        health.bump(self.ka, "ka_mails", 40, NOW)
        health.bump(self.ka, "ka_cards", 3, NOW)
        # eBay-сторож цілу добу: 24 запуски по годині
        self.runs = [_run("ebay-watch", h + 1, h) for h in range(24)] + [_run("ka-share", 2), _run("ram-mail-alert", 1)]

    def test_all_good(self):
        t = daily_report.build(self.ebay, self.ka, self.runs, NOW)
        self.assertTrue(t.startswith("✅ Все працює"), t)
        self.assertIn("листів 40 → карток 3", t)
        self.assertIn("перевірок 451 (працював 100% часу) → карток 2", t)
        self.assertIn("посилань («поділитися»): 1", t)

    def test_problems(self):
        runs = [_run("ebay-watch", h + 1, h) for h in range(12)] + [
            _run("ka-share", 3, conclusion="failure", url="https://x/1"), _run("ebay-watch", 20, conclusion="cancelled")]
        t = daily_report.build(self.ebay, {}, runs, NOW)
        self.assertTrue(t.startswith("⚠️"), t)
        self.assertIn("лише 50% часу", t)
        self.assertIn("жодного листа від Kleinanzeigen", t)
        self.assertIn("«поділитися»: збоїв 1 (https://x/1)", t)

    def test_first_day_no_false_alarm(self):
        t = daily_report.build({}, {}, [_run("ebay-watch", h + 1, h) for h in range(24)], NOW)
        self.assertTrue(t.startswith("✅"), t)
        self.assertIn("ведуться з сьогодні", t)

    def test_overlapping_runs_not_double_counted(self):
        runs = [_run("ebay-watch", h + 1, h) for h in range(12)] + [_run("ebay-watch", h + 1, h) for h in range(12)]
        self.assertIn("лише 50% часу", daily_report.build(self.ebay, self.ka, runs, NOW))

    def test_running_now_counts_until_now(self):
        runs = [_run("ebay-watch", 0.5, status="in_progress", conclusion=None)] + [_run("ebay-watch", h + 1.5, h + 0.5) for h in range(24)]
        t = daily_report.build(self.ebay, self.ka, runs, NOW)
        self.assertIn("100% часу", t)


if __name__ == "__main__":
    unittest.main()


class LastDispatchQueryTest(unittest.TestCase):
    def test_no_event_filter_and_newest_dispatch(self):
        import requests
        seen = {}

        class R:
            status_code = 200

            def json(self):
                return {"workflow_runs": [{"event": "schedule", "created_at": "2026-10-02T10:30:00Z"},
                                          {"event": "workflow_dispatch", "created_at": "2026-10-02T10:21:27Z"},
                                          {"event": "workflow_dispatch", "created_at": "2026-10-02T05:23:27Z"}]}
        old = requests.get
        requests.get = lambda url, params=None, **k: (seen.update(params=params), R())[1]
        try:
            last = ebay_watch._last_ka_dispatch()
        finally:
            requests.get = old
        self.assertNotIn("event", seen["params"])
        self.assertEqual(last.isoformat(), "2026-10-02T10:21:27+00:00")
