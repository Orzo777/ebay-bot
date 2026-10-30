"""Регресійні тести для research/ram_alert.py і research/ram_mail_check.py на РЕАЛЬНИХ назвах
зі сповіщень Kleinanzeigen (22–23.09.2026). Без мережі."""
import os
import sys
import unittest

os.environ["RAM_PRICES_OFF"] = "1"   # фіксовані цифри Terapeak: щотижневе оновлення цін не змінює очікувані вердикти
sys.path.insert(0, ".")
sys.path.insert(0, "research")
import ram_alert
from ram_alert import buyer_message, evaluate, format_html, seller_template
from ram_mail_check import _ad_key, build_keyboard


class TestModuleCount(unittest.TestCase):
    def test_4x16_not_mixed_with_2x32(self):
        q = evaluate("Crucial 4x16GB DDR4 2666 RAM Kit 64GB", 65)   # 10.10: дві пари 2×16, а не 2×32
        self.assertIn("4×16", q["type"])
        self.assertNotEqual(q["quick_sale"], evaluate("Crucial 2x32GB DDR4 2666 RAM Kit 64GB", 65)["quick_sale"])
        self.assertEqual(evaluate("Crucial 2x32GB DDR4 2666 RAM Kit 64GB", 65)["verdict"], "BUY-EXCELLENT")

    def test_4x8_sodimm_is_skip(self):
        self.assertEqual(evaluate("4x 8GB DDR4 SO-DIMM RAM (Gesamt 32GB) für Laptop / Mini-PC", 65)["verdict"], "SKIP")

    def test_2x8_labelled_16_is_skip(self):
        self.assertEqual(evaluate("16GB DDR5-5600 RAM (2x 8GB) SK hynix SO-DIMM RAM", 90)["verdict"], "SKIP")

    def test_ambiguous_set_title_rejected(self):
        self.assertEqual(evaluate("Crucial 2 / 4 x 16GB DDR4 2666 RAM CT16G4DFD8266 32GB 64GB Set", 65)["verdict"], "UNKNOWN")

    def test_server_ecc_skip(self):
        self.assertEqual(evaluate("64GB (2x 32GB) DDR4-2933 ECC RDIMM Server RAM – Samsung", 90)["verdict"], "SKIP")


class TestAmbiguousKit(unittest.TestCase):
    def test_bare_64gb_ddr4_is_kit_check(self):
        r = evaluate("DDR4 RAM Arbeitsspeicher 64GB Vengeance", 100)
        self.assertTrue(r["verdict"].startswith("BUY"))
        self.assertTrue(r["kit_unknown"])
        self.assertFalse(r["single_module_warning"])
        self.assertIn("2x32", seller_template(r))
        self.assertIn("2×32", format_html(r))

    def test_real_single_16_gets_single_warning(self):
        r = evaluate("SK Hynix 16GB DDR5 SODIMM RAM, 5600MHz, PC5-5600B", 45)
        self.assertEqual(r["verdict"], "BUY-GOOD")   # 10.10: правило «≥ 50 € за медіаною»
        self.assertTrue(r["single_module_warning"])
        self.assertFalse(r["kit_unknown"])
        self.assertIn("EIN Riegel mit 16 GB", seller_template(r))

    def test_description_answers_kit_and_tested(self):
        r = evaluate("DDR4 RAM Arbeitsspeicher 64GB Vengeance", 200)   # 11.10: 100 € за фірмові 2×32 — підозріло дешево (фото наклейки)
        r["desc"] = "2x32GB Kit, mit MemTest getestet, ohne Rechnung."
        t = seller_template(r)
        self.assertEqual(t, 'Hallo, ich würde den RAM gern nehmen – reservierst du ihn mir? '
                            'Ich zahle gleich per „Sicher bezahlen", Versand und Gebühr übernehme ich. Viele Grüße')
        self.assertNotIn("Скільки планок", format_html(r))

    def test_no_silly_questions(self):
        # 11.10 (користувач): «18 Monate im PC, wegen Upgrade ausgebaut» — не питати, чи працює і чи є чек
        r = evaluate("Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200", 90)
        self.assertNotIn("Rechnung", seller_template(r))          # б/у пам'ять — чек не питаємо взагалі
        self.assertIn("fehlerfrei", seller_template(r))           # опис мовчить — питаємо, чи працювала
        r["desc"] = "War 18 Monate in meinem Gaming-PC verbaut, wegen Upgrade auf 64 GB ausgebaut."
        self.assertNotIn("fehlerfrei", seller_template(r))
        from console_alert import evaluate_console
        x = evaluate_console("Xbox Series X 1TB", 410)
        self.assertIn("Rechnung", seller_template(x))             # консоль — чек питаємо…
        x["desc"] = "Seit 2 Jahren genutzt, läuft einwandfrei."
        self.assertNotIn("Rechnung", seller_template(x))          # …але не після двох років користування

    def test_gemini_text_checked(self):
        import os
        from unittest import mock
        import seller_msg
        r = evaluate("Corsair Vengeance LPX 32GB (2x16GB) DDR4 3200", 90)
        good = ('Hi, ich würde den RAM gern für 85 € nehmen und gleich über Sicher bezahlen kaufen, Versand und Gebühr '
                'übernehme ich. Magst du ihn mir reservieren?')
        cases = [(good, good), ("Ich zahle 70 € per Überweisung", None), (good.replace("85", "80"), None),
                 (good.replace("Sicher bezahlen", "Kleinanzeigen"), None), ("", None)]
        with mock.patch.dict(os.environ, {"SELLER_MSG": "1", "GEMINI_API_KEY": "k"}):
            for got, want in cases:
                seller_msg._CACHE.clear()
                with mock.patch("photo_check.ask_gemini", return_value={"text": got}):
                    t = buyer_message(r, 85)
                if want:
                    self.assertEqual(t, want)
                else:   # підозріла відповідь — шаблон
                    self.assertTrue(t.startswith("Hallo, ich würde den RAM gern für 85 € nehmen"), got)
        seller_msg._CACHE.clear()


