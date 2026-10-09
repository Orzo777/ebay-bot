"""Автопублікація на eBay (09.10): research/ebay_list.py + sell.ebay_mode. Без мережі — eBay і Telegram підмінені."""
import json
import os
import sys
import unittest
import xml.etree.ElementTree as ET

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import ebay_list as el
import sell

NS = "urn:ebay:apis:eBLBaseComponents"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
# той самий вектор, що в tools/test_publish.js: Python шифрує (post_back) — Apps Script розшифровує (sealedPost)
PY_VECTOR = ("0c8A4OJb57wcp3jldRyU2WGjqaHFc/fY8aoTHvgmlJJqabu2mlrMKIAOPlI/JAOifwxfrtw2zL8pfxBWURIS4Kb9",
             "f184639636b9b2f66b20581ff6e831cc")


class R:
    def __init__(self, status=200, body=b"", js=None):
        self.status_code, self.content, self._js = status, body, js

    def json(self):
        if self._js is None:
            raise ValueError
        return self._js


def xml_resp(call, inner="", ack="Success"):
    return R(body=f'<?xml version="1.0"?><{call}Response xmlns="{NS}"><Ack>{ack}</Ack>{inner}</{call}Response>'.encode())


def err(msg, code, sev="Error"):
    return f"<Errors><ShortMessage>{msg}</ShortMessage><LongMessage>{msg}</LongMessage><ErrorCode>{code}</ErrorCode><SeverityCode>{sev}</SeverityCode></Errors>"


class FakeEbay:
    """Підмінений requests.post: токени, фото, Verify/Add. verify_errors / add_errors — що поверне eBay."""

    def __init__(self, verify_inner="", add_inner="<ItemID>1234567890</ItemID>", token_status=200):
        self.calls, self.verify_inner, self.add_inner, self.token_status = [], verify_inner, add_inner, token_status

    def __call__(self, url, headers=None, data=None, files=None, timeout=None):
        if url == el.TOKEN_URL:
            self.calls.append(("token", data))
            if self.token_status != 200:
                return R(self.token_status, js={"error": "invalid_grant", "error_description": "the provided authorization grant is invalid"})
            return R(js={"access_token": "AT", "refresh_token": "RT", "refresh_token_expires_in": 47304000})
        call = headers["X-EBAY-API-CALL-NAME"]
        self.calls.append((call, data if data is not None else files))
        if call == "UploadSiteHostedPictures":
            n = sum(1 for c in self.calls if c[0] == call)
            return xml_resp(call, f"<SiteHostedPictureDetails><FullURL>https://i.ebayimg.com/p{n}.jpg</FullURL></SiteHostedPictureDetails>")
        fees = "<Fees><Fee><Name>ListingFee</Name><Fee currencyID=\"EUR\">0.0</Fee></Fee></Fees>"
        if call == "VerifyAddFixedPriceItem":
            return xml_resp(call, fees + self.verify_inner, "Failure" if "<SeverityCode>Error" in self.verify_inner else "Success")
        return xml_resp(call, fees + self.add_inner)

    def item(self, call="VerifyAddFixedPriceItem"):
        body = [c[1] for c in self.calls if c[0] == call][-1].decode()
        return ET.fromstring(body).find(f"{{{NS}}}Item")


def t(item, path):
    return item.findtext("/".join(f"{{{NS}}}{p}" for p in path.split("/")))


class AuthTest(unittest.TestCase):
    def test_consent_url_and_code(self):
        u = el.consent_url("APP-ID", "Name-RuName-x")
        self.assertIn("client_id=APP-ID", u)
        self.assertIn("redirect_uri=Name-RuName-x", u)
        self.assertIn("sell.inventory", u)
        pasted = ("https://signin.ebay.de/ws/eBayISAPI.dll?ThirdPartyAuthSucessFailure&isAuthSuccessful=true"
                  "&code=v%5E1.1%23i%5E1%23f%5E0%23r%5E1%23I%5E3%23p%5E3%23t%5EUl41&expires_in=299")
        self.assertEqual(el.code_from(pasted), "v^1.1#i^1#f^0#r^1#I^3#p^3#t^Ul41")
        self.assertEqual(el.code_from("v^1.1#i^1#abc"), "v^1.1#i^1#abc")
        self.assertIsNone(el.code_from("привіт"))

    def test_exchange_and_expired(self):
        fake = FakeEbay()
        self.assertEqual(el.exchange_code("C", "A", "S", "RU", fake), ("RT", 47304000))
        self.assertEqual(fake.calls[0][1]["redirect_uri"], "RU")
        with self.assertRaises(el.AuthError):
            el.exchange_code("C", "A", "S", "RU", FakeEbay(token_status=400))

    def test_python_vector_for_apps_script(self):
        self.assertEqual(sell.seal({"kind": "listed", "row": 3, "price": 96, "item_id": "1234567890"}, "123:ABC", "n0nce2"), PY_VECTOR)


