"""Дії в eBay кнопками (10.10): research/ebay_actions.py через sell.ebay_mode. eBay підмінений — відповіді Trading API в XML."""
import os
import sys
import unittest
import xml.etree.ElementTree as ET

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import ebay_actions as ea
import ebay_list as el
import sell

NS = "urn:ebay:apis:eBLBaseComponents"


class R:
    def __init__(self, status=200, body=b"", js=None):
        self.status_code, self.content, self._js = status, body, js

    def json(self):
        return self._js


def resp(call, inner="", ack="Success"):
    return R(body=f'<?xml version="1.0"?><{call}Response xmlns="{NS}"><Ack>{ack}</Ack>{inner}</{call}Response>'.encode())


ORDERS = ("<OrderArray>"
          "<Order><OrderID>O1</OrderID><TransactionArray><Transaction><OrderLineItemID>L1</OrderLineItemID><TransactionID>T1</TransactionID>"
          "<Item><ItemID>111</ItemID><Title>Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200 CMK32GX4M2B3200C16 DIMM getestet</Title>"
          "</Item></Transaction></TransactionArray></Order>"
          "<Order><OrderID>O2</OrderID><ShippedTime>2026-10-08T10:00:00.000Z</ShippedTime><TransactionArray><Transaction>"
          "<TransactionID>T2</TransactionID><Item><ItemID>222</ItemID><Title>OWC 32GB DDR4 SO-DIMM</Title></Item></Transaction>"
          "</TransactionArray></Order></OrderArray>")
ACTIVE = ("<ActiveList><ItemArray><Item><ItemID>111</ItemID><Title>Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200</Title></Item>"
          "<Item><ItemID>333</ItemID><Title>Kingston Fury 2x16GB DDR5</Title><SKU>ledger-4</SKU></Item></ItemArray></ActiveList>")
ITEM = ("<Item><ItemID>111</ItemID><Title>Corsair Vengeance LPX 32GB</Title><StartPrice currencyID=\"EUR\">158.0</StartPrice>"
        "<ListingDetails><BestOfferAutoAcceptPrice currencyID=\"EUR\">147.0</BestOfferAutoAcceptPrice>"
        "<MinimumBestOfferPrice currencyID=\"EUR\">137.0</MinimumBestOfferPrice></ListingDetails></Item>")
QUESTIONS = ("<MemberMessage><MemberMessageExchange><Item><ItemID>111</ItemID><Title>Corsair Vengeance LPX 32GB</Title></Item>"
             "<Question><MessageID>M9</MessageID><SenderID>buyer_77</SenderID><Body>Hallo, laufen die Module auch mit XMP 3200?</Body>"
             "</Question></MemberMessageExchange></MemberMessage>")
OFFERS = ("<ItemBestOffersArray><ItemBestOffers><Item><ItemID>111</ItemID><Title>Corsair Vengeance LPX 32GB</Title></Item>"
          "<BestOfferArray><BestOffer><BestOfferID>B5</BestOfferID><Price currencyID=\"EUR\">140.0</Price><Buyer><UserID>buyer_77</UserID>"
          "</Buyer></BestOffer></BestOfferArray></ItemBestOffers></ItemBestOffersArray>")


class Fake:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail or {}

    def __call__(self, url, headers=None, data=None, files=None, timeout=None):
        if url == el.TOKEN_URL:
            self.calls.append(("token", data))
            return R(js={"access_token": "AT"})
        call = headers["X-EBAY-API-CALL-NAME"]
        self.calls.append((call, data.decode()))
        if call in self.fail:
            return resp(call, f"<Errors><LongMessage>{self.fail[call]}</LongMessage><ErrorCode>1</ErrorCode>"
                              "<SeverityCode>Error</SeverityCode></Errors>", "Failure")
        return resp(call, {"GetOrders": ORDERS, "GetMyeBaySelling": ACTIVE, "GetItem": ITEM, "GetMemberMessages": QUESTIONS,
                           "GetBestOffers": OFFERS}.get(call, ""))

    def body(self, call):
        return ET.fromstring([c[1] for c in self.calls if c[0] == call][-1])


def run(d, fake=None):
    sent, back, fake = [], [], fake or Fake()
    sell.ebay_mode(dict(d, mode="ebay", rt="RT"), "123:ABC", post=fake, send=lambda t, k: sent.append((t, k)) or True,
                   back=lambda x, k: back.append(x) or True)
    return sent, back, fake


def t(root, path):
    return root.findtext("/".join(f"{{{NS}}}{p}" for p in path.split("/")))


class MatchTest(unittest.TestCase):
    def test_pick(self):
        c = [{"item_id": "1", "title": "Corsair Vengeance 32GB DDR4", "sku": ""}, {"item_id": "2", "title": "Kingston DDR5", "sku": "ledger-4"}]
        self.assertEqual(ea.pick(c, item_id="2")["item_id"], "2")
        self.assertEqual(ea.pick(c, row=4)["item_id"], "2")
        self.assertEqual(ea.pick(c, title="Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200")["item_id"], "1")
        self.assertIsNone(ea.pick(c, title="PlayStation 5 Slim"))

    def test_thresholds_keep_proportions(self):
        self.assertEqual(ea.new_thresholds({"price": 158, "accept": 147, "decline": 137}, 140), {"item": 140, "accept": 130, "decline": 121})
        self.assertEqual(ea.new_thresholds({"price": 158}, 140), {"item": 140})
        self.assertEqual(ea.counter_price(140, {"price": 158, "accept": 147}), 149)   # середина 149 ≥ поріг 147
        self.assertEqual(ea.counter_price(150, {"price": 152, "accept": 151}), 151)


