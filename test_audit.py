"""Перевірка живих оголошень (research/audit.py, 26.10): розбіжності назви, номера, частоти, обсягу, опису й доставки.
Кейси — з реальних помилок 26.10 (№4 з чужим номером 3200, №5 «Vengeance OVP», №8 неповний номер, DHL при Hermes). Без мережі."""
import sys
import unittest
from unittest import mock

sys.path.insert(0, ".")
sys.path.insert(0, "research")
import audit  # noqa: E402

DESC = ("Biete hier ein Kit Corsair Vengeance {s} 32GB (2x16GB) DDR4 DIMM Desktop RAM an.\nDie Daten:\n"
        "• Kapazität: 32 GB (2 × 16 GB)\n• Typ: DDR4 DIMM (Desktop-PC)\n• Geschwindigkeit: {sp} MHz\n{pn}\n"
        "Verschickt wird gut und antistatisch verpackt als versichertes {ship}Paket mit Sendungsnummer.")
T4 = "Corsair Vengeance LPX 32GB (2x16GB) DDR4-2133 CL13 CMK32GX4M2A2133C13 getestet"


def ebay(title=T4, s="LPX", sp=2133, pn="• Teilenummer: CMK32GX4M2A2133C13", ship="", specs=None, free=True, service="DE_HermesPaket"):
    base = {"Herstellernummer": ["CMK32GX4M2A2133C13"], "Busgeschwindigkeit": ["PC4-17000 (DDR4-2133)"], "Modell": ["Corsair Vengeance LPX"],
            "Gesamtkapazität": ["32 GB"], "Anzahl der Module": ["2"], "Formfaktor": ["DIMM"], "Produktart": ["DDR4 SDRAM"]}
    return dict(where="eBay", id="1", url="u", title=title, desc=DESC.format(s=s, sp=sp, pn=pn, ship=ship),
                specs=base if specs is None else specs, ship_free=free, ship_service=service)


class TestRules(unittest.TestCase):
    def test_clean_listing(self):
        facts = {"pn": "CMK32GX4M2A2133C13", "speed": 2133, "module_gb": 16, "gen": "ddr4", "form": "udimm"}
        self.assertEqual(audit.check(ebay(), "Corsair Vengeance LPX 32GB DDR4 2133", facts), [])

    def test_foreign_part_number_3200_on_2133(self):
        it = ebay(specs=dict(ebay()["specs"], Herstellernummer=["CMK32GX4M2B3200C16", "CMK32GX4M2A2133C13"]))
        out = "\n".join(audit.check(it))
        self.assertIn("2 номери", out)
        self.assertIn("номер CMK32GX4M2B3200C16 — 3200", out)

    def test_ovp_model_and_description(self):
        it = ebay(title="Corsair Vengeance LPX 32GB (2x16GB) DDR4-2400 CMK32GX4M2A2400C16 getestet", s="OVP", sp=2400, pn="",
                  specs={"Modell": ["Corsair Vengeance OVP"], "Herstellernummer": ["CMK32GX4M2A2400C16"]})
        out = "\n".join(audit.check(it))
        self.assertIn("«OVP» не модель", out)
        self.assertIn("в описі «Corsair Vengeance OVP», а в назві «Corsair Vengeance LPX»", out)

    def test_incomplete_part_number(self):
        it = dict(where="eBay", title="G.SKILL FORTIS 32GB (2x16GB) DDR4-2400 F4-2400C16D-32GFT Desktop RAM getestet",
                  desc="Biete hier ein Kit G.SKILL FORTIS 32GB (2x16GB) DDR4 DIMM Desktop RAM an.\n• Teilenummer: F4-2400C16D",
                  specs={"Herstellernummer": ["F4-2400C16D"]})
        self.assertIn("номер моделі різний", audit.check(it)[0])

    def test_shipping_text_vs_listing(self):
        self.assertIn("в описі DHL, а доставка в оголошенні — HermesPaket", audit.check(ebay(ship="DHL-")))
        self.assertEqual(audit.check(ebay(ship="DHL-", service="DE_DHLPaket")), [])
        paid = ebay(free=False, service="DE_DHLPaket")
        paid["desc"] = paid["desc"].replace("Verschickt wird", "Kostenloser Versand:")
        self.assertIn("в описі «Kostenloser Versand», а доставка платна", audit.check(paid))
        self.assertEqual(audit.check(dict(ebay(ship="DHL-"), where="KA")), [])   # на KA доставки в оголошенні не читаємо

    def test_capacity_form_gen_and_ledger(self):
        it = ebay(specs=dict(ebay()["specs"], **{"Gesamtkapazität": ["64 GB"], "Formfaktor": ["SO-DIMM"]}))
        out = "\n".join(audit.check(it))
        self.assertIn("обсяг не збігається", out)
        self.assertIn("тип планки не збігається", out)
        self.assertIn("обсяг: облік — 16 ГБ", "\n".join(audit.check(ebay(), "Kingston 16GB 2x8GB DDR4 2133")))
        self.assertIn("на фото планки по 8 ГБ", "\n".join(audit.check(ebay(), None, {"module_gb": 8})))

    def test_part_number_speed(self):
        self.assertEqual([audit.pn_speed(p) for p in ("CMK32GX4M2A2133C13", "F4-2400C16D-32GFT", "KF432C16BBK2/32",
                                                      "KVR26N19S8/16", "M471A4G43AB1-CWE")], [2133, 2400, 3200, 2666, None])
        self.assertEqual(audit.text_speeds("DDR4-2133 · PC4-19200 · 3200MT/s"), {2133, 2400, 3200})

    def test_digest_order_free(self):
        self.assertEqual(audit.digest(["a", "b"]), audit.digest(["b", "a"]))