class ItemXmlTest(unittest.TestCase):
    def test_ram_item(self):
        title = "Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200 CMK32GX4M2B3200C16"
        p = sell.prepare(title, 70.63, 1, lambda r: [])
        ip = sell.item_prices(p["pr"], p["tx"]["ship"])
        fake = FakeEbay()
        res = el.verify_and_add("ram", p["tx"], ip, ["https://i/1.jpg", "https://i/2.jpg"], "ledger-1", "AT", fake, dry=False)
        self.assertEqual(res["item_id"], "1234567890")
        self.assertEqual([c[0] for c in fake.calls], ["VerifyAddFixedPriceItem", "AddFixedPriceItem"])
        it = fake.item("AddFixedPriceItem")
        self.assertEqual(t(it, "Title"), p["tx"]["title"])
        self.assertEqual(t(it, "PrimaryCategory/CategoryID"), "170083")
        self.assertEqual(t(it, "ConditionID"), "3000")
        self.assertEqual(float(t(it, "StartPrice")), ip["item"])
        self.assertEqual(float(t(it, "ShippingDetails/ShippingServiceOptions/ShippingServiceCost")), 6.49)
        self.assertEqual(t(it, "ShippingDetails/ShippingServiceOptions/ShippingService"), "DE_DHLPaket")
        self.assertEqual(t(it, "ReturnPolicy/ReturnsAcceptedOption"), "ReturnsNotAccepted")
        self.assertEqual(float(t(it, "ListingDetails/BestOfferAutoAcceptPrice")), ip["accept"])
        self.assertEqual(float(t(it, "ListingDetails/MinimumBestOfferPrice")), ip["decline"])
        spec = {n.findtext(f"{{{NS}}}Name"): n.findtext(f"{{{NS}}}Value") for n in it.iter(f"{{{NS}}}NameValueList")}
        self.assertEqual(spec["Marke"], "Corsair")
        self.assertEqual(spec["Herstellernummer"], "CMK32GX4M2B3200C16")
        self.assertNotIn("Speicher-Eigenschaften", spec)   # «—» не надсилаємо (не ECC)
        self.assertEqual(len(list(it.iter(f"{{{NS}}}PictureURL"))), 2)
        self.assertIn("<br>", t(it, "Description"))         # опис — HTML з переносами

    def test_console_aspects_match_taxonomy(self):
        p = sell.prepare("Sony PS5 Slim Disc 1TB mit Controller", 300, 4, lambda r: [])
        names = dict(el.specifics(p["tx"]))
        self.assertEqual(names["Regionalcode"], "PAL")
        self.assertEqual(names["Produktart"], "Heimkonsole")
        self.assertNotIn("Region", names)
        p = sell.prepare("Xbox Series X 1TB", 380, 5, lambda r: [])
        self.assertEqual(dict(el.specifics(p["tx"]))["Plattform"], "Microsoft Xbox Series X|S")

    def test_verify_errors_stop_before_add(self):
        p = sell.prepare("Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200", 70, 1, lambda r: [])
        ip = sell.item_prices(p["pr"], p["tx"]["ship"])
        fake = FakeEbay(verify_inner=err("Item specific Marke is missing.", "21919303") + err("Picture small", "21919137", "Warning"))
        res = el.verify_and_add("ram", p["tx"], ip, ["https://i/1.jpg"], "ledger-1", "AT", fake, dry=False)
        self.assertFalse(res["ok"])
        self.assertEqual([c[0] for c in fake.calls], ["VerifyAddFixedPriceItem"])
        self.assertIn("Marke is missing", res["errors"][0])
        self.assertEqual(len(res["warnings"]), 1)

    def test_fee_parts_and_known_warnings(self):
        root = ET.fromstring(f'<R xmlns="{NS}"><Fees><Fee><Name>InsertionFee</Name><Fee>0.5</Fee></Fee>'
                             '<Fee><Name>ListingFee</Name><Fee>0.5</Fee></Fee><Fee><Name>GalleryFee</Name><Fee>0.0</Fee></Fee></Fees></R>')
        self.assertEqual(el.fee_parts(root), {"InsertionFee": 0.5})
        ua = el.explain(["Final Value Fee waived. [21920376]", "Geldbeträge … werden als einbehalten angezeigt. []", "Other [1]"])
        self.assertIn("0 €", ua[0])
        self.assertIn("притримати", ua[1])
        self.assertEqual(ua[2], "Other [1]")

    def test_dry_fee_variants(self):
        p = sell.prepare("Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200", 70, 1, lambda r: [])
        ip = sell.item_prices(p["pr"], p["tx"]["ship"])
        calls = []

        def post(url, headers=None, data=None, **k):
            body = data.decode()
            calls.append(body)
            fee = "0.0" if "Days_30" in body else "0.5"
            return xml_resp("VerifyAddFixedPriceItem", f"<Fees><Fee><Name>ListingFee</Name><Fee>{fee}</Fee></Fee></Fees>")
        res = el.verify_and_add("ram", p["tx"], ip, ["https://i/1.jpg"], "ledger-1", "AT", post, dry=True)
        self.assertEqual(res["fee"], 0.5)
        self.assertEqual(res["variants"], {"30 днів замість безстрокового": 0.0, "без Preisvorschlag": 0.5, "30 днів і без Preisvorschlag": 0.0})
        self.assertNotIn("BestOfferDetails", calls[2])
        self.assertFalse(any("AddFixedPriceItemRequest" in c and "Verify" not in c for c in calls))   # нічого не опубліковано

    def test_token_errors_raise_auth(self):
        with self.assertRaises(el.AuthError):
            el.trading("VerifyAddFixedPriceItem", "<Item/>", "AT", lambda *a, **k: xml_resp("VerifyAddFixedPriceItem", err("Invalid token", "931"), "Failure"))


