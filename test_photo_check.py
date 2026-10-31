"""Тести перевірки фото (research/photo_check.py): що ми очікуємо і як порівнюємо з відповіддю моделі. Без мережі."""
import os as _os
_os.environ.setdefault("RAM_PRICES_OFF", "1")   # цифри Terapeak, а не щоденні ціни сторожа (research/ram_prices.json)
import sys
import unittest

sys.path.insert(0, ".")
sys.path.insert(0, "research")
from console_alert import evaluate_console
from html import escape

from photo_check import SERVER_MSG, add_to_card, compare, expectation, ka_images
from ram_alert import evaluate


class TestPhotoCheck(unittest.TestCase):
    def test_corsair_4x4_sold_as_32(self):
        # 29.09: «Corsair Dominator RAM DDR 4 32 GB» за €80, на фото чотири планки з наклейкою «8GB (2x4GB)»
        exp = expectation(evaluate("Corsair Dominator RAM DDR 4 32 GB 3200mhz", 80))
        self.assertEqual((exp["total"], exp["modules"], exp["per"]), (32, 2, 16))
        bad, _ = compare(exp, {"ram_gb_per_module": 4, "ram_modules_visible": 4, "ram_form": "desktop", "ram_gen": "DDR4",
                               "confidence": "high"})
        self.assertTrue(bad and "16 GB" in bad[0])

    def test_matching_kit(self):
        exp = expectation(evaluate("Corsair Vengeance DDR5 32GB (2x16GB) 6000", 150))
        bad, warn = compare(exp, {"ram_gb_per_module": 16, "ram_modules_visible": 2, "ram_form": "desktop", "ram_gen": "DDR5",
                                  "confidence": "high"})
        self.assertEqual((bad, warn), ([], []))

    def test_laptop_and_low_confidence(self):
        exp = expectation(evaluate("Crucial DDR5 32GB (2x16GB) 5600", 100))
        self.assertTrue(compare(exp, {"ram_form": "laptop", "confidence": "medium"})[0])
        self.assertEqual(compare(exp, {"ram_form": "laptop", "confidence": "low"}), ([], []))

    def test_console_model(self):
        exp = expectation(evaluate_console("Xbox Series X 1TB Konsole", 300))
        self.assertEqual(exp["model"], "xbox series x")
        self.assertTrue(compare(exp, {"console_model": "xbox series s", "confidence": "high"})[0])
        self.assertFalse(compare(exp, {"console_model": "xbox series x", "confidence": "high"})[0])
        self.assertTrue(compare(exp, {"console_model": None, "only_box_or_accessory": True, "confidence": "high"})[0])
        exp = expectation(evaluate_console("PS5 Digital Edition Slim", 250))
        self.assertEqual(exp["model"], "ps5 digital")

    def test_server_ram_on_photo(self):
        # 03.10: KA «SK Hynix 2x 16GB DDR4 RAM PC4-2666V» за 85 € — на наклейці HMA82GR7AFR8N, PC4-2666V-RE1-11 (RDIMM)
        exp = expectation(evaluate("SK Hynix 2x 16GB DDR4 RAM PC4-2666V Arbeitsspeicher", 85))
        ok = {"ram_gb_per_module": 16, "ram_modules_visible": 2, "ram_form": "desktop", "ram_gen": "DDR4", "confidence": "high"}
        self.assertEqual(compare(exp, dict(ok, ram_server=False, label_text="16GB 2Rx8 PC4-2666V-UA2-11"))[0], [])
        self.assertIn(SERVER_MSG, compare(exp, dict(ok, ram_server=True))[0][0])
        self.assertIn(SERVER_MSG, compare(exp, dict(ok, label_text="16GB 2Rx8 PC4 - 2666V - RE1 - 11"))[0][0])   # модель сказала «ні»
        self.assertIn(SERVER_MSG, compare(exp, dict(ok, label_text="HMA82GR7AFR8N-VK TF AC"))[0][0])

    def test_server_card_folded_only_for_subscriptions(self):
        import requests

        import photo_check
        sent = []
        saved = (photo_check.photo_line, requests.post)
        photo_check.photo_line = lambda *a, **k: ("📷 <b>⛔ ФОТО НЕ ЗБІГАЄТЬСЯ З НАЗВОЮ:</b> " + escape(SERVER_MSG) + " — …", True)
        requests.post = lambda url, data=None, **k: (sent.append(data), type("R", (), {"status_code": 200})())[1]
        try:
            res = {"price": 85}
            add_to_card(7, "КАРТКА", '{"inline_keyboard": []}', res, "SK Hynix 2x 16GB DDR4", ["u"], collapse_server=True)
            add_to_card(8, "КАРТКА", '{"inline_keyboard": []}', res, "SK Hynix 2x 16GB DDR4", ["u"])   # «поділитися»
        finally:
            photo_check.photo_line, requests.post = saved
        self.assertTrue(sent[0]["text"].startswith("🗑 <b>Відсіяно"))
        self.assertNotIn("КАРТКА", sent[0]["text"])
        self.assertNotIn("reply_markup", sent[0])   # кнопки зникають
        self.assertIn("КАРТКА", sent[1]["text"])
        self.assertIn("reply_markup", sent[1])

    def test_server_ram_by_title_and_description(self):
        from ram_alert import refine_by_desc
        self.assertEqual(evaluate("SK Hynix 2x 16GB DDR4 PC4-2666V-RE1 Arbeitsspeicher", 85)["verdict"], "SKIP")
        self.assertEqual(evaluate("Micron 2x16GB 2RX8 PC4-2666V-RE2-12 MTA18ASF2G72PDZ", 85)["verdict"], "SKIP")
        self.assertNotEqual(evaluate("Samsung 2x16GB DDR4 M378A2K43EB1-CWE PC4-3200AA-UA2-11", 40)["verdict"], "SKIP")   # 10.10: звичайні планки дешевші; 31.10: з рекламою — 40 (було 45)
        t = "SK Hynix 2x 16GB DDR4 RAM PC4-2666V Arbeitsspeicher"
        r = evaluate(t, 30)   # 28.10: повільна DDR4 — від швидкого продажу, «бери» лише дешевше (було 45)
        self.assertNotEqual(r["verdict"], "SKIP")
        desc = ("Modellbezeichnung: HMA82GR7AFR8N-VK TF AC - Bauform: 288-pin DIMM. Er eignet sich hervorragend, "
                "um den Arbeitsspeicher deines PCs aufzurüsten. Preis pro Stück.")
        self.assertIn("серверна", refine_by_desc(r, t, 30, False, desc, evaluate)["reason"])
        self.assertNotEqual(refine_by_desc(r, t, 30, False, "Non-ECC, unbuffered, kein Server RAM. Läuft im Gaming-PC.",
                                           evaluate)["verdict"], "SKIP")

    def test_ecc_unbuffered_is_separate_type(self):
        # 04.10: eBay «2x16GB SK Hynix DDR4 2666 (HMA82GU7CJR8N)» — ECC UDIMM (U7, наклейка PC4-2666V-EE1), не серверна
        r = evaluate("2x16GB SK Hynix DDR4 RAM 2666 MHz PC4-2666V Arbeitsspeicher (HMA82GU7CJR8N)", 65.69)
        plain = evaluate("SK Hynix 2x16GB DDR4 RAM 2666 MHz PC4-2666V Arbeitsspeicher", 65.69)
        self.assertIn("ECC UDIMM", r["type"])
        self.assertTrue(r.get("ecc_udimm") and r["notes"])
        self.assertLess(r["quick_sale"], plain["quick_sale"])   # обережніше за звичайну
        for t in ["Kingston 32GB 2x16 GB DDR4-2400 DIMM, ungepuffert, MIT ECC", "2x16GB DDR4 ECC UDIMM 2666",
                  "32GB Kit (2x16GB) Samsung DDR4-2666 ECC UDIMM M391A2K43BB1-CTD", "16GB SK hynix ECC UDIMM Server RAM 2x16GB DDR4"]:
            self.assertIn("ECC UDIMM", evaluate(t, 40).get("type", ""), t)
        for t in ["DDR4 32GB 2x16 Unbuffered, Non-ECC Corsair", "DDR4 32GB 2x16 unbuffered non ECC"]:
            self.assertNotIn("ECC", evaluate(t, 40).get("type", ""), t)
        for t in ["Samsung 32GB 2x16 DDR4 ECC Registered RDIMM", "2x16GB 2Rx8 PC4-2666V-RE1-11 DDR4", "2x16GB DDR4 2666 ECC RAM"]:
            self.assertEqual(evaluate(t, 40)["verdict"], "SKIP", t)   # Registered і «просто ECC» — як раніше
        exp = expectation(r)
        ok = {"ram_gb_per_module": 16, "ram_modules_visible": 2, "ram_form": "desktop", "ram_gen": "DDR4", "confidence": "high"}
        self.assertEqual(compare(exp, dict(ok, ram_server=True, ram_ecc_unbuffered=True,
                                           label_text="16GB 2Rx8 PC4-2666V-EE1-11 HMA82GU7CJR8N-VK"))[0], [])
        self.assertEqual(compare(exp, dict(ok, ram_server=True, label_text="16GB 2Rx8 PC4-2666V-EE1-11"))[0], [])

    def test_first_switch_not_bought(self):
        # 04.10 (рішення користувача): перша Switch — маржа 15–30 €, на аукціонах за ринковою ціною, плутанина з V1/V2 і Switch 2
        import console_alert
        import ebay_watch
        import price_refresh
        self.assertFalse(console_alert.SWITCH1_ON)
        for t, p in [("Nintendo Switch V2 Konsole mit Dock", 50), ("Nintendo Switch OLED weiß", 70), ("Nintendo Switch Lite grau", 35)]:
            r = evaluate_console(t, p)
            self.assertEqual(r["verdict"], "SKIP", t)
            self.assertIn("перша Switch", r["reason"], t)
        self.assertIn("Switch 2", evaluate_console("Nintendo Switch 2 Konsole mit Mario Kart", 250)["type"])
        self.assertIn("з назви не видно", evaluate_console("Mario Kart 8 Deluxe Nintendo Switch", 30)["reason"])   # гра — як гра
        self.assertNotIn("switch oled", ebay_watch.AUCTION_QUERIES[1][0])
        self.assertEqual(sorted(price_refresh.CONSOLE_QUERIES), ["PS5_DIGITAL", "PS5_DISC", "SWITCH2", "XBOX_SERIES_X"])

    def test_ka_images(self):
        page = ('x https://img.kleinanzeigen.de/api/v1/prod-ads/images/35/35a26f80-95c0-4f6c-b0e1-53177f8aa286?rule=$_59.AUTO '
                'https://img.kleinanzeigen.de/api/v1/prod-ads/images/35/35a26f80-95c0-4f6c-b0e1-53177f8aa286?rule=$_57.AUTO '
                'https://img.kleinanzeigen.de/api/v1/prod-ads/images/b9/b9d94559-d038-4be6-9bc3-6a6f43e3ee59?rule=$_59.JPG')
        self.assertEqual(len(ka_images(page)), 2)
        self.assertTrue(ka_images(page)[0].endswith("?rule=$_57.JPG"))
        # 04.10: фото зі «схожих оголошень» нижче галереї — не наші
        similar = ('<div class="imagebox srpimagebox"><img src="https://img.kleinanzeigen.de/api/v1/prod-ads/images/'
                   '3c/3c03bd83-7486-420a-b94a-f56abfdbc55a?rule=$_2.AUTO"></div>')
        self.assertEqual(len(ka_images(page + similar)), 2)
        similar_ld = ('<section id="vap-smlrads" class="j-search-results"><li class="ad-listitem"><script type="application/ld+json">'
                      '{"contentUrl":"https://img.kleinanzeigen.de/api/v1/prod-ads/images/09/09eee61e-f212-4ca9-bd3b-21776a89c265'
                      '?rule=$_59.AUTO"}</script>')
        self.assertEqual(len(ka_images(page + similar_ld)), 2)