class TestKA(unittest.TestCase):
    PAGE = ('<h1 id="viewad-title" class="x">Corsair Vengeance LPX 32GB (2x16GB) DDR4-2400 RAM getestet</h1>'
            '<h2 id="viewad-price">163 € VB</h2><p id="viewad-description-text" class="t">Biete hier ein Kit Corsair Vengeance '
            'OVP 32GB (2x16GB) DDR4 DIMM Desktop RAM an.<br/>• Geschwindigkeit: 2400 MHz (PC4-19200)</p>'
            '<a href="/s-bestandsliste.html?userId=12345">Alle Anzeigen</a>')

    def test_ad_page(self):
        it = audit.ka_item("3550381538", self.PAGE)
        self.assertEqual((it["title"][:24], it["price"], it["where"]), ("Corsair Vengeance LPX 32", "163 € VB", "KA"))
        self.assertIn("в описі «Corsair Vengeance OVP»", audit.check(it)[0])
        self.assertEqual(audit.ka_user(self.PAGE), "12345")

    def test_match_unlinked_ad_to_ledger(self):
        rows = [{"n": 4, "title": "Corsair LPX 32GB 2x16 DDR4 2133"}, {"n": 8, "title": "G.Skill Fortis 2x16GB DDR4-2400"},
                {"n": 9, "title": "Kingston 16GB DDR4 2400"}]
        self.assertEqual(audit.match_row("G.SKILL FORTIS 32GB 2x16GB DDR4-2400 F4-2400C16D-32GFT getestet", rows)["n"], 8)
        self.assertIsNone(audit.match_row("Corsair 32GB 2x16GB DDR4-2400", rows + [{"n": 10, "title": "Crucial 2x16GB DDR4 2400"}]))

    def test_run_links_and_reports(self):
        pages = {"3550381538": self.PAGE, "3551003186": self.PAGE.replace("Corsair Vengeance LPX 32GB (2x16GB) DDR4-2400",
                                                                          "G.SKILL FORTIS 32GB 2x16GB DDR4-2400")}
        listing = ('<li data-adid="3550381538"><h2>Corsair</h2></li><li data-adid="3551003186"><a class="ellipsis" href="x">'
                   'G.SKILL FORTIS 32GB 2x16GB DDR4-2400 F4-2400C16D-32GFT getestet</a></li>')

        def get(url, **k):
            body = listing if "bestandsliste" in url else pages.get(url.rsplit("/", 1)[1], "")
            return mock.Mock(status_code=200, text=body)
        rows = [{"n": 6, "title": "Corsair LPX 2x16 DDR4 2400", "status": "Виставлено", "ka": "3550381538"},
                {"n": 8, "title": "G.Skill Fortis 2x16GB DDR4-2400", "status": "Виставлено", "ka": ""}]
        with mock.patch.object(audit, "ebay_item", return_value=ebay()):
            res = audit.run([{"id": "1"}], [{"n": 4, "id": "1"}], rows, "tok", None, get=get, cache={}, ask=lambda *a: None)
        self.assertEqual(res["ka_links"], [{"n": 8, "id": "3551003186"}])
        self.assertEqual(sorted((a["n"], a["where"]) for a in res["audit"]), [(6, "KA"), (8, "KA")])   # eBay №4 — чистий

    def test_photo_facts_cached(self):
        calls = []
        ask = lambda imgs, prompt: calls.append(1) or {"part_number": "F4-2400C16-16GFT", "module_gb": 16, "gen": "ddr4",   # noqa: E731
                                                      "form": "udimm", "confidence": "high"}
        cache = {}
        f = audit.photo_facts(["https://i.ebayimg.com/images/g/x/s-l500.jpg"], cache, ask, lambda u: b"img")
        self.assertEqual((f["speed"], f["module_gb"]), (2400, 16))
        audit.photo_facts(["https://i.ebayimg.com/images/g/x/s-l500.jpg"], cache, ask, lambda u: b"img")
        self.assertEqual(len(calls), 1)
        unsure = lambda imgs, prompt: {"part_number": "X", "confidence": "low"}   # noqa: E731
        self.assertIsNone(audit.photo_facts(["u2"], cache, unsure, lambda u: b"img"))


if __name__ == "__main__":
    unittest.main()