class EbayModeTest(unittest.TestCase):
    def run_mode(self, d, fake=None, files=None):
        sent, back = [], []
        fake = fake or FakeEbay()
        os.environ["EBAY_RUNAME"] = "RU"
        ok = sell.ebay_mode(d, "123:ABC", post=fake, file_fn=lambda fid, key: (files or {}).get(fid, JPEG),
                            send=lambda text, kb: sent.append((text, kb)) or True, back=lambda data, key: back.append(data) or True,
                            comp_fn=lambda r: [])
        return ok, sent, back, fake

    def test_auth_url(self):
        ok, sent, _, _ = self.run_mode({"mode": "auth_url"})
        self.assertIn("redirect_uri=RU", sent[0][1]["inline_keyboard"][0][0]["url"])

    def test_auth_code_goes_back_to_apps_script(self):
        ok, sent, back, _ = self.run_mode({"mode": "auth_code", "code": "https://signin.ebay.de/x?isAuthSuccessful=true&code=v%5E1.1%23abc&expires_in=299"})
        self.assertEqual(back, [{"kind": "ebay_rt", "rt": "RT", "exp": 47304000}])
        self.assertEqual(sent, [])   # повідомлення «підключено» шле Apps Script, коли збереже ключ

    def test_expired_code(self):
        ok, sent, back, _ = self.run_mode({"mode": "auth_code", "code": "code=v%5E1.1%23abc"}, FakeEbay(token_status=400))
        self.assertIn("5 хвилин", sent[0][0])
        self.assertEqual(back, [])

    def test_publish_full(self):
        d = {"mode": "publish", "row": 1, "title": "Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200 CMK32GX4M2B3200C16", "cost": 70.63,
             "rt": "RT", "photos": [{"id": "P1", "t": "photo"}, {"id": "P2", "t": "photo"}, {"id": "D1", "t": "document"}]}
        ok, sent, back, fake = self.run_mode(d, files={"D1": b"<html>memtest</html>"})   # не картинка — пропускаємо
        self.assertEqual([c[0] for c in fake.calls], ["token", "UploadSiteHostedPictures", "UploadSiteHostedPictures",
                                                      "VerifyAddFixedPriceItem", "AddFixedPriceItem"])
        self.assertEqual(back[0]["kind"], "listed")
        self.assertEqual(back[0]["item_id"], "1234567890")
        self.assertIn("https://www.ebay.de/itm/1234567890", json.dumps(sent[0][1]))
        self.assertIn("виставлено на eBay", sent[0][0])

    def test_publish_dry_and_errors(self):
        d = {"mode": "publish", "dry": True, "row": 1, "title": "Kingston 2x16GB DDR4 3200", "cost": 50, "rt": "RT",
             "photos": [{"id": "P1", "t": "photo"}]}
        ok, sent, back, fake = self.run_mode(d)
        self.assertNotIn("AddFixedPriceItem", [c[0] for c in fake.calls])
        self.assertIn("Нічого не опубліковано", sent[0][0])
        self.assertIn("без комісії", sent[0][0])
        self.assertEqual(back, [])
        ok, sent, back, fake = self.run_mode(dict(d, dry=False), FakeEbay(verify_inner=err("Bad thing", "1")))
        self.assertIn("не прийняв", sent[0][0])
        self.assertEqual(back, [])

    def test_publish_revoked_key(self):
        d = {"mode": "publish", "row": 1, "title": "Kingston 2x16GB DDR4 3200", "cost": 50, "rt": "RT", "photos": [{"id": "P1", "t": "photo"}]}
        ok, sent, back, fake = self.run_mode(d, FakeEbay(token_status=400))
        self.assertEqual(back, [{"kind": "ebay_bad"}])
        self.assertIn("ebay вхід", sent[0][0])

    def test_publish_no_photos(self):
        d = {"mode": "publish", "row": 1, "title": "Kingston 2x16GB DDR4 3200", "cost": 50, "rt": "RT", "photos": [{"id": "D1", "t": "document"}]}
        ok, sent, back, fake = self.run_mode(d, files={"D1": b"not an image"})
        self.assertIn("немає фото", sent[0][0])
        self.assertNotIn("VerifyAddFixedPriceItem", [c[0] for c in fake.calls])

    def test_card_button_only_when_connected(self):
        t_ = "Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200"
        _, kb, _ = sell.make(t_, 70, 1, lambda r: [], ebay=True)
        self.assertIn("c|авто|1", json.dumps(kb, ensure_ascii=False))
        _, kb, _ = sell.make(t_, 70, 1, lambda r: [])
        self.assertNotIn("c|авто", json.dumps(kb, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