class DescReviewTest(unittest.TestCase):
    """19.10: ШІ читає й опис: проблема з опису — ⛔ «НЕ БЕРИ» з цитатою; «не тестовано» — лише попередження; низька
    впевненість щодо опису — нічого."""

    EXP = {"kind": "ram", "gen": "DDR4", "laptop": False, "total": 64, "modules": 2, "per": 32}

    def test_problem_quote_and_untested(self):
        import photo_check as pc
        bad, warn = pc.compare(self.EXP, {"desc_problem": "defect", "desc_quote": "Selten kam es zu Abstürzen",
                                          "desc_note_uk": "ПК падав", "desc_confidence": "high", "confidence": "high"})
        self.assertEqual(bad, ["в описі: ПК падав — «Selten kam es zu Abstürzen»"])
        bad, warn = pc.compare(self.EXP, {"desc_problem": "", "desc_untested": True, "confidence": "high"})
        self.assertEqual((bad, warn), ([], ["продавець не тестував — бери лише з поправкою на ризик"]))
        bad, warn = pc.compare(self.EXP, {"desc_problem": "scam", "desc_confidence": "low", "confidence": "high"})
        self.assertEqual(bad, [])

    def test_text_only_and_headline(self):
        from unittest import mock
        import photo_check as pc
        res = {"type": "DDR4 UDIMM 64 ГБ (2×32) кіт", "total": 64, "price": 200, "median_sale": 357, "desc": "Lief super. Keine Probleme."}
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}),                 mock.patch.object(pc, "ask_gemini", return_value={"desc_problem": "", "confidence": "high"}) as ask:
            line, bad = pc.photo_line(res, "Corsair 64GB DDR4", [])
        self.assertEqual((line, bad), ("📝 Опис прочитано ✓", False))
        self.assertIn("Keine Probleme", ask.call_args[0][1])               # опис і ринкова ціна — у запиті
        self.assertIn("about 357 EUR", ask.call_args[0][1])
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}),                 mock.patch.object(pc, "ask_gemini", return_value={"desc_problem": "price_per_unit", "desc_quote": "Preis pro Riegel",
                                                                  "desc_confidence": "high", "confidence": "high"}):
            line, bad = pc.photo_line(res, "Corsair 64GB DDR4", [])
        self.assertTrue(bad)
        self.assertIn("⛔ НЕ БЕРИ:", line)
        # 28.10: «(64GB Kit) 2x 32GB … 350 €», а в описі «Der Preis gilt für ein 32gb Ram!» — ШІ каже: ціна за 1 планку
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(pc, "ask_gemini", return_value={"desc_problem": "", "price_covers_modules": 1, "confidence": "high"}):
            line, bad = pc.photo_line(res, "Corsair 64GB DDR4", [])
        self.assertTrue(bad)
        self.assertIn("ціна за 1 планку з 2", line)
        # ШІ не відповів (503 / таймаут) — не мовчимо
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}), mock.patch.object(pc, "ask_gemini", return_value=None):
            self.assertEqual(pc.photo_line(res, "Corsair 64GB DDR4", []), (pc.AI_FAILED, False))

    def test_photo_other_layout_recalculated_30_10(self):
        # 30.10: «G.Skill Ripjaws V 64 GB DDR4 3200» за 200 € — бот рахував 2×32, на фото 4×16 (2 кіти F4-3200C16D-32GVK):
        # не «фото не збігається», а нова оцінка за фото + питання продавцю «ціна за всі 4?»
        from unittest import mock
        import json as _json
        import requests
        import photo_check as pc
        from ram_alert import evaluate
        t = "G.Skill Ripjaws V 64 GB DDR4 3200 MHz RAM Kit"
        res = dict(evaluate(t, 200), desc="Verkaufe meinen RAM, läuft einwandfrei.", risk_lines=["низький"], buy_now=True)
        ph = {"ram_gb_per_module": 16, "ram_modules_visible": 4, "label_text": "F4-3200C16D-32GVK", "ram_gen": "DDR4",
              "ram_form": "desktop", "confidence": "high", "desc_problem": ""}
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}), mock.patch.object(pc, "ask_gemini", return_value=ph),                 mock.patch.object(pc, "_download", return_value=b"x"):
            line, bad = pc.photo_line(res, t, ["u"])
        self.assertIn("Перерахував за фото", line)
        r2 = res["_relayout"]
        self.assertEqual((r2["layout"], r2["verdict"].startswith("BUY")), ((4, 16), True))
        self.assertIn("продаси як 2 пари 2×16", r2["type"])
        self.assertIn("für alle 4 zusammen", pc.seller_question(r2))
        posted = []
        saved = requests.post
        requests.post = lambda url, data=None, timeout=None: posted.append(data) or type("R", (), {"status_code": 200})()
        try:
            with mock.patch.object(pc, "photo_line", return_value=(line, bad)):
                pc.add_to_card(5, "🟢🟢 ДУЖЕ ВИГІДНО · +137 €", _json.dumps({"inline_keyboard": [[{"text": "🔗", "url": "u"}]]}),
                               res, t, ["u"])
        finally:
            requests.post = saved
        self.assertNotIn("+137", posted[0]["text"])                              # стара оцінка (2×32) — геть
        self.assertIn("4×16", posted[0]["text"])
        kb = _json.loads(posted[0]["reply_markup"])
        self.assertIn("für alle 4 zusammen", kb["inline_keyboard"][0][0]["copy_text"]["text"])
        # інший обсяг (16 ГБ × 2 при 64 у назві) — як і раніше «не збігається»
        bad_ph = dict(ph, ram_modules_visible=2)
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}), mock.patch.object(pc, "ask_gemini", return_value=bad_ph),                 mock.patch.object(pc, "_download", return_value=b"x"):
            res2 = dict(evaluate(t, 200), desc="x")
            self.assertIn("ФОТО НЕ ЗБІГАЄТЬСЯ", pc.photo_line(res2, t, ["u"])[0])

    def test_ai_failed_goes_on_top_of_ka_card(self):
        import requests
        import photo_check
        saved, posted = (photo_check.photo_line, requests.post), []
        photo_check.photo_line = lambda *a, **k: (photo_check.AI_FAILED, False)
        requests.post = lambda url, data=None, timeout=None: posted.append(data) or type("R", (), {"status_code": 200})()
        try:
            photo_check.add_to_card(5, "🟢 <b>ВИГІДНО</b> · +138 €", None, {"price": 350}, "(64GB Kit) 2x 32GB Micron", [])
        finally:
            photo_check.photo_line, requests.post = saved
        self.assertTrue(posted[0]["text"].startswith(photo_check.AI_FAILED))

    def test_desc_problem_rewrites_card(self):
        # 19.10: KA 3543763957 — картка «дуже вигідно», ШІ знайшов дефект в описі: увесь текст картки → ⛔, кнопки лишаються
        import requests

        import photo_check
        sent = []
        saved = (photo_check.photo_line, requests.post)
        photo_check.photo_line = lambda *a, **k: ("📷 <b>⛔ НЕ БЕРИ:</b> в описі: ПК падав — «Selten kam es zu Abstürzen»", True)
        requests.post = lambda url, data=None, **k: (sent.append(data), type("R", (), {"status_code": 200})())[1]
        try:
            add_to_card(9, "ДУЖЕ ВИГІДНО", '{"inline_keyboard": []}', {"price": 200}, "Corsair 2x32GB DDR4", ["u"], collapse_server=True)
        finally:
            photo_check.photo_line, requests.post = saved
        self.assertTrue(sent[0]["text"].startswith("<b>⛔ НЕ БЕРИ:</b> в описі: ПК падав"))
        self.assertNotIn("ВИГІДНО", sent[0]["text"])
        self.assertIn("Corsair 2x32GB DDR4</i> · 200 €", sent[0]["text"])
        self.assertIn("reply_markup", sent[0])


if __name__ == "__main__":
    unittest.main()
