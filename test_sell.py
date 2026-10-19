"""«продати N» (01.10): шифрування в парі з ledger.gs, ціни й пороги, німецькі тексти. Без мережі."""
import itertools
import json
import os
import sys
import unittest

NL, NL2 = chr(10), chr(10) * 2

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import sell

# Перша Switch вимкнена 04.10 (console_alert.SWITCH1_ON); ці тести стережуть логіку її розпізнавання — на час модуля вмикаємо
def setUpModule():
    import console_alert
    console_alert._switch1_saved, console_alert.SWITCH1_ON = console_alert.SWITCH1_ON, True


def tearDownModule():
    import console_alert
    console_alert.SWITCH1_ON = console_alert._switch1_saved

# Той самий вектор, що в tools/test_sell.js (зашифрував Apps Script-код ledger.gs) — сумісність JS ↔ Python
VECTOR = ("ckNGkBdsfkbzaDMDdkUQy2CAcFUVcmcL47hP7XagKCLHDNhFMfmX+ODbR4DI3gCFZpjESsJhH5H5",
          "61231ce607032562185d2078ff7a8ccd", "123:ABC", "n0nce")

TITLES = ["OWC 2x16GB DDR4 SO-DIMM 2666MHz für iMac 2019", "RAM ddr4 32GB für iMac",
          "Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200MHz CL16 CMK32GX4M2E3200C16",
          "Kingston FURY Beast 64GB (2x32GB) DDR5 5600MHz CL36 KF556C36BBEK2-64",
          "G.Skill Trident Z5 RGB 32GB 2x16GB DDR5 6000", "Crucial 32GB DDR5 SO-DIMM 5600 CT32G56C46S5",
          "Sony PS5 Slim Disc 1TB mit Controller", "PS5 Digital Edition", "Xbox Series X 1TB", "Nintendo Switch 2",
          "Nintendo Switch OLED weiß", "Nintendo Switch Lite türkis"]


class CryptoTest(unittest.TestCase):
    def test_js_vector(self):
        self.assertEqual(sell.unseal(*VECTOR), {"row": 3, "title": "OWC 2x16GB DDR4 für iMac", "cost": 45.5})

    def test_roundtrip_and_tamper(self):
        d = {"row": 12, "title": "Kingston Fury 2×16 GB – für iMac", "cost": 123.45}
        blob, mac = sell.seal(d, "tok", "abc")
        self.assertEqual(sell.unseal(blob, mac, "tok", "abc"), d)
        self.assertNotIn("123.45", blob)
        with self.assertRaises(ValueError):
            sell.unseal(blob, mac, "other-token", "abc")
        with self.assertRaises(ValueError):
            sell.unseal(blob[:-4] + "AAAA", mac, "tok", "abc")


class PriceTest(unittest.TestCase):
    def test_invariants(self):
        for title, cost, comp in itertools.product(TITLES, (None, 20, 45, 100, 300, 600), ([], [50], [999], [120, 130])):
            r = sell.identify(title)
            self.assertIsNotNone(r, title)
            pr = sell.plan_price(r, cost, comp)
            self.assertLessEqual(pr["decline"], pr["accept"], (title, cost, comp))
            self.assertLessEqual(pr["accept"], pr["list"], (title, cost, comp))
            if cost:   # жоден автоматичний варіант не продає в мінус
                self.assertGreaterEqual(pr["profit_accept"], min(sell.MIN_PROFIT_ABS, 0.1 * cost) - 0.5, (title, cost, comp))
                self.assertGreaterEqual(sell.net_of(r, pr["decline"]) - cost, -0.01, (title, cost, comp))

    def test_median_price_competitors_only_warn(self):
        # 08.10 (рішення користувача): ціна = медіана продажів, а не підрізання під найдешевших конкурентів
        r = sell.identify("Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200MHz")
        med = sell.nice(r["median_sale"])
        self.assertEqual(sell.plan_price(r, 50, [])["list"], med)
        for comp in ([145], [120, 125, 129, 131], [100]):
            pr = sell.plan_price(r, 50, comp)
            self.assertEqual(pr["list"], med, comp)
            if comp[min(2, len(comp) - 1)] <= med:
                self.assertIn("за медіаною продаватиметься повільніше", " ".join(pr["warn"]), comp)
        self.assertFalse(sell.plan_price(r, 50, [999])["warn"])
        # тижневий звіт (залежалося): як раніше — серед трьох найдешевших, не нижче 92% «швидкої»
        self.assertEqual(sell.plan_price(r, 50, [120, 125, 129, 131], undercut=True)["list"],
                         sell.nice(r["quick_sale"] * sell.LOW_SHARE))   # 10.10: фірмовий набір — не нижче 92% його «швидкої»
        self.assertGreaterEqual(sell.plan_price(r, 50, [100], undercut=True)["list"], r["quick_sale"] * sell.LOW_SHARE - 5)

    def test_loss_guard(self):
        r = sell.identify("Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200MHz")
        pr = sell.plan_price(r, 200, [])   # переплатили — ціна вища за ринок, але не в мінус
        self.assertGreater(pr["list"], r["median_sale"])
        self.assertIn("в мінус", " ".join(pr["warn"]))

    def test_nice(self):
        self.assertEqual([sell.nice(x) for x in (128, 130, 134, 135.9, 27.5)], [124, 129, 134, 134, 27])