class TestNewTypes(unittest.TestCase):
    def test_ddr5_sodimm_2x16_kit(self):
        r = evaluate("Crucial 32GB Kit DDR5-4800 CL40 CT2K16G48C40S5 2x16GB SODIMM", 190)   # 14.10: від середини p25–медіана
        self.assertTrue(r["type"].startswith("DDR5 SO-DIMM 32 ГБ (2×16) кіт"))
        self.assertEqual(r["verdict"], "BUY")
        # 14.10: «торгуйся» — якщо після торгу (фікс. −10 €, VB −15 €) це «бери»
        self.assertEqual(evaluate("2x16 GB DDR5 RAM SODIMM Arbeitsspeicher", 205)["verdict"], "NEGOTIATE")
        self.assertEqual(evaluate("2x16 GB DDR5 RAM SODIMM Arbeitsspeicher", 215)["verdict"], "SKIP")
        self.assertEqual(evaluate("2x16 GB DDR5 RAM SODIMM Arbeitsspeicher", 215, vb=True)["verdict"], "NEGOTIATE")
        self.assertEqual(evaluate("2x16 GB DDR5 RAM SODIMM Arbeitsspeicher", 220, vb=True)["verdict"], "SKIP")

    def test_ddr5_sodimm_2x32_kit(self):
        # 26.09: поділились у бот Kingston FURY Impact 64GB (2x32) SO-DIMM за 420 € — бот не знав типу
        t = "Kingston FURY Impact 64GB (2x32GB) DDR5-5600 SO-DIMM KF556S40IBK2"
        r = evaluate(t, 560)   # 11.10: повний замір Terapeak, сегменти й швидкість
        self.assertTrue(r["type"].startswith("DDR5 SO-DIMM 64 ГБ (2×32) кіт"))
        self.assertEqual(r["verdict"], "SKIP")
        self.assertTrue(evaluate(t, 270)["verdict"].startswith("BUY"))
        self.assertEqual(evaluate(t, 450, vb=True)["verdict"], "NEGOTIATE")
        self.assertEqual(evaluate(t, 460, vb=True)["verdict"], "SKIP")   # 14.10: −15 € торгу не робить «бери»

    def test_types_added_26_09(self):
        # заміряні 26.09, коли з'ясувалось, що їх бракує
        self.assertEqual(evaluate("Corsair Vengeance DDR5 48GB 2x24GB 6000", 150)["type"], "DDR5 UDIMM 48 ГБ (2×24) кіт")
        self.assertEqual(evaluate("Corsair Vengeance DDR5 48GB 2x24GB 6000", 150)["verdict"], "BUY-EXCELLENT")
        self.assertTrue(evaluate("Kingston Fury Beast DDR5 16GB (2x8GB) 5200", 90)["type"].startswith("DDR5 UDIMM 16 ГБ (2×8) кіт"))
        self.assertTrue(evaluate("Crucial 32GB Kit DDR4 3200 SODIMM 2x16GB", 70)["type"].startswith("DDR4 SO-DIMM 32 ГБ (2×16) кіт"))
        self.assertIn("не купуємо", evaluate("Crucial DDR5 96GB Kit 2x48GB 5600", 200)["reason"])

    def test_ddr5_single_16_desktop(self):
        r = evaluate("Kingston FURY Beast DDR5 16GB 5200MT/s CL40 DIMM", 85)
        self.assertEqual(r["type"], "DDR5 UDIMM 16 ГБ (одна планка)")
        self.assertTrue(r["verdict"].startswith("BUY"))
        self.assertTrue(r["single_module_warning"])

    def test_not_added_types_still_skip(self):
        for t, p in [("Samsung 8GB DDR5-5600 SO-DIMM", 20),
                     ("Crucial DDR5 96GB Kit 2x48GB 5600", 200), ("Micron 16gb DDR5 sodimm 5600 2x 8Gb", 30)]:
            self.assertEqual(evaluate(t, p)["verdict"], "SKIP", t)