class ActionsTest(unittest.TestCase):
    def test_ship_tracking(self):
        sent, _, f = run({"action": "ship", "row": 2, "title": "Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200", "track": "00340434161234567890",
                          "carrier": "DHL"})
        b = f.body("CompleteSale")
        self.assertEqual([t(b, "ItemID"), t(b, "TransactionID"), t(b, "Shipped")], ["111", "T1", "true"])
        self.assertEqual(t(b, "Shipment/ShipmentTrackingDetails/ShipmentTrackingNumber"), "00340434161234567890")
        self.assertEqual(t(b, "Shipment/ShipmentTrackingDetails/ShippingCarrierUsed"), "DHL")
        self.assertIn("Versendet", sent[0][0])

    def test_ship_no_order(self):
        sent, _, f = run({"action": "ship", "row": 9, "title": "PlayStation 5 Slim", "track": "H1000000000001", "carrier": "Hermes"})
        self.assertNotIn("CompleteSale", [c[0] for c in f.calls])
        self.assertIn("вручну", sent[0][0])

    def test_revise_price(self):
        sent, back, f = run({"action": "revise", "row": 2, "title": "Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200", "price": 140})
        b = f.body("ReviseFixedPriceItem")
        self.assertEqual([t(b, "Item/ItemID"), float(t(b, "Item/StartPrice")), float(t(b, "Item/ListingDetails/BestOfferAutoAcceptPrice")),
                          float(t(b, "Item/ListingDetails/MinimumBestOfferPrice"))], ["111", 140.0, 130.0, 121.0])
        self.assertEqual(back, [{"kind": "repriced", "row": 2, "price": 140, "item_id": "111"}])
        self.assertIn("158 → <b>140 €</b>", sent[0][0])

    def test_revise_never_raises_price(self):
        sent, back, f = run({"action": "revise", "row": 2, "title": "Corsair Vengeance LPX 32GB", "price": 170})
        self.assertNotIn("ReviseFixedPriceItem", [c[0] for c in f.calls])
        self.assertEqual(back, [])

    def test_revise_by_sku(self):
        sent, back, f = run({"action": "revise", "row": 4, "title": "щось інше", "price": 100})
        self.assertEqual(t(f.body("GetItem"), "ItemID"), "333")

    def test_answer_question(self):
        sent, _, f = run({"action": "answer", "title": "Corsair Vengeance LPX 32GB", "question": "laufen die Module auch mit XMP 3200?",
                          "text": "Hallo, ja, XMP 3200 läuft stabil. Viele Grüße"})
        b = f.body("AddMemberMessageRTQ")
        self.assertEqual([t(b, "ItemID"), t(b, "MemberMessage/ParentMessageID"), t(b, "MemberMessage/RecipientID")], ["111", "M9", "buyer_77"])
        self.assertIn("XMP 3200 läuft", t(b, "MemberMessage/Body"))
        self.assertIn("надіслано", sent[0][0])

    def test_offer_accept_counter_decline(self):
        sent, _, f = run({"action": "offer", "op": "acc", "amount": 140, "title": "Corsair Vengeance LPX 32GB"})
        b = f.body("RespondToBestOffer")
        self.assertEqual([t(b, "ItemID"), t(b, "BestOfferID"), t(b, "Action")], ["111", "B5", "Accept"])
        sent, _, f = run({"action": "offer", "op": "cnt", "amount": 140, "title": "Corsair Vengeance LPX 32GB"})
        b = f.body("RespondToBestOffer")
        self.assertEqual([t(b, "Action"), float(t(b, "CounterOfferPrice"))], ["Counter", 149.0])
        self.assertIn("149 €", sent[0][0])
        sent, _, f = run({"action": "offer", "op": "dec", "amount": 140, "title": "Corsair Vengeance LPX 32GB"})
        self.assertEqual(t(f.body("RespondToBestOffer"), "Action"), "Decline")

    def test_offer_gone(self):
        sent, _, f = run({"action": "offer", "op": "acc", "amount": 99, "title": "Xbox Series X"})
        self.assertNotIn("RespondToBestOffer", [c[0] for c in f.calls])
        self.assertIn("Не знайшов", sent[0][0])

    def test_ebay_error_reported(self):
        sent, back, f = run({"action": "revise", "row": 2, "title": "Corsair Vengeance LPX 32GB", "price": 140},
                            Fake(fail={"ReviseFixedPriceItem": "Price too low"}))
        self.assertIn("Price too low", sent[0][0])
        self.assertEqual(back, [])


if __name__ == "__main__":
    unittest.main()
