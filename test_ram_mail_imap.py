"""KA-пошта (01.10): повний лист тягнемо лише для нових; обрив Gmail IMAP не губить прогрес і не дублює картки;
ціна на сторінці оголошення важливіша за ціну в листі. Без мережі: IMAP, сторінка і Telegram підмінені."""
import imaplib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import ram_mail_check as rmc


def mail(n, title="2x 16GB Samsung DDR4-3200 RAM Desktop (32GB Kit)", price=80):
    msg = EmailMessage()
    msg["Subject"] = "Neue Treffer zu deiner Suche „ddr4 32gb“"
    msg["Message-ID"] = f"<m{n}@ka>"
    msg["Date"] = format_datetime(datetime.now(timezone.utc) - timedelta(minutes=2))
    msg.set_content(f'<img alt="Bild zur Anzeige {title}"> {price} € VB Von Privat '
                    f'<a href="https://www.kleinanzeigen.de/s-anzeige/35284576{n:02d}?utm=x" title="Anzeige ansehen">x</a>',
                    subtype="html")
    return msg.as_bytes()


class FakeIMAP:
    def __init__(self, mails, fail_at=None):
        self.mails, self.fail_at, self.full = mails, fail_at, []

    def login(self, *a):
        pass

    def list(self):
        return "OK", [b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"']

    def select(self, *a, **k):
        return "OK", [b"1"]

    def search(self, *a):
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.mails)))]

    def fetch(self, ids, what):
        if "HEADER.FIELDS" in what:
            out = []
            for i in ids.split(","):
                hdr = b"Message-ID: " + self.mails[int(i) - 1].split(b"Message-ID: ")[1].split(b"\n")[0] + b"\r\n\r\n"
                out += [(f"{i} (BODY[HEADER.FIELDS (MESSAGE-ID)] {{{len(hdr)}}}".encode(), hdr), b")"]
            return "OK", out
        i = int(ids)
        if self.fail_at == i:
            raise imaplib.IMAP4.abort("command: FETCH => System Error")
        self.full.append(i)
        return "OK", [(b"x", self.mails[i - 1])]

    def logout(self):
        pass


class ImapTest(unittest.TestCase):
    def setUp(self):
        self.cards = []
        self._saved = (rmc.imaplib.IMAP4_SSL, rmc.send_telegram_card, rmc.check_listing, rmc.add_to_card)
        rmc.send_telegram_card = lambda *a: (self.cards.append(a), (1, "{}"))[1]
        rmc.add_to_card = lambda *a, **k: False
        rmc.check_listing = lambda *a: {"level": "low", "score": 0, "reasons": [], "seller": "x", "buy_now": True, "page_price": None}
        os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"] = "u", "p"
        self.state = os.path.join(tempfile.mkdtemp(), "s.json")

    def tearDown(self):
        rmc.imaplib.IMAP4_SSL, rmc.send_telegram_card, rmc.check_listing, rmc.add_to_card = self._saved

    def run_with(self, fake):
        rmc.imaplib.IMAP4_SSL = lambda *a: fake
        rmc.run(self.state)

    def test_only_new_mails_fetched_in_full(self):
        mails = [mail(1, price=300), mail(2, price=300), mail(3, price=300)]
        self.run_with(FakeIMAP(mails))
        fake = FakeIMAP(mails + [mail(4, price=300)])
        self.run_with(fake)
        self.assertEqual(fake.full, [4])

    def test_abort_keeps_progress_and_no_duplicate_cards(self):
        mails = [mail(1), mail(2, title="Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200"), mail(3)]
        with self.assertRaises(SystemExit):
            self.run_with(FakeIMAP(mails, fail_at=3))
        first = len(self.cards)
        self.assertGreaterEqual(first, 1)
        self.assertEqual(len(json.load(open(self.state, encoding="utf-8"))["seen_ids"]), 2)
        self.run_with(FakeIMAP(mails))
        self.assertEqual(len(self.cards), first + 1)   # лише третій лист, перші два — не вдруге

    def test_page_price_wins_over_mail_price(self):
        rmc.check_listing = lambda *a: {"level": "low", "score": 0, "reasons": [], "seller": "x", "buy_now": True, "page_price": 200.0}
        self.run_with(FakeIMAP([mail(1)]))
        self.assertEqual(self.cards, [])   # у листі 80 €, на сторінці вже 200 € — не вигідно
        rmc.check_listing = lambda *a: {"level": "low", "score": 0, "reasons": [], "seller": "x", "buy_now": True, "page_price": 70.0}
        self.run_with(FakeIMAP([mail(1), mail(2)]))
        self.assertEqual(len(self.cards), 1)
        self.assertIn("Продавець змінив ціну: у листі було 80 €, зараз 70 €", self.cards[0][0])


if __name__ == "__main__":
    unittest.main()
