"""Порядок у повідомленнях eBay (research/ebay_messages.py, 31.10) — без мережі. Кейси з «Posteingang» користувача 31.10:
mr.wombat (№5, відправлено сьогодні) — лишається; berwald08 (продаж 9.10, давно відправлено) — у «Verkauft – erledigt»;
системні eBay — у «eBay»; неоплачена покупка — лишається."""
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, ".")
sys.path.insert(0, "research")
import ebay_messages as em  # noqa: E402

NOW = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)
SALES = [dict(item_id="336849011147", buyer="mr.wombat", shipped=True, shipped_at="2026-10-31"),
         dict(item_id="336831111111", buyer="berwald08", shipped=True, shipped_at="2026-10-10")]
BUYS = [dict(item_id="2001", seller="brezn77", created="2026-10-05T10:00:00Z", paid=True),
        dict(item_id="2002", seller="neu_seller", created="2026-10-02T10:00:00Z", paid=False)]
MSGS = [dict(id="1", sender="i1_dev", to="mr.wombat", subject="Corsair", item_id="336849011147", date="2026-10-31T14:40:00Z"),
        dict(id="2", sender="i1_dev", to="berwald08", subject="Corsair 3200", item_id="336831111111", date="2026-10-31T14:30:00Z"),
        dict(id="3", sender="eBay", to="i1_dev", subject="Ihr Konto wird mit einem neuen Gerät genutzt", item_id="", date="2026-10-31T14:00:00Z"),
        dict(id="4", sender="brezn77", to="i1_dev", subject="G.SKILL", item_id="2001", date="2026-10-12T10:00:00Z"),
        dict(id="5", sender="eBay", to="i1_dev", subject="Bitte bezahlen Sie Ihren Artikel", item_id="2002", date="2026-10-30T10:00:00Z"),
        dict(id="6", sender="luan9999", to="i1_dev", subject="G.SKILL FORTIS", item_id="336849477931", date="2026-10-31T00:00:00Z"),
        dict(id="7", sender="frager", to="i1_dev", subject="Frage", item_id="336844624233", date="2026-10-20T00:00:00Z")]


class TestClassify(unittest.TestCase):
    def test_inbox_keeps_only_active(self):
        plan = em.classify(MSGS, SALES, BUYS, NOW)
        self.assertEqual(sorted(plan["keep"]), ["1", "5", "6"])   # покупець №5, неоплачена покупка, свіже питання
        self.assertEqual(plan["sold"], ["2"])                      # berwald08 — продаж давно закритий
        self.assertEqual(plan["ebay"], ["3"])
        self.assertEqual(plan["bought"], ["4"])                    # покупка 5.10 — понад 3 тижні
        self.assertEqual(plan["other"], ["7"])                     # старе питання не від покупця

    def test_sent_messages_follow_the_deal_31_10(self):
        # розмова berwald08 лишалась у «Posteingang» через моє надіслане «Entschuldigung…» — його теж у «Verkauft – erledigt»
        sent = [dict(MSGS[1], id="s2", sent=True), dict(MSGS[0], id="s1", sent=True)]
        plan = em.classify(sent, SALES, BUYS, NOW)
        self.assertEqual((plan["sold"], plan["keep"]), (["s2"], ["s1"]))

    def test_active_sale_guard(self):
        self.assertTrue(em.active_sale(SALES[0], NOW))
        self.assertFalse(em.active_sale(SALES[1], NOW))           # 9.10 → 21 день: подяку більше не пишемо
        self.assertTrue(em.active_sale(dict(shipped=False), NOW))

    def test_tidy_moves_in_batches_of_ten(self):
        calls = []
        box = lambda tok, post, days=120, folder=0: [dict(MSGS[2], id=str(i)) for i in range(23)] if folder == 0 else []   # noqa: E731
        with mock.patch.object(em, "inbox", side_effect=box), \
                mock.patch.object(em, "purchases", return_value=[]), \
                mock.patch.object(em, "ensure_folders", return_value={"ebay": "77", "sold": "78", "bought": "79", "other": "80"}), \
                mock.patch.object(em.el, "trading", side_effect=lambda call, body, tok, post: calls.append((call, body)) or
                                  em.el.ET.fromstring('<R xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack></R>')):
            moved = em.tidy("tok", None, SALES)
        self.assertEqual(moved, {"ebay": 23})
        self.assertEqual([c[0] for c in calls], ["ReviseMyMessages"] * 3)
        self.assertIn("<FolderID>77</FolderID>", calls[0][1])


if __name__ == "__main__":
    unittest.main()
