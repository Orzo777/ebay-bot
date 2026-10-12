"""«📦 Де посилки» (12.10): DHL (живий статус), eBay GetOrders (очікувана / фактична доставка), облік — без мережі."""
import os
import sys
import unittest

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import parcels

NS = "urn:ebay:apis:eBLBaseComponents"


class R:
    def __init__(self, js, code=200):
        self._js, self.status_code = js, code

    def json(self):
        return self._js


def dhl(status, delivered=False, when="2026-10-11T10:00:00+02:00", extra=None):
    det = {"sendungsverlauf": {"status": status, "datumAktuellerStatus": when}, "istZugestellt": delivered}
    det.update(extra or {})
    return {"sendungen": [{"sendungsdetails": det}]}


ORDERS = f"""<GetOrdersResponse xmlns="{NS}"><Ack>Success</Ack><OrderArray>
<Order><OrderStatus>Completed</OrderStatus><ShippedTime>2026-10-10T09:00:00.000Z</ShippedTime>
 <TransactionArray><Transaction><Item><ItemID>880007385134</ItemID><Title>Corsair Vengeance DDR4 DIMM RAM 32 GB (2 x 16GB) 2400 MHz</Title></Item>
  <ShippingDetails><ShipmentTrackingDetails><ShipmentTrackingNumber>00340434111111111111</ShipmentTrackingNumber>
  <ShippingCarrierUsed>DHL</ShippingCarrierUsed></ShipmentTrackingDetails></ShippingDetails>
  <ShippingServiceSelected><ShippingPackageInfo><EstimatedDeliveryTimeMin>2026-10-13T00:00:00.000Z</EstimatedDeliveryTimeMin>
  <EstimatedDeliveryTimeMax>2026-10-15T00:00:00.000Z</EstimatedDeliveryTimeMax></ShippingPackageInfo></ShippingServiceSelected>
 </Transaction></TransactionArray></Order>
<Order><OrderStatus>Completed</OrderStatus>
 <TransactionArray><Transaction><Item><ItemID>267809865959</ItemID><Title>SK Hynix32GB (2x16GB) DDR5 5600B MHz RAM Kit für Notebooks</Title></Item>
  <ShippingServiceSelected><ShippingPackageInfo><EstimatedDeliveryTimeMin>2026-10-14T00:00:00.000Z</EstimatedDeliveryTimeMin>
  <EstimatedDeliveryTimeMax>2026-10-16T00:00:00.000Z</EstimatedDeliveryTimeMax></ShippingPackageInfo></ShippingServiceSelected>
 </Transaction></TransactionArray></Order>
</OrderArray></GetOrdersResponse>"""


class ParcelsTest(unittest.TestCase):
    def setUp(self):
        self.dhl = {}
        self.calls = []

    def get(self, url, params=None, **kw):
        self.calls.append(params["piececode"])
        return R(self.dhl.get(params["piececode"], {"sendungen": [{}]}))

    def post(self, url, data=None, headers=None, **kw):
        body = data.decode() if isinstance(data, bytes) else data
        empty = f'<GetOrdersResponse xmlns="{NS}"><Ack>Success</Ack><OrderArray/></GetOrdersResponse>'

        class P:
            status_code, content = 200, (ORDERS if "<OrderRole>Buyer" in body else empty).encode()
        return P()

    def test_dhl_parse(self):
        self.dhl["1"] = dhl("Die Sendung wird zugestellt", extra={"zustellung": {"zustellzeitfensterVon": "2026-10-12T14:00:00+02:00",
                                                                               "zustellzeitfensterBis": "2026-10-12T18:00:00+02:00"}})
        s = parcels.dhl_status("1", self.get)
        self.assertEqual((s["delivered"], s["ready"], s["eta"], s["date"]), (False, False, "12.10 – 12.10", "11.10"))
        self.dhl["2"] = dhl("Die Sendung liegt in der Filiale zur Abholung bereit")
        self.assertTrue(parcels.dhl_status("2", self.get)["ready"])
        self.dhl["3"] = dhl("Abholung in der Filiale ist erfolgt", delivered=True)
        self.assertEqual((parcels.dhl_status("3", self.get)["delivered"], parcels.dhl_status("3", self.get)["ready"]), (True, False))
        self.assertIsNone(parcels.dhl_status("404", self.get))

    def test_report(self):
        import ebay_actions   # noqa: F401 — модулі ebay_list/ebay_actions підтягуються як у робочому процесі
        self.dhl["00340434111111111111"] = dhl("Die Sendung wurde in das Zustellfahrzeug geladen")
        self.dhl["990502112245"] = dhl("Die Sendung liegt in der Filiale zur Abholung bereit")
        self.dhl["00340434000000000001"] = dhl("Die Sendung wurde erfolgreich zugestellt", delivered=True)
        d = {"incoming": [
                {"n": 7, "title": "Corsair Vengeance DDR4 DIMM RAM 32 GB (2 x 16GB) 2400 MHz", "src": "eBay", "id": "880007385134"},
                {"n": 8, "title": "SK Hynix32GB (2x16GB) DDR5 5600B MHz RAM Kit für Notebooks", "src": "eBay", "id": "267809865959"},
                {"n": 3, "title": "Corsair 2x16 DDR4", "src": "Kleinanzeigen", "track": "990502112245", "carrier": "DHL"},
                {"n": 9, "title": "Samsung DDR5 2x8", "src": "Kleinanzeigen"}],
             "outgoing": [{"n": 1, "title": "Corsair Vengeance LPX 32GB", "src": "eBay", "track": "00340434000000000001",
                           "carrier": "DHL"}]}
        t = parcels.report(d, "tok", self.post, self.get)
        lines = t.split("\n")
        self.assertEqual(lines[0], "📦 <b>Посилки</b>")
        self.assertIn("№7", lines[2])
        self.assertIn("🚚 Die Sendung wurde in das Zustellfahrzeug geladen", lines[2])   # трек з eBay → живий DHL
        self.assertIn("№8", lines[3])
        self.assertIn("⏳ ще не відправлено · eBay обіцяє 14.10–16.10", lines[3])
        self.assertIn("📬 чекає у відділенні / Packstation з 11.10 — забери", lines[4])
        self.assertIn("⏳ трек-номера ще немає", lines[5])
        self.assertEqual(lines[6], "⬆️ Від тебе покупцям")
        self.assertIn("✅ доставлено 11.10", lines[7])
        self.assertIn('href="https://www.dhl.de/', t)

    def test_nothing_and_no_ebay(self):
        self.assertIn("немає", parcels.report({"incoming": [], "outgoing": []}, None, self.post, self.get))
        t = parcels.report({"incoming": [{"n": 2, "title": "OWC", "src": "eBay", "track": "H1000000000000000001", "carrier": "Hermes"}],
                            "outgoing": []}, None, self.post, self.get)
        self.assertIn("🚚 в дорозі", t)            # Hermes — без живого статусу, але з посиланням
        self.assertIn("myhermes.de", t)
        self.assertEqual(self.calls, [])          # DHL для Hermes не питаємо


if __name__ == "__main__":
    unittest.main()
