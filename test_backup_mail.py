"""03.11: запасна пошта (research/backup_mail.py) — спить без секретів; Gmail заблоковано → листи KA з запасної скриньки;
той самий лист в обох скриньках — одна картка; eBay-сторож запускає обробку, коли в запасній з'явились нові листи KA."""
import imaplib
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import backup_mail  # noqa: E402
import ram_mail_check as rmc  # noqa: E402
from test_ram_mail_imap import FakeIMAP, mail  # noqa: E402

ENV = ("MAIL_HOST", "MAIL_USER", "MAIL_PASSWORD", "GMAIL_USER", "GMAIL_APP_PASSWORD")


class Box(FakeIMAP):
    """Dovecot (Hostinger): роздільник «.», службові атрибути; uid SEARCH."""
    def __init__(self, mails, uids=None, fail_login=False):
        super().__init__(mails)
        self.uids, self.fail_login, self.selected = uids or [], fail_login, []

    def login(self, *a):
        if self.fail_login:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")

    def list(self):
        return "OK", [b'(\\HasNoChildren) "." INBOX', b'(\\HasNoChildren \\Trash) "." INBOX.Trash',
                      b'(\\HasNoChildren \\Junk) "." INBOX.Spam', b'(\\HasNoChildren \\Sent) "." INBOX.Sent',
                      b'(\\HasNoChildren) "." Projekt']

    def select(self, name, readonly=False):
        self.selected.append(name)
        return "OK", [b"1"]

    def uid(self, cmd, *a):
        return "OK", [" ".join(self.uids).encode()]


class BackupTest(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k, None) for k in ENV}

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_sleeps_without_secrets(self):
        self.assertFalse(backup_mail.configured())
        calls = []
        self.assertEqual(backup_mail.poll({}, datetime.now(timezone.utc), lambda w: calls.append(w) or 204), 0)
        self.assertEqual(calls, [])

    def test_folders_inbox_trash_spam_only(self):
        self.assertEqual(backup_mail.folders(Box([])), ['"INBOX"', '"INBOX.Trash"', '"INBOX.Spam"'])

    def test_poll_dispatches_once_per_new_mail(self):
        os.environ["MAIL_USER"], os.environ["MAIL_PASSWORD"] = "bot@example.org", "x"
        state, calls, now = {}, [], datetime.now(timezone.utc)
        box = Box([], uids=["7", "8"])
        n = backup_mail.poll(state, now, lambda w: calls.append(w) or 204, connect_fn=lambda: box)
        self.assertEqual((n, calls, state["backup_uids"]), (2, ["ram_mail_alert.yml"], ["7", "8"]))
        self.assertEqual(backup_mail.poll(state, now, lambda w: calls.append(w) or 204, connect_fn=lambda: box), 0)
        self.assertEqual(len(calls), 1)
        box.uids = ["7", "8", "9"]
        backup_mail.poll(state, now, lambda w: calls.append(w) or 500, connect_fn=lambda: box)   # GitHub не прийняв —
        self.assertEqual(state["backup_uids"], ["7", "8"])                                    # наступне коло спробує ще

    def test_poll_alerts_after_repeated_login_failures(self):
        os.environ["MAIL_USER"], os.environ["MAIL_PASSWORD"] = "bot@example.org", "x"
        state, sent, now = {}, [], datetime.now(timezone.utc)

        def bad():
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        for _ in range(4):
            backup_mail.poll(state, now, lambda w: 204, connect_fn=bad, send=lambda t, **k: sent.append(t) or True)
        self.assertEqual(sent, [])
        backup_mail.poll(state, now, lambda w: 204, connect_fn=bad, send=lambda t, **k: sent.append(t) or True)
        self.assertEqual(len(sent), 1)
        self.assertIn("Запасна пошта не відкривається", sent[0])


def raw_mail(uid, frm, to, subject, text):
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = frm, to, subject, f"<{uid}@x>"
    msg["Date"] = "Tue, 03 Nov 2026 19:00:00 +0100"
    msg.set_content(text)
    msg.add_alternative(f"<p>{text}</p>", subtype="html")
    return msg.as_bytes()


class RelayBox(Box):
    """uid SEARCH за відправником (і TO, якщо задано) + uid FETCH."""
    def __init__(self, mails):   # {uid: raw}
        super().__init__([])
        self.by_uid, self.searches = mails, []

    def uid(self, cmd, *a):
        if cmd == "SEARCH":
            crit = a[-1]
            self.searches.append(crit)
            frm = crit.split('FROM "')[1].split('"')[0]
            to = crit.split('TO "')[1].split('"')[0] if 'TO "' in crit else ""
            hits = [u for u, raw in self.by_uid.items() if frm in raw.decode(errors="ignore").split("To:")[0]
                    and (not to or to in raw.decode(errors="ignore"))]
            return "OK", [" ".join(hits).encode()]
        return "OK", [(b"1 (BODY[] {1}", self.by_uid[a[0]]), b")"]