class TextTest(unittest.TestCase):
    def test_titles_short_and_clean(self):
        for t in TITLES:
            r = sell.identify(t)
            tx = sell.ram_texts(t, r) if r["kind"] == "ram" else sell.console_texts(t, r)
            self.assertLessEqual(len(tx["title"]), 80, t)
            self.assertNotRegex(tx["title"], r"невідом|сумнівн|x16GB MHz|  ", t)
            self.assertNotRegex(tx["desc"], r"[а-яіїє]", t)   # опис — лише німецькою

    def test_ram_specs(self):
        r = sell.identify(TITLES[0])
        tx = sell.ram_texts(TITLES[0], r)
        self.assertEqual(tx["title"], "OWC 32GB (2x16GB) DDR4-2666 SO-DIMM Laptop RAM für iMac 2019 – getestet")
        self.assertIn("PC4-21300", tx["desc"])
        r = sell.identify(TITLES[2])
        self.assertIn("Teilenummer: CMK32GX4M2E3200C16", sell.ram_texts(TITLES[2], r)["desc"])

    def test_console_names(self):
        r = sell.identify("Sony PS5 Slim Disc 1TB mit Controller")
        self.assertTrue(sell.console_texts("Sony PS5 Slim Disc 1TB mit Controller", r)["title"]
                        .startswith("Sony PlayStation 5 Slim Disc Edition 1TB Konsole + Controller"))
        r = sell.identify("PS5 Digital Edition")
        self.assertIn("Digital Edition", sell.console_texts("PS5 Digital Edition", r)["title"])