class TestPrices(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(evaluate("Samsung 64 GB SO-DIMM DDR4 2666 MHz Arbeitsspeicher (2 x 32GB)", 150)["verdict"], "BUY")   # 28.10: повільна DDR4 — від p25 (було BUY-GOOD)
        self.assertEqual(evaluate("Corsair Vengeance 64GB DDR5-6000 CL30 (2x32GB)", 450)["verdict"], "BUY-GOOD")
        self.assertEqual(evaluate("Crucial DDR5 16GB 5600 SODIMM", 145)["verdict"], "SKIP")


class TestTelegramCard(unittest.TestCase):
    def test_seller_text_fits_copy_button(self):
        for t, p in [("SK Hynix 16GB DDR5 SODIMM RAM, 5600MHz, PC5-5600B", 50),
                     ("DDR4 RAM Arbeitsspeicher 64GB Vengeance", 100),
                     ("Corsair Vengeance DDR5 RAM 32GB (2x16GB)", 180)]:
            s = seller_template(evaluate(t, p))
            self.assertLessEqual(len(s), 256, s)
            kb = build_keyboard("https://www.kleinanzeigen.de/s-anzeige/1", s)
            self.assertEqual(kb["inline_keyboard"][1][0]["copy_text"]["text"], s)

    def test_html_escaped(self):
        r = evaluate("Corsair <Vengeance> DDR5 RAM 32GB (2x16GB) & more", 180)
        h = format_html(r)
        self.assertIn("&lt;Vengeance&gt;", h)
        self.assertIn("&amp;", h)


class TestDedup(unittest.TestCase):
    def test_same_ad_same_price_same_key(self):
        a = dict(title="x", price=180.0, link="https://www.kleinanzeigen.de/s-anzeige/3520593662")
        b = dict(title="x (інший лист)", price=180.0, link="https://www.kleinanzeigen.de/s-anzeige/3520593662")
        self.assertEqual(_ad_key(a), _ad_key(b))

    def test_price_drop_is_new(self):
        a = dict(title="x", price=180.0, link="https://www.kleinanzeigen.de/s-anzeige/3520593662")
        b = dict(title="x", price=150.0, link="https://www.kleinanzeigen.de/s-anzeige/3520593662")
        self.assertNotEqual(_ad_key(a), _ad_key(b))


class TestModelFromDesc(unittest.TestCase):
    def test_model_only_when_unambiguous(self):
        from ram_alert import model_from_desc
        self.assertEqual(model_from_desc("Xbox zu verkaufen", "Meine Xbox Series X 1TB mit Controller"), "Xbox Series X")
        self.assertEqual(model_from_desc("Switch Nintendo", "Nintendo Switch 2 kaum benutzt"), "Nintendo Switch 2")
        self.assertIsNone(model_from_desc("Xbox zu verkaufen", "Xbox One S mit 2 Controllern"))
        self.assertIsNone(model_from_desc("Nintendo Spielkonsole", "Switch 2 oder Switch Lite"))
        self.assertIsNone(model_from_desc("DDR5 32GB", "Series X"))


class TestPerUnitPrice(unittest.TestCase):
    def test_capacity_per_module_is_not_price(self):
        from ram_alert import _D_PER_UNIT
        self.assertFalse(_D_PER_UNIT.search("16GB je Modul, 32GB insgesamt"))
        self.assertFalse(_D_PER_UNIT.search("Kapazität je Riegel: 16GB"))
        self.assertTrue(_D_PER_UNIT.search("Preis je Riegel"))
        self.assertTrue(_D_PER_UNIT.search("120 € je Riegel"))



class TestSwitchTabletDesc(unittest.TestCase):
    def test_incomplete_switch_by_description(self):
        from console_alert import evaluate_console
        from ram_alert import refine_by_desc
        ev = lambda t, p, vb=False: evaluate_console(t, p, vb=vb)
        r = evaluate_console("Nintendo Switch Konsole 32GB Pokemon Joycons", 85)
        self.assertEqual(refine_by_desc(r, r["title"], 85, False, "Die Switch kommt ohne Zubehör.", ev)["verdict"], "SKIP")
        self.assertEqual(refine_by_desc(r, r["title"], 85, False, "Lieferung ohne Joycons, Dock und Ladekabel", ev)["verdict"],
                         "SKIP")


class HaggleTest(unittest.TestCase):
    """13.10 (користувач): «торгуйся» на дорогих лотах з +30–40 € — мало; на дешевих 120 → 100 € — відчутно."""

    def test_expensive_low_percent_is_skip(self):
        # ціна, за якої зараз +10%: після торгу (−5% / VB −10%) усе одно < 25% — великі гроші в товарі за мало
        t = "Corsair Vengeance DDR5 32GB (2x16GB) 6000MHz"
        r0 = evaluate(t, 300)
        p = int(ram_alert.item_for_cost(r0["net_q"] / 1.10, r0["ship_in"]))
        self.assertGreater(p, 250)
        self.assertEqual(evaluate(t, p)["verdict"], "SKIP")
        self.assertEqual(evaluate(t, p, vb=True)["verdict"], "SKIP")

    def test_haggle_ten_or_fifteen_euro(self):
        # 14.10 (користувач): німці скидають 10–15 € незалежно від суми — «торгуйся» лише коли після цього це «бери»
        import re
        t = "Corsair Vengeance LPX DDR4 32GB (2x16GB) 3200"
        r0 = evaluate(t, 100)
        p = int(ram_alert.item_for_cost(r0["cap"] + 6, r0["ship_in"]))   # на 6–7 € дорожче за «бери до»
        r = evaluate(t, p)
        self.assertEqual(r["verdict"], "NEGOTIATE")
        self.assertEqual(ram_alert.offer_price(r), int(5 * round((p - 10) / 5)))   # −10 € з фіксованою ціною
        text = re.sub("<[^>]+>", "", ram_alert.format_html(r))
        self.assertRegex(text, r"^💬 ТОРГУЙСЯ · зараз \+\d+ € \(\+\d+%\)")
        m = re.search(r"🤝 Запропонуй \d+ € → \+(\d+) € \(\+(\d+)%\)", text)
        self.assertTrue(m and int(m.group(1)) >= 48 and int(m.group(2)) >= 19)   # після торгу — «бери» (≥ 50 € і ≥ 20%)
        p2 = int(ram_alert.item_for_cost(r0["cap"] + 12, r0["ship_in"]))
        self.assertEqual(evaluate(t, p2)["verdict"], "SKIP")              # −10 € не вистачає
        self.assertEqual(evaluate(t, p2, vb=True)["verdict"], "NEGOTIATE")   # з VB −15 € — вистачає
        self.assertEqual(ram_alert.offer_price(evaluate(t, p2, vb=True)), int(5 * round((p2 - 15) / 5)))

    def test_card_blocks(self):
        # 14.10: три блоки — що це, гроші, перевірки (у цитаті); рядок «📷» — у блок перевірок
        r = evaluate("Corsair Vengeance LPX DDR4 32GB (2x16GB) 3200", 80)
        r["risk_lines"] = ["🛡 Ризик шахрайства: 🟢 низький"]
        t = ram_alert.format_html(r)
        self.assertEqual(t.count("\n\n"), 2)
        self.assertIn("\n\n💶 <b>80 €</b>", t)
        self.assertTrue(t.endswith("</blockquote>"))
        t2 = ram_alert.add_check(t, "📷 Фото збігається з назвою ✓")
        self.assertTrue(t2.endswith("\n📷 Фото збігається з назвою ✓</blockquote>"))
        self.assertEqual(ram_alert.add_check("без перевірок", "📷 x"), "без перевірок\n\n📷 x")


class SetPriceInDescTest(unittest.TestCase):
    """15.10: «4 x 16GB DDR4 RAM 2666MHz (64GB Gesamt)» за 84 € — «ВИГІДНО +98 €», а в описі 84 € — за одну планку."""

    def test_set_price_from_description(self):
        from ram_alert import refine_by_desc
        t = "4 x 16GB DDR4 RAM 2666MHz (64GB Gesamt)"
        d = ("Verkaufe Crucual RAM bestehend aus: 1 x 16GB DDR4 SO-DIMM Modell: CT16G4S266M.M16FJ DDR4-2666 CL 19 1.2V "
             "Hersteller: Crucial by Micron Zustand: Gebraucht, technisch einwandfrei Nach Absprache auch Set mit: "
             "2 x 16GB DDR4 SO-DIMM (32GB RAM) -> EUR 159,00 4 x 16GB DDR4 SO-DIMM (64GB RAM) -> EUR 329,00")
        r = evaluate(t, 84)
        self.assertIn(r["verdict"], ram_alert.SEND_VERDICTS)        # за назвою — вигідно
        r2 = refine_by_desc(r, t, 84, False, d, lambda tt, pp, vb=False: evaluate(tt, pp, vb=vb))
        self.assertEqual(r2["verdict"], "SKIP")
        self.assertIn("ціна однієї планки; 4×16 ГБ — 329 €", r2["reason"])
        self.assertEqual(ram_alert.desc_set_price(d, 32), (2, 16, 159.0))

    def test_slow_ddr4_from_quick_sale_28_10(self):
        # 28.10 (рішення користувача): повільна DDR4 (до 2666) — від медіани 5 найдешевших вживаних живих оголошень сегмента
        # («найдешевший 135, далі 145 і 150 — рахуй ~145»), не нижче p25, не вище колишньої середини; мінус реклама 5%.
        # DDR4 3200+ і DDR5 — як було.
        from unittest import mock
        t = "Corsair Vengeance LPX 32GB (2x16GB) DDR4-2400"
        with mock.patch.object(ram_alert, "TOP_USED", {"ddr4|udimm|0|32|2": {"brand_slow": [130, 135, 140, 150, 160]}}):
            r = evaluate(t, 80)
            self.assertIn("повільна — продаж ≈ 140 € (медіана 5 найдешевших зараз ≈ 140 €)", r["type"])
            self.assertAlmostEqual(r["net_q"], 140 - ram_alert.costs(140) - 0.05 * 140, places=2)
        with mock.patch.object(ram_alert, "TOP_USED", {"ddr4|udimm|0|32|2": {"brand_slow": [170, 175, 180]}}):
            self.assertNotIn("≈ 175 € (", evaluate(t, 80)["type"].split("продаж")[1][:8])   # не вище колишньої середини
        with mock.patch.object(ram_alert, "TOP_USED", {"ddr4|udimm|0|32|2": {"brand_slow": [60, 65, 70]}}):
            low = evaluate(t, 80)
            self.assertIn(f"продаж ≈ {low['quick_sale']} €", low["type"])        # не нижче швидкого продажу
        none = evaluate(t, 80)
        self.assertIn("швидкий продаж + ¼ до медіани", none["type"])           # даних немає
        self.assertEqual(evaluate(t, 100)["verdict"], "SKIP")                   # наші №5 за ~100 € — тепер не «бери»
        self.assertIn(evaluate("Corsair Vengeance LPX 32GB 2x16GB DDR4 3200", 100)["verdict"], ram_alert.SEND_VERDICTS)
        self.assertNotIn("повільна", evaluate("G.Skill 32GB 2x16 DDR5 5200", 200).get("type", ""))

    def test_quad_both_ways_30_10(self):
        # 30.10 (користувач): «може вигідніше 2 по 2×16 ставити потім? таке теж треба рахувати, а не 4»
        r = evaluate("G.Skill Ripjaws V 64 GB DDR4 3200 MHz RAM Kit 4x16GB", 200)
        self.assertIn("продаси як 2 пари 2×16", r["type"])
        self.assertEqual(r["net_q"], evaluate("G.Skill Ripjaws V 64 GB DDR4 3200 MHz RAM Kit 4x16GB", 200, _as_pairs=True)["net_q"])

    def test_price_for_one_stick_28_10(self):
        # 28.10: KA 3555003483 «(64GB Kit) 2x 32GB Micron DDR5-5600» за 350 € — «ВИГІДНО», а в описі одна планка й
        # «Der Preis gilt für ein 32gb Ram!» — користувач ледь не купив
        from ram_alert import refine_by_desc
        t = "(64GB Kit) 2x 32GB Micron DDR5-5600 UDIMM RAM"
        d = ("Ich verkaufe hier ein hochwertiges DDR5-Arbeitsspeichermodul von Micron, das aus einem neuen Server stammt. Da der "
             "Server auf 128 GB aufgerüstet wurde, ist dieses Modul nun übrig.\n- 32 GB DDR5 UDIMM Desktop-RAM\n"
             "Der Riegel ist technisch in einem einwandfreien Zustand.\nDer Preis gilt für ein 32gb Ram!")
        ev = lambda tt, pp, vb=False: evaluate(tt, pp, vb=vb)   # noqa: E731
        r = evaluate(t, 350, vb=True)
        self.assertIn(r["verdict"], ram_alert.SEND_VERDICTS)
        r2 = refine_by_desc(r, t, 350, True, d, ev)
        self.assertEqual(r2["verdict"], "SKIP")
        self.assertIn("ціна за одну планку, увесь набір ≈ 700 €", r2["reason"])
        self.assertIn("💬 Про ціну: «Der Preis gilt für ein 32gb Ram!»", ram_alert.desc_line(dict(r, desc=d)))
        # без фрази про ціну — але опис лише про одну планку, а в назві 2× — теж за штуку
        r3 = refine_by_desc(r, t, 350, True, d.replace("Der Preis gilt für ein 32gb Ram!", ""), ev)
        self.assertEqual(r3["verdict"], "SKIP")
        # звичайний кіт: «die Riegel sind», «2x32GB» — нічого не міняємо
        ok = "Verkaufe mein Kit 2x32GB. Die Riegel sind getestet und laufen stabil. Preis VB."
        self.assertEqual(refine_by_desc(r, t, 350, True, ok, ev)["verdict"], r["verdict"])
        for s in ("Preis pro Riegel 120€", "Der Preis ist für einen Riegel", "Preis gilt für 1 Stück", "auch einzeln abzugeben"):
            self.assertTrue(ram_alert._D_PER_UNIT.search(s), s)
        self.assertFalse(ram_alert._D_PER_UNIT.search("Preis gilt für beide Riegel zusammen"))

    def test_new_price_is_not_the_price(self):
        self.assertIsNone(ram_alert.desc_set_price("Neupreis 4x16GB 329 €, jetzt günstig", 64))
        self.assertIsNone(ram_alert.desc_set_price("Damals für 4 x 16GB 299€ gekauft", 64))
        self.assertIsNone(ram_alert.desc_set_price("Verkaufe 2x8GB DDR4, 40 €", 64))   # інший обсяг


class BusinessSwitchTest(unittest.TestCase):
    """13.10: перемикач «комерційний акаунт eBay» — поки вимкнено; увімкнений додає комісію (5% ×1,19 + 0,95 €)."""

    def test_off_by_default(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BUSINESS_SELLER", None)
            self.assertFalse(ram_alert.business())
            self.assertAlmostEqual(ram_alert.costs(150), 6.19 + 1.0 + 0.03 * 2 * 6.19, places=6)

    def test_on_adds_fees(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"BUSINESS_SELLER": "1"}):
            fee = 0.05 * 1.19 * 155.49 + 0.80 * 1.19
            self.assertAlmostEqual(ram_alert.sell_fee(155.49)[0], fee, places=6)    # ≈ 10,20 € — приклад №2
            self.assertAlmostEqual(round(fee, 2), 10.20)
            self.assertAlmostEqual(ram_alert.costs(150), 0.0595 * 150 + 0.952 + 7.19 + 0.05 * (12.38 + 0.0595 * 150 + 0.952), places=6)
            self.assertAlmostEqual(ram_alert.after_tax(67.47), 47.229, places=3)
            self.assertEqual(ram_alert.after_tax(-5), -5)
            # межі купівлі нижчі: від тієї ж медіани лишається менше
        with mock.patch.dict(os.environ, {"BUSINESS_SELLER": "0"}):
            private = ram_alert.costs(150)
        self.assertLess(private, 9)


if __name__ == "__main__":
    unittest.main()


class TestPickup(unittest.TestCase):
    """Самовивіз у Гамбурзі (27.09): без пересилки й збору, готівкою; текст продавцю — «заберу й заплачу бар»."""

    def test_pickup_cheaper_than_shipping(self):
        from ram_alert import PICKUP_COST, apply_pickup, buyer_message
        ship = evaluate("Kingston Fury Beast DDR5 32GB (2x16GB) 6000", 230)
        pick = apply_pickup(evaluate("Kingston Fury Beast DDR5 32GB (2x16GB) 6000", 230))
        self.assertAlmostEqual(pick["buy_cost"], 230 + PICKUP_COST)
        self.assertLess(pick["buy_cost"], ship["buy_cost"])
        msg = buyer_message(pick)
        self.assertIn("bar", msg)
        self.assertNotIn("Sicher bezahlen", msg)
        self.assertLessEqual(len(msg), 256)

    def test_pickup_can_turn_skip_into_negotiate(self):
        from ram_alert import apply_pickup
        from console_alert import evaluate_console
        self.assertEqual(evaluate_console("Xbox Series X 1TB Konsole", 450, vb=True)["verdict"], "SKIP")
        self.assertIn(apply_pickup(evaluate_console("Xbox Series X 1TB Konsole", 450, vb=True))["verdict"],
                      ("BUY", "NEGOTIATE"))

    def test_hamburg_caps_match_model(self):
        import ram_mail_check as m
        from ram_alert import apply_pickup
        import console_alert as ca
        import ka_bounds as kb
        # стеля для самовивозу — найвища ціна, що ще дає картку (13.10: «торгуйся» за новим правилом)
        from ram_alert import sale_basis
        x = sale_basis(ca.XBOX_SERIES_X["p25"], ca.XBOX_SERIES_X["med"])   # 14.10: між швидким продажем і медіаною
        top = m.HAMBURG_MAX["xbox"]
        self.assertEqual(top, kb._pickup(x, ca.costs(x)))
        self.assertNotEqual(apply_pickup(ca.evaluate_console("Xbox Series X 1TB", top, vb=True))["verdict"], "SKIP")
        self.assertEqual(apply_pickup(ca.evaluate_console("Xbox Series X 1TB", top + 3, vb=True))["verdict"], "SKIP")
        self.assertTrue(m.is_pickup_search("Neue Anzeigen für „Konsolen - xbox series x in Hamburg“"))
        self.assertIn("l9409r30", m.public_search_url("Neue Anzeigen für „Konsolen - ps5 in Hamburg“"))


class TestAudit28Ram(unittest.TestCase):
    """Прогін 626 реальних оголошень KA (28.09): назви, які бот розумів неправильно."""

    def test_parse(self):
        from ram_parse import parse_title
        cases = {"2x Corsair Vengeance DDR5 16GB 5600MHz SODIMM RAM insg. 32GB neu": (32, 2),
                 "3x  32GB DDR5 SODIMM": (96, 3), "Crucial 64GB DDR5 RAM (1x64GB) 5600MHz SODIMM": (64, 1),
                 "NEU Corsair Vengeance DDR5 48GB (2x24GB) 5600MHz nicht 32GB 64GB": (48, 2),
                 "G.Skill 64-GB-Kit DDR 5 RAM": (64, 1), "32 GB DDR4 RAM 3200 MHz – 2x16 GB – Gaming PC": (32, 2),
                 "Corsair Vengeance 64GB Verpackung geöffnet DDR4": (64, 1),
                 "Samsung 16GB (2 Stk) DDR5 SODIMM 5600": (16, 2)}
        for t, (tot, mods) in cases.items():
            p, why = parse_title(t)
            self.assertIsNotNone(p, f"{t}: {why}")
            self.assertEqual((p["total"], p["modules"]), (tot, mods), t)
        for t in ["Gaming PC 7800x3d 64GB DDR5-6000 RTX4080 Super", "10 Stück DDR4 8GB",
                  "PC-Hardware-Bundle – Ryzen 7 3700X + 32 GB DDR4 + B550 Mainboard"]:
            self.assertIsNone(parse_title(t)[0], t)

    def test_evaluate(self):
        self.assertEqual(evaluate("2x 16GB DDR4 RAM (G.Skill Aegis & Crucial Ballistix)", 65)["verdict"], "SKIP")
        self.assertEqual(evaluate("RAM 1x32Gb 2x16Gb DDR 4", 1)["verdict"], "SKIP")        # ціна-заглушка
        r = evaluate("Kingston FURY Beast DDR5 32GB 5600 MHz CL40", 200)                  # кіт 2x16 без «2x16»
        self.assertTrue(r["verdict"].startswith("BUY") and r["kit_unknown"])
        r = evaluate("Kingston FURY Beast DDR5 16 GB Kit – 5200 MHz – CL40", 80)
        self.assertEqual(r["type"], evaluate("Kingston FURY Beast DDR5 2x8GB 5200", 80)["type"])
        self.assertIn("48", evaluate("48GB DDR5-6800 CL34 RGB – SK Hynix M-Die nicht 32GB 64GB", 250)["type"])
        r = evaluate("Crucial 64GB DDR5 RAM (1x64GB) 5600MHz SODIMM", 300)
        self.assertEqual(r["verdict"], "SKIP")                                              # 1x64 — не кіт 2x32


class TestPass3Parse(unittest.TestCase):
    def test_titles(self):
        from ram_parse import parse_title
        cases = {"16GB Corsair DDR4-3200 RGB Kit": (16, 1), "16GB G.Skill Aegis - 2mal 8 GB DDR4-3200": (16, 2),
                 "Corsair Vengeance RGB Pro CMW32GX4M2Z3600C18  DDR4 3600 MHz": (32, 2),
                 "Kingston FURY KF432C16BBK2/32 DDR4": (32, 2), "G.Skill F4-3200C16D-32GVK Ripjaws": (32, 2),
                 "Crucial CT2K16G4DFD832A DDR4": (32, 2), "16GB (2*8GB) SO-DIMM PC4-3200 SK Hynix": (16, 2),
                 "Crucial 8‑GB‑DDR4‑2133 Desktop‑RAM‑Modul": (8, 1), "Samsung RAM 16GB 2x8 5600MHz Laptop DDR5": (16, 2)}
        for t, exp in cases.items():
            p, why = parse_title(t)
            self.assertIsNotNone(p, f"{t}: {why}")
            self.assertEqual((p["total"], p["modules"]), exp, t)
        self.assertIsNone(parse_title("Corsair Light Enhancement Kit DDR4")[0])
        self.assertEqual(parse_title("Samsung M425R2GA3BB0-CQK 16GB DDR5")[0]["form"], "sodimm")



class TestQuadKits1010(unittest.TestCase):
    """Набори з 4 планок. 11.10: де є власні продажі (4×16 DDR4/DDR5, 4×8 DDR4) — свій тип (4×16 DDR4 продається за ~260 €,
    а як «дві пари» бот рахував ~338 € — завищено); 4×32 — як дві пари 2×32 (5% знижки)."""

    def test_quad_is_two_pairs(self):
        from ram_alert import QUAD_SHARE, REAL, SEGMENT, costs, evaluate, sale_basis
        key = ("ddr4", "udimm", False, 64, 2)
        pair = REAL[key]
        r = evaluate("Corsair Vengeance 128GB (4x32GB) DDR4 3200", 505)
        self.assertEqual(r["verdict"], "BUY")
        med = round(pair["med"] * SEGMENT[key]["brand_fast"][1])   # фірмові швидкі 2×32
        b = sale_basis(round(pair["p25"] * SEGMENT[key]["brand_fast"][0]), med)   # 14.10: між p25 і медіаною
        self.assertAlmostEqual(r["net_q"], QUAD_SHARE * 2 * (b - costs(b)))
        self.assertIn("4×32", r["type"])
        self.assertIn("2 пари", r["type"])
        self.assertEqual(r["quick_sale"], 2 * round(pair["p25"] * SEGMENT[key]["brand_fast"][0]))

    def test_quads_with_own_sales(self):
        from ram_alert import evaluate
        r = evaluate("G.Skill Ripjaws 64GB (4x16GB) DDR4 3200", 150)
        self.assertIn("4×16", r["type"])
        # 30.10 (користувач): рахуємо і набором 4×16 (Terapeak 11.10: фірмові від 2933 — медіана 317), і двома парами 2×16 —
        # беремо вигідніше
        pairs = evaluate("G.Skill Ripjaws 64GB (4x16GB) DDR4 3200", 150, _as_pairs=True)
        self.assertGreaterEqual(r["net_q"], pairs["net_q"])
        self.assertEqual(evaluate("G.Skill Ripjaws 64GB (4x16GB) DDR4 3200", 300)["verdict"], "SKIP")
        self.assertIn("4×16", evaluate("64GB DDR4 Quad-Kit G.Skill Trident Z 3200", 150)["type"])
        self.assertNotEqual(evaluate("Crucial Pro DDR5-5600 64GB (4x16GB) UDIMM", 300)["verdict"], "SKIP")
        # 18.10 (користувач): 4×8 не купуємо — 4 слоти мало в кого, продається погано; DDR5 4×8 — теж (не «дві пари»)
        self.assertEqual(evaluate("Corsair Vengeance LPX 32GB (4x8GB) DDR4", 60)["verdict"], "SKIP")
        self.assertIn("4 планок по 8", evaluate("Corsair Vengeance LPX 32GB (4x8GB) DDR4", 60)["reason"])
        self.assertEqual(evaluate("Kingston Fury 32GB (4x8GB) DDR5 5600", 90)["verdict"], "SKIP")
        self.assertEqual(evaluate("G.Skill 16GB (4x4GB) DDR4 3200", 20)["verdict"], "SKIP")

    def test_speed_segments(self):
        from ram_alert import evaluate
        fast = evaluate("Corsair Vengeance DDR5 32GB (2x16GB) 6000 CL30", 300)
        slow = evaluate("Corsair Vengeance DDR5 32GB (2x16GB) 5200 CL40", 300)
        self.assertEqual((fast["median_sale"], slow["median_sale"]), (393, 326))
        self.assertIn("від 6000 МГц", fast["type"])
        self.assertIn("до 5600 МГц", slow["type"])
        self.assertEqual(evaluate("Kingston FURY Beast DDR5 32GB 6000", 300)["median_sale"], 393)   # «6000» без «MHz»
        self.assertEqual(evaluate("Kingston FURY Beast DDR5 32GB (2x16GB)", 300)["median_sale"], 383)   # швидкість невідома
        self.assertEqual(evaluate("Samsung DDR5 32GB (2x16GB) 4800", 200)["median_sale"], 280)