class RelayTest(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k, None) for k in ENV + ("MAIL_ALIAS",)}
        os.environ.update(MAIL_USER="team@example.org", MAIL_PASSWORD="x", MAIL_ALIAS="bot@example.org")

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_only_bot_mail_relayed_alerts_without_body(self):
        box = RelayBox({
            "11": raw_mail(11, "eBay <ebay@ebay.de>", "bot@example.org", "Ihre Bestellung ist bestätigt: Kingston", "Gesamtbetrag 95,00 €"),
            "12": raw_mail(12, "PayPal <service@paypal.de>", "team@example.org", "Zahlung erhalten", "інший проєкт"),   # не боту
            "13": raw_mail(13, "Kleinanzeigen <noreply@kleinanzeigen.de>", "bot@example.org", "Neue Treffer zu deiner Suche", "оголошення"),
            "14": raw_mail(14, "DHL <noreply@dhl.de>", "bot@example.org", "Ihre Sendung kommt heute", "00340434161094000001")})
        posted, state = [], {}
        n = backup_mail.relay(state, "k", connect_fn=lambda: box, send_fn=lambda d, k: posted.append(d) or {"relay": len(d["mails"])})
        mails = posted[0]["mails"]
        self.assertEqual(n, 3)
        self.assertEqual([m["subject"] for m in mails], ["Ihre Bestellung ist bestätigt: Kingston", "Neue Treffer zu deiner Suche",
                                                         "Ihre Sendung kommt heute"])
        self.assertIn("Gesamtbetrag 95,00 €", mails[0]["text"])
        self.assertIn("<p>Gesamtbetrag", mails[0]["html"])
        self.assertEqual((mails[1]["text"], mails[1]["html"]), ("", ""))      # лист підписки — лише заголовок
        self.assertTrue(all('TO "bot@example.org"' in c for c in box.searches))
        self.assertEqual(mails[0]["id"], "<11@x>")
        # наступного кола — нічого нового
        self.assertEqual(backup_mail.relay(state, "k", connect_fn=lambda: box, send_fn=lambda d, k: posted.append(d) or {"relay": 0}), 0)
        self.assertEqual(len(posted), 1)

    def test_not_accepted_retried_next_round(self):
        box = RelayBox({"21": raw_mail(21, "eBay <ebay@ebay.de>", "bot@example.org", "Artikel verkauft", "EUR 169,00")})
        state = {}
        self.assertEqual(backup_mail.relay(state, "k", connect_fn=lambda: box, send_fn=lambda d, k: None), 0)
        self.assertEqual(state.get("relay_uids"), None)
        self.assertEqual(backup_mail.relay(state, "k", connect_fn=lambda: box, send_fn=lambda d, k: {"relay": 1}), 1)
        self.assertEqual(state["relay_uids"], ["21"])

    def test_batch_limit(self):
        box = RelayBox({str(30 + i): raw_mail(30 + i, "eBay <ebay@ebay.de>", "bot@example.org", f"Artikel verkauft {i}", "x") for i in range(8)})
        state, posted = {}, []
        backup_mail.relay(state, "k", connect_fn=lambda: box, send_fn=lambda d, k: posted.append(d) or {"relay": 5})
        self.assertEqual(len(posted[0]["mails"]), 5)
        backup_mail.relay(state, "k", connect_fn=lambda: box, send_fn=lambda d, k: posted.append(d) or {"relay": 3})
        self.assertEqual(len(posted[1]["mails"]), 3)

    def test_sleeps_without_secrets_or_key(self):
        self.assertEqual(backup_mail.relay({}, "", connect_fn=lambda: 1 / 0), 0)
        os.environ.pop("MAIL_USER")
        self.assertEqual(backup_mail.relay({}, "k", connect_fn=lambda: 1 / 0), 0)


class RunBothBoxesTest(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k, None) for k in ENV}
        self.cards = []
        self._saved = (rmc.imaplib.IMAP4_SSL, rmc.send_telegram_card, rmc.check_listing, rmc.add_to_card, backup_mail.connect)
        rmc.send_telegram_card = lambda *a: (self.cards.append(a), (1, "{}"))[1]
        rmc.add_to_card = lambda *a, **k: False
        rmc.check_listing = lambda *a: {"level": "low", "score": 0, "reasons": [], "seller": "x", "buy_now": True, "page_price": None}
        self.state = os.path.join(tempfile.mkdtemp(), "s.json")

    def tearDown(self):
        rmc.imaplib.IMAP4_SSL, rmc.send_telegram_card, rmc.check_listing, rmc.add_to_card, backup_mail.connect = self._saved
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_gmail_blocked_backup_still_reads(self):
        # 02.11: Gmail «Invalid credentials» — раніше весь запуск падав; тепер листи KA з запасної скриньки
        os.environ.update(GMAIL_USER="u", GMAIL_APP_PASSWORD="p", MAIL_USER="bot@example.org", MAIL_PASSWORD="x")
        rmc.imaplib.IMAP4_SSL = lambda *a, **k: Box([], fail_login=True)
        box = Box([mail(1)])
        backup_mail.connect = lambda: box
        rmc.run(self.state)
        self.assertEqual(len(self.cards), 1)
        self.assertIn('"INBOX"', box.selected)

    def test_same_mail_in_both_boxes_one_card(self):
        os.environ.update(GMAIL_USER="u", GMAIL_APP_PASSWORD="p", MAIL_USER="bot@example.org", MAIL_PASSWORD="x")
        rmc.imaplib.IMAP4_SSL = lambda *a, **k: FakeIMAP([mail(2)])
        backup_mail.connect = lambda: Box([mail(2)])
        rmc.run(self.state)
        self.assertEqual(len(self.cards), 1)

    def test_all_boxes_fail_is_a_failed_run(self):
        os.environ.update(GMAIL_USER="u", GMAIL_APP_PASSWORD="p")
        rmc.imaplib.IMAP4_SSL = lambda *a, **k: Box([], fail_login=True)
        with self.assertRaises(imaplib.IMAP4.error):
            rmc.run(self.state)


if __name__ == "__main__":
    unittest.main()