class StepsTest(unittest.TestCase):
    """09.10 (прохання користувача): «продати N» — огляд українською + покроково як у формі eBay, поля німецькою."""

    def test_part_number_in_title_and_steps(self):
        t = "Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200 CMK32GX4M2B3200C16"
        text, kb, steps = sell.make(t, 70.63, 2, lambda r: [])
        r = sell.identify(t)
        tx = sell.ram_texts(t, r)
        self.assertIn("CMK32GX4M2B3200C16", tx["title"])
        self.assertLessEqual(len(tx["title"]), 80)
        skb = sell.make.steps_kb["inline_keyboard"]   # 10.10: «скопіювати» — під покроковими, під карткою — дії
        self.assertEqual(skb[1][0]["copy_text"]["text"], "CMK32GX4M2B3200C16")   # кнопка Herstellernummer
        self.assertEqual([b["callback_data"] for row in kb["inline_keyboard"] for b in row if "callback_data" in b], ["c|кроки|2"])
        self.assertIn("Перед публікацією", steps)
        self.assertLessEqual(text.count("\n"), 5)   # коротка картка
        order = ["FOTOS", "TITEL", "ARTIKELKATEGORIE", "ARTIKELMERKMALE", "ZUSTAND", "BESCHREIBUNG", "PREISGESTALTUNG",
                 "DETAILS ZUR LIEFERUNG", "ANGEBOT BEWERBEN", "Angebot einstellen"]
        self.assertEqual([steps.index(x) for x in order], sorted(steps.index(x) for x in order))   # порядок як у формі
        for f in ("Produktart: <code>DDR4 SDRAM</code>", "Herstellernummer: <code>CMK32GX4M2B3200C16</code>",
                  "Anzahl der Pins: <code>288</code>", "Käufer zahlt: <code>6,49</code>", "DHL Paket", "виставив 2 "):
            self.assertIn(f, steps)
        self.assertIn("ECC-Speicher", steps)   # попередження не тиснути «Alle übernehmen»
        # платна доставка: ціна товару = «разом» мінус 6,49, пороги теж
        pr = sell.plan_price(r, 70.63, [])
        ip = sell.item_prices(pr, sell.SHIP_CHARGE_RAM)
        self.assertEqual(ip["item"], round(pr["list"] - 6.49))
        self.assertIn(f"Artikelpreis: <code>{ip['item']},00</code>", steps)
        self.assertIn(f"Automatisch akzeptieren: <code>{ip['accept']},00</code>", steps)
        self.assertIn(f"<b>{ip['item']} €</b> + доставка 6,49 €", text)
        self.assertNotIn("kostenlos", tx["desc"].lower())
        self.assertNotRegex(tx["desc"], r"[а-яіїє]")

    def test_part_number_picks_real_model(self):
        self.assertEqual(sell._part_number("2X16GB SK Hynix DDR4 PC4-2666V (HMA82GU7CJR8N) ECC UDIMM"), "HMA82GU7CJR8N")
        self.assertEqual(sell._part_number("Samsung 32GB DDR5-4800 SO-DIMM M425R4GA3BB0-CQK0D"), "M425R4GA3BB0-CQK0D")
        self.assertIsNone(sell._part_number("Corsair Vengeance 2X16GB DDR4-3200 PC4-25600"))

    def test_console_steps(self):
        text, kb, steps = sell.make("Sony PS5 Slim Disc 1TB mit Controller", 300, 4, lambda r: [])
        self.assertIn("Plattform: <code>Sony PlayStation 5</code>", steps)
        self.assertNotIn("Anzahl der Pins", steps)


class MakeTest(unittest.TestCase):
    def test_unknown_item(self):
        text, kb, steps = sell.make("Lampe", 5, 4, lambda r: [])
        self.assertIn("Не впізнав", text)
        self.assertIsNone(kb)

    def test_single_module_warning_and_card(self):
        text, kb, steps = sell.make("RAM ddr4 32GB für iMac", 45, 3, lambda r: [])
        self.assertIn("ОДНУ на 32 ГБ", text)
        self.assertIn("продати 3", text)
        self.assertEqual(sell.make.steps_kb["inline_keyboard"][0][0]["copy_text"]["text"], "32GB DDR4 SO-DIMM Laptop RAM für iMac – getestet")

    def test_competitor_api_failure_still_gives_card(self):
        def boom(r):
            raise RuntimeError("429")
        text, _, _ = sell.make("Xbox Series X 1TB", 380, 5, boom)
        self.assertIn("схожих зараз немає", text)

    def test_competitor_filter(self):
        import main
        items = [{"title": "Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200", "price": {"value": "129"},
                  "shippingOptions": [{"shippingCost": {"value": "0"}}], "seller": {"feedbackScore": 12}},
                 {"title": "Corsair Vengeance 32GB (2x16GB) DDR4 3200", "price": {"value": "99"},
                  "seller": {"feedbackScore": 0}},                                                # 0 відгуків
                 {"title": "Corsair 16GB (2x8GB) DDR4 3200", "price": {"value": "60"}, "seller": {"feedbackScore": 5}},          # інша ємність
                 {"title": "DDR4 32GB 2x16 defekt", "price": {"value": "40"}, "seller": {"feedbackScore": 5}},                   # дефект
                 {"title": "G.Skill 32GB (2x16GB) DDR4 3600", "price": {"value": "140"},
                  "shippingOptions": [{"shippingCost": {"value": "4.99"}}], "seller": {"feedbackScore": 300}}]
        old = main._request_with_backoff
        calls = []
        # 19.10: усі сторінки — друга сторінка (offset 200) теж читається, поки не дійшли до total
        main._request_with_backoff = lambda *a, **k: calls.append(k["params"]["offset"]) or (
            {"itemSummaries": items * 40, "total": 210} if k["params"]["offset"] == 0 else {"itemSummaries": items[:1], "total": 210})
        try:
            r = sell.identify("Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200MHz")
            got = sell.competitors(r, client=type("C", (), {"_headers": lambda self: {}})())
            self.assertEqual(calls, [0, 200])
            self.assertEqual(sorted(set(got)), [129.0, 144.99])
            self.assertEqual(len(got), 81)
        finally:
            main._request_with_backoff = old


