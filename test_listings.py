"""«🏷 Виставлено» (16.10): активні лоти eBay з переглядами / спостерігачами, зіставлення з обліком, KA з обліку — без мережі."""
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, ".")
sys.path.insert(0, "research")
import listings

NS = "urn:ebay:apis:eBLBaseComponents"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
SELLING = f"""<GetMyeBaySellingResponse xmlns="{NS}"><Ack>Success</Ack><ActiveList><ItemArray>
<Item><ItemID>1234567890</ItemID><Title>Corsair Vengeance LPX 32GB (2x16GB) DDR4 2133 CL13</Title>
 <SellingStatus><CurrentPrice currencyID="EUR">175.0</CurrentPrice></SellingStatus><WatchCount>2</WatchCount>
 <ListingDetails><StartTime>2026-10-08T10:00:00.000Z</StartTime><ViewItemURL>https://www.ebay.de/itm/1234567890</ViewItemURL></ListingDetails></Item>
<Item><ItemID>555</ItemID><Title>Kingston Fury 16GB DDR5</Title><BuyItNowPrice currencyID="EUR">80.0</BuyItNowPrice>
 <QuestionCount>1</QuestionCount><ListingDetails><StartTime>2026-10-15T10:00:00.000Z</StartTime></ListingDetails></Item>
</ItemArray></ActiveList></GetMyeBaySellingResponse>"""


class P:
    def __init__(self, xml):
        self.status_code, self.content = 200, xml.encode()


def post(url, data=None, headers=None, **kw):
    body = data.decode() if isinstance(data, bytes) else str(data)
    if "GetMyeBaySelling" in (headers or {}).get("X-EBAY-API-CALL-NAME", "") or "<ActiveList>" in body:
        return P(SELLING)
    hit = "<HitCount>41</HitCount>" if "1234567890" in body else ""
    return P(f'<GetItemResponse xmlns="{NS}"><Ack>Success</Ack><Item>{hit}</Item></GetItemResponse>')


class ListingsTest(unittest.TestCase):
    def test_report(self):
        d = {"rows": [{"n": 4, "title": "2x Corsair Vengeance LPX DDR4 RAM 32GB (2x16GB) 2133MHz", "status": "Виставлено",
                       "note": "виставлено за 189 € 12.10 (eBay 1234567890); ціна знижена до 175 €"},
                      {"n": 9, "title": "G.Skill Fortis 2x16 DDR4 2400", "status": "Виставлено", "note": "на KA"}]}
        text, kb = listings.report(d, "tok", post, NOW)
        self.assertTrue(text.startswith("🏷 <b>Виставлено</b> · 3"))
        self.assertIn("<b>№4 2x Corsair Vengeance LPX DDR4 RAM 32GB</b> · eBay", text)
        self.assertIn("💶 175 € · 8 дн. · 👁 41 · ⭐ 2", text)
        self.assertIn("пропозицію на 5–10 € дешевше", text)          # спостерігачі + тиждень
        self.assertIn("💬 є питання", text)                           # лот без обліку — теж показуємо
        self.assertIn("<b>№9 G.Skill Fortis 2x16 DDR4 2400</b> · KA / вручну", text)
        urls = [b["url"] for row in kb["inline_keyboard"] for b in row]
        self.assertEqual(urls, ["https://www.ebay.de/itm/1234567890", "https://www.ebay.de/itm/555", listings.KA_MINE])

    def test_no_ebay(self):
        text, kb = listings.report({"rows": []}, None, post, NOW)
        self.assertIn("Нічого не виставлено", text)
        self.assertEqual(kb["inline_keyboard"], [])

    def test_tips(self):
        self.assertEqual(listings.tip(3, 10, 0, 0), "")
        self.assertIn("мало переглядів", listings.tip(8, 20, 0, 0))
        self.assertIn("два тижні", listings.tip(15, 200, 0, 0))



class SyncTest(unittest.TestCase):
    """17.10: активні лоти eBay → облік: відомий номер, однозначна назва, схожі кіти — спитати, завершені."""

    ROWS = [dict(n=4, title="2x Corsair Vengeance LPX DDR4 RAM 32GB (2x16GB) 2133MHz CMK32GX4M2A2133C13", status="Виставлено",
                 note="виставлено за 168 € 12.10 (eBay 306000000004)"),
            dict(n=5, title="Corsair Vengeance DDR4 DIMM RAM 32 GB (2x16GB) 2400MHz CMK32GX4M2A2400C16", status="Перевірено", note=""),
            dict(n=6, title="Corsair Vengeance DDR4 DIMM RAM 32 GB (2x16GB) 2400MHz", status="Отримано", note=""),
            dict(n=8, title="G.SKILL FORTIS 32GB (2x16GB) DDR4-2400", status="Перевірено", note="")]

    def test_known_id_price_and_ended(self):
        ch = listings.sync([dict(id="306000000004", title="whatever", price=159.0)], self.ROWS)
        self.assertEqual(ch["lots"], [dict(n=4, id="306000000004", price=159.0)])
        self.assertEqual(ch["ended"], [])
        self.assertEqual(listings.sync([], self.ROWS)["ended"], [dict(n=4, id="306000000004")])

    def test_unique_title_links_similar_asks(self):
        ch = listings.sync([dict(id="306000000008", title="G.Skill Fortis 32GB (2x16GB) DDR4-2400 F4-2400C15D getestet", price=99.0)],
                           self.ROWS)
        self.assertEqual([x["n"] for x in ch["lots"]], [8])
        ch = listings.sync([dict(id="306000000005", title="Corsair Vengeance LPX 32GB (2x16GB) DDR4-2400 getestet", price=175.0)],
                           self.ROWS)
        self.assertEqual(ch["lots"], [])                              # №5 і №6 майже однакові — питаємо
        self.assertEqual(set(ch["ask"][0]["cands"]) >= {5, 6}, True)
        ch = listings.sync([dict(id="306000000009", title="Lego Star Wars 75192", price=500.0)], self.ROWS)
        self.assertEqual((ch["lots"], ch["ask"]), ([], []))           # не з обліку — не питаємо


if __name__ == "__main__":
    unittest.main()
