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
        self.assertIn("⏳ трек є лише в KA", lines[5])            # KA шле трек лише в чат — підказка, як додати
        self.assertIn("«трек 9 номер»", lines[5])
        self.assertEqual(lines[6], "⬆️ Від тебе покупцям")
        self.assertIn("✅ доставлено 11.10", lines[7])
        self.assertIn('href="https://www.dhl.de/', t)

    def test_ledger_delivery_beats_ebay_shipped(self):
        r = {"n": 2, "title": "Corsair", "src": "eBay", "delivered": "2026-10-11"}
        o = {"track": ("00340434", "DHL"), "shipped": "2026-10-10T09:00:00.000Z", "eta_min": "", "eta_max": "", "delivered": ""}
        self.assertIn("✅ доставлено 11.10", parcels.line_for(r, o, None, True))

    def test_dhl_official_api(self):
        from unittest import mock

        def get(url, params=None, headers=None, **kw):
            self.assertEqual((url, headers), (parcels.DHL_API, {"DHL-API-Key": "k"}))
            return R({"shipments": [{"status": {"statusCode": "transit", "timestamp": "2026-10-12T09:00:00Z",
                                                "description": "Die Sendung wurde in das Zustellfahrzeug geladen."},
                                     "estimatedDeliveryTimeFrame": {"estimatedFrom": "2026-10-13T08:00:00Z",
                                                                    "estimatedThrough": "2026-10-13T12:00:00Z"}}]})
        with mock.patch.dict(os.environ, {"DHL_API_KEY": " k\n"}):   # пробіл / перенос рядка з копіювання
            s = parcels.dhl_status("00340434111111111111", get)
        self.assertEqual((s["delivered"], s["ready"], s["eta"], s["date"]), (False, False, "13.10 – 13.10", "12.10"))

    def test_17track(self):
        from unittest import mock
        sent = []

        def post(url, json=None, headers=None, **kw):
            sent.append((url.rsplit("/", 1)[1], [x["number"] for x in json], headers["17token"]))
            if url.endswith("/register"):
                return R({"code": 0, "data": {"accepted": [{"number": "53284131401093"}], "rejected": []}})
            return R({"code": 0, "data": {
                "accepted": [{"number": "00340434000000000001", "track_info": {
                    "latest_status": {"status": "Delivered"},
                    "latest_event": {"time_iso": "2026-10-12T11:00:00+02:00", "description": "Zugestellt"}}},
                    {"number": "H1000000000000000001", "track_info": {
                        "latest_status": {"status": "AvailableForPickup"},
                        "latest_event": {"time_iso": "2026-10-13T09:00:00+02:00", "description": "Im PaketShop"}}}],
                "rejected": [{"number": "53284131401093", "error": {"code": -18019902, "message": "does not register"}}]}})

        d = {"incoming": [{"n": 2, "title": "OWC", "src": "eBay", "track": "H1000000000000000001", "carrier": "Hermes"},
                          {"n": 3, "title": "Corsair 2133", "src": "Kleinanzeigen", "track": "53284131401093", "carrier": "Hermes"}],
             "outgoing": [{"n": 1, "title": "Corsair 3200", "src": "eBay", "track": "00340434000000000001", "carrier": "DHL"}]}
        with mock.patch.dict(os.environ, {"TRACK17_KEY": " key\n"}):
            t = parcels.report(d, None, post, self.get)
        self.assertEqual(sent[0], ("gettrackinfo", ["H1000000000000000001", "53284131401093", "00340434000000000001"], "key"))
        self.assertEqual(sent[1], ("register", ["53284131401093"], "key"))   # новий номер — реєструємо один раз
        self.assertIn("📬 чекає у відділенні / Packstation з 13.10 — забери", t)   # Hermes тепер з живим статусом
        self.assertIn("✅ доставлено 12.10", t)
        self.assertIn("№3 Corsair 2133 (Kleinanzeigen) — 🚚 в дорозі", t)     # щойно зареєстрований — поки посилання
        self.assertEqual(self.calls, [])                                       # DHL не питаємо, 17TRACK уже відповів
        self.assertEqual(parcels.t17_statuses(["1"], lambda *a, **k: R({}, 401), "k"), {})   # недійсний ключ — без падіння

    def test_nothing_and_no_ebay(self):
        self.assertIn("немає", parcels.report({"incoming": [], "outgoing": []}, None, self.post, self.get))
        t = parcels.report({"incoming": [{"n": 2, "title": "OWC", "src": "eBay", "track": "H1000000000000000001", "carrier": "Hermes"}],
                            "outgoing": []}, None, self.post, self.get)
        self.assertIn("🚚 в дорозі", t)            # Hermes — без живого статусу, але з посиланням
        self.assertIn("myhermes.de", t)
        self.assertEqual(self.calls, [])          # DHL для Hermes не питаємо


if __name__ == "__main__":
    unittest.main()