class AlbumTest(unittest.TestCase):
    def test_photos_after_listing(self):
        # 08.10: фото товару з обліку — альбомом (до 10 у групі), одиночне — окремим фото; картинки-файли — окремо
        import requests
        posted, old = [], requests.post
        requests.post = lambda url, data=None, **k: (posted.append((url.rsplit("/", 1)[1], data)), type("R", (), {"status_code": 200})())[1]
        try:
            photos = [{"id": f"P{i}", "t": "photo"} for i in range(12)] + [{"id": "D1", "t": "document"}, {"bad": 1}]
            self.assertEqual(sell.office_send_album(photos, "📸 №3"), 13)
        finally:
            requests.post = old
        self.assertEqual([m for m, _ in posted], ["sendMediaGroup", "sendMediaGroup", "sendDocument"])
        first = json.loads(posted[0][1]["media"])
        self.assertEqual((len(first), first[0]["caption"], "caption" in first[1]), (10, "📸 №3", False))
        self.assertEqual(len(json.loads(posted[1][1]["media"])), 2)


class HumanDescTest(unittest.TestCase):
    """16.10: опис — по-людськи, без «am nächsten Werktag» і «Rechte aus dem eBay-Käuferschutz … unberührt»."""

    def test_ram_desc(self):
        r = sell.identify("Corsair Vengeance LPX 32GB (2x16GB) DDR4-2133")
        d = sell.ram_texts("Corsair Vengeance LPX 32GB (2x16GB) DDR4-2133", r)["desc"]
        self.assertTrue(d.startswith("Biete hier ein Kit Corsair Vengeance LPX"))
        self.assertIn("Die Riegel sind gebraucht, laufen einwandfrei", d)
        self.assertIn("0 Fehler", d)
        self.assertIn("Privatverkauf, daher keine Garantie und keine Rücknahme.", d)
        for bad in ("nächsten Werktag", "unberührt", "Gewährleistung", "Verkauft wird"):
            self.assertNotIn(bad, d)


class ReviseModeTest(unittest.TestCase):
    """16.10: «🔁 Оновити на eBay» — та сама ціна / назва з правками → revise_listing; успіх → у бот і в облік."""

    def test_revise_mode(self):
        from unittest import mock
        import ebay_list as el
        sent, back, calls = [], [], []
        d = {"mode": "revise", "row": 4, "title": "Corsair Vengeance LPX 32GB (2x16GB) DDR4-2133 CL13 CMK32GX4M2A2133C13",
             "cost": 96.54, "rt": "rt", "item_id": "1234567890", "photos": [], "edits": {"price": 165}}
        with mock.patch.object(el, "access_token", return_value="tok"), \
                mock.patch.object(el, "revise_listing", side_effect=lambda *a, **k: calls.append(a) or
                                  {"ok": True, "errors": [], "warnings": []}):
            sell.ebay_mode(d, "k", post=lambda *a, **k: None, send=lambda t, m: sent.append((t, m)) or True,
                           back=lambda x, k: back.append(x), comp_fn=lambda r: [])
        self.assertEqual(calls[0][0], "1234567890")
        self.assertEqual(calls[0][2]["item"], 165)                       # правка ціни — у живе оголошення
        self.assertIn("№4 оновлено на eBay", sent[-1][0])
        self.assertEqual(back[-1], {"kind": "revised", "row": 4, "price": 165, "item_id": "1234567890"})
        with mock.patch.object(el, "access_token", return_value="tok"), \
                mock.patch.object(el, "revise_listing", return_value={"ok": False, "errors": ["Titel zu lang [240]"], "warnings": []}):
            sell.ebay_mode(d, "k", post=lambda *a, **k: None, send=lambda t, m: sent.append((t, m)) or True,
                           back=lambda x, k: back.append(x), comp_fn=lambda r: [])
        self.assertIn("eBay не прийняв зміни №4", sent[-1][0])
        sell.ebay_mode(dict(d, item_id=""), "k", post=lambda *a, **k: None, send=lambda t, m: sent.append((t, m)) or True,
                       back=lambda x, k: back.append(x), comp_fn=lambda r: [])
        self.assertIn("Не знаю номер оголошення", sent[-1][0])

    def test_revise_only_changed_parts(self):
        # 17.10: «🔁» — лише змінене через ✏️ (тут фото): назва / ціна в revise не йдуть, у облік — без ціни
        from unittest import mock
        import ebay_list as el
        sent, back, calls = [], [], []
        d = {"mode": "revise", "row": 4, "title": "Corsair Vengeance LPX 32GB (2x16GB) DDR4-2133", "cost": 96.54, "rt": "rt",
             "item_id": "1234567890", "photos": [{"id": "f1"}, {"id": "f2"}], "edits": {"photos": True}, "parts": ["photos"]}
        with mock.patch.object(el, "access_token", return_value="tok"), \
                mock.patch.object(el, "is_image", return_value=True), \
                mock.patch.object(el, "upload_picture", side_effect=lambda data, name, tok, post: f"https://i.ebayimg.com/{name}.jpg"), \
                mock.patch.object(sell, "studio_or_original", side_effect=lambda b: b), \
                mock.patch.object(el, "revise_listing", side_effect=lambda *a: calls.append(a) or {"ok": True, "errors": [], "warnings": []}):
            sell.ebay_mode(d, "k", post=lambda *a, **k: None, send=lambda t, m: sent.append((t, m)) or True,
                           back=lambda x, k: back.append(x), file_fn=lambda fid, k: b"img", comp_fn=lambda r: [])
            self.assertEqual(calls[-1][6], {"photos"})
            self.assertEqual(len(calls[-1][3]), 2)
            self.assertIn("Решта — як була", sent[-1][0])
            self.assertNotIn("💶", sent[-1][0])
            self.assertEqual(back[-1], {"kind": "revised", "row": 4, "item_id": "1234567890"})
            sell.ebay_mode(dict(d, parts=["price"], edits={"price": 165}), "k", post=lambda *a, **k: None,
                           send=lambda t, m: sent.append((t, m)) or True, back=lambda x, k: back.append(x),
                           file_fn=lambda fid, k: self.fail("фото не змінювались — не вантажимо"), comp_fn=lambda r: [])
            self.assertEqual((calls[-1][3], calls[-1][6]), ([], {"price"}))
            self.assertEqual(back[-1]["price"], 165)
        sell.ebay_mode(dict(d, parts=[]), "k", post=lambda *a, **k: None, send=lambda t, m: sent.append((t, m)) or True,
                       back=lambda x, k: back.append(x), comp_fn=lambda r: [])
        self.assertIn("нічого не змінено", sent[-1][0])

    def test_got_mode_sends_seller_photo(self):
        # 17.10: «отримав N» — головне фото з оголошення продавця з підписом «звір наклейку»
        from unittest import mock
        import ebay_list as el
        import parcels
        albums = []
        with mock.patch.object(el, "access_token", return_value="tok"),                 mock.patch.object(parcels, "picture_for", return_value="https://i.ebayimg.com/x.jpg"),                 mock.patch.object(sell, "office_send_url_album", side_effect=lambda pics, get: albums.append(pics) or 1):
            sell.ebay_mode({"mode": "got", "row": 6, "id": "306123123123", "link": "", "pn": "CMK32GX4M2A2400C16", "rt": "rt"}, "k",
                           post=lambda *a, **k: None, send=lambda t, m: True, back=lambda x, k: None)
        cap, url = albums[0][0]
        self.assertEqual(url, "https://i.ebayimg.com/x.jpg")
        self.assertIn("№6", cap)
        self.assertIn("CMK32GX4M2A2400C16", cap)

    def test_long_html_split(self):
        # 18.10 (аудит): довше 4096 — кількома повідомленнями, кнопки під останнім (раніше — обрізка посеред тегу, втрата)
        from unittest import mock
        calls = []

        class R:
            status_code = 200
        t = NL2.join(f"<b>лот {i}</b>" + NL + "<blockquote>" + "x" * 240 + "</blockquote>" for i in range(30))
        with mock.patch("requests.post", side_effect=lambda url, data=None, **k: calls.append(data) or R()):
            self.assertTrue(sell.office_send_html(t, {"inline_keyboard": [[{"text": "k", "url": "https://x"}]]}))
        self.assertGreater(len(calls), 1)
        self.assertTrue(all(len(c["text"]) <= 4096 and c["text"].count("<blockquote>") == c["text"].count("</blockquote>") for c in calls))
        self.assertEqual([("reply_markup" in c) for c in calls], [False] * (len(calls) - 1) + [True])

    def test_sync_mode_posts_changes(self):
        # 17.10: фонова перевірка лотів — зміни назад в облік, без повідомлення в чат
        from unittest import mock
        import ebay_list as el
        import listings
        back, sent = [], []
        rows = [dict(n=4, title="Corsair 2133", status="Виставлено", note="(eBay 306000000004)")]
        with mock.patch.object(el, "access_token", return_value="tok"),                 mock.patch.object(listings, "active", return_value=[dict(id="306000000004", title="x", price=159.0)]):
            sell.ebay_mode({"mode": "sync", "rt": "rt", "rows": rows}, "k", post=lambda *a, **k: None,
                           send=lambda t, m: sent.append(t) or True, back=lambda x, k: back.append(x))
        self.assertEqual(back, [{"kind": "sync", "manual": 0, "count": 1, "lots": [dict(n=4, id="306000000004", price=159.0)], "ask": [], "ended": []}])
        self.assertEqual(sent, [])

    def test_market_check(self):
        # 19.10: №5 Corsair 2×16 DDR4-2400 — 169 € + 6,49 доставка, а три найдешевші 139 / 140 / 145 → порада серед них;
        # свіжий лот (< 3 дн.), дешевший за конкурентів, або мінімум прибутку не дає знизити — без поради
        from datetime import datetime, timezone
        import listings
        now = datetime(2026, 10, 19, tzinfo=timezone.utc)
        rows = [dict(n=5, title="Corsair Vengeance 32GB (2x16GB) DDR4-2400 DIMM", status="Виставлено", note="(eBay 1)", spent=95),
                dict(n=8, title="G.SKILL FORTIS 32GB (2x16GB) DDR4-2400 DIMM", status="Виставлено", note="(eBay 2)", spent=140),
                dict(n=4, title="Corsair Vengeance LPX 32GB (2x16GB) DDR4-2133", status="Виставлено", note="(eBay 3)", spent=90)]
        lots = [dict(id="1", title="x", price=169.0, ship=None, start="2026-10-11T10:00:00Z"),
                dict(id="2", title="y", price=169.0, ship=0.0, start="2026-10-11T10:00:00Z"),
                dict(id="3", title="z", price=169.0, ship=None, start="2026-10-18T10:00:00Z")]
        got = listings.market_check(lots, rows, now, comp_fn=lambda r: [139.0, 140.0, 145.0, 160.0])
        self.assertEqual([(m["n"], m["comp"], m["floor"]) for m in got], [(5, [139, 140, 145], False), (8, [139, 140, 145], True)])
        self.assertLess(got[0]["item"] + got[0]["ship"], 145)
        self.assertEqual(got[1]["ship"], 0.0)   # безкоштовна доставка — ціна товару = повна
        self.assertEqual((got[0]["place"], got[0]["total"]), (5, 4))
        self.assertGreater(got[0]["profit"], 0)
        cheap = listings.market_check([dict(lots[0], price=129.0)], rows, now, comp_fn=lambda r: [139.0, 140.0, 145.0])
        self.assertEqual(cheap, [])

    def test_listed_card_has_revise_button(self):
        kb = sell.keyboard({}, sell.identify("Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200"), 4, True, listed=True)
        cb = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
        self.assertIn("c|оновити|4", cb)
        self.assertNotIn("c|авто|4", cb)


class EditsTest(unittest.TestCase):
    """14.10: правки з бота перед «🚀» — ціна (без доставки), назва на eBay, опис; пороги — від нової ціни."""

    def test_edits_apply_to_card_and_listing(self):
        title = "Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200 CMK32GX4M2B3200C16"
        base = sell.prepare(title, 70.63, 1, lambda r: [])
        p = sell.prepare(title, 70.63, 1, lambda r: [], {"price": 139, "title": "Corsair LPX 2x16GB DDR4 3200 getestet",
                                                          "desc": "Mit OVP."})
        ip = sell.item_prices(p["pr"], p["tx"]["ship"])
        self.assertEqual(ip["item"], 139)
        self.assertLessEqual(p["pr"]["accept"], p["pr"]["list"])
        self.assertLessEqual(p["pr"]["decline"], p["pr"]["accept"])
        self.assertEqual(p["tx"]["title"], "Corsair LPX 2x16GB DDR4 3200 getestet")
        self.assertTrue(p["tx"]["desc"].endswith("Mit OVP."))
        self.assertEqual(p["edited"], ["ціна", "назва", "опис"])
        self.assertNotEqual(base["pr"]["list"], p["pr"]["list"])
        card = sell.build_card(1, title, p["r"], p["pr"], [], p["tx"], 70.63, p["edited"])
        self.assertIn("✏️ змінено: ціна, назва, опис", card)
        self.assertIn("📝 На eBay: <i>Corsair LPX 2x16GB DDR4 3200 getestet</i>", card)
        self.assertIn("<b>139 €</b>", card)
        low = sell.prepare(title, 70.63, 1, lambda r: [], {"price": 50})
        self.assertTrue(any("мінус" in w for w in low["pr"]["warn"]))   # нижче закупівлі — попередження

    def test_edit_button(self):
        kb = sell.keyboard({}, sell.identify("Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200"), 3, True)
        self.assertIn("c|змінити|3", [b.get("callback_data") for row in kb["inline_keyboard"] for b in row])


if __name__ == "__main__":
    unittest.main()



class PlanTest(unittest.TestCase):
    """10.10: план продажу дописується в повідомлення «📒 Записав покупку» (без запитів до eBay)."""

    def test_plan_line_and_edit(self):
        line = sell.plan_line("Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200", 70.63, 3)
        self.assertRegex(line, r"^📈 План: продати за ≈ \d+ € \+ доставка → прибуток ≈ \d+ €$")
        self.assertIsNone(sell.plan_line("Lampe", 5))
        sent = []
        ok = sell.plan_mode({"title": "Corsair Vengeance LPX 32GB (2x16GB) DDR4-3200", "cost": 70.63, "row": 3, "mid": 77,
                             "text": "📒 Записав покупку №3: Corsair — 70.63 €", "markup": {"inline_keyboard": [[{"text": "x", "callback_data": "c|отримав|3"}]]}},
                            "K", post=lambda url, data=None, timeout=None: (sent.append((url, data)), type("R", (), {"status_code": 200})())[1])
        self.assertTrue(ok)
        self.assertTrue(sent[0][0].endswith("/botK/editMessageText"))
        self.assertEqual(sent[0][1]["message_id"], 77)
        self.assertIn("📒 Записав покупку №3", sent[0][1]["text"])
        self.assertIn("📈 План:", sent[0][1]["text"])
        self.assertIn("c|отримав|3", sent[0][1]["reply_markup"])
