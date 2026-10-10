"""Регресійні тести для research/console_alert.py на РЕАЛЬНИХ назвах приватних оголошень
Kleinanzeigen (Konsolen, 24.09.2026). Без мережі."""
import os
import sys
import unittest

os.environ["RAM_PRICES_OFF"] = "1"
sys.path.insert(0, ".")
sys.path.insert(0, "research")
from console_alert import SUSPICIOUS_BELOW, evaluate_console
from ram_alert import buy_cost, desc_facts, evaluate, format_html, offer_price, offer_template, seller_template

# Перша Switch вимкнена 04.10 (console_alert.SWITCH1_ON); ці тести стережуть логіку її розпізнавання — на час модуля вмикаємо
def setUpModule():
    import console_alert
    console_alert._switch1_saved, console_alert.SWITCH1_ON = console_alert.SWITCH1_ON, True


def tearDownModule():
    import console_alert
    console_alert.SWITCH1_ON = console_alert._switch1_saved


def verdict(title, price):
    r = evaluate_console(title, price)
    return r["verdict"] if r else None


class TestConsoleListings(unittest.TestCase):
    def test_real_cheap_consoles_are_buy(self):
        # повна вартість = ціна + пересилка 11 € + Sicher bezahlen (0,50 € + 4,5%)
        self.assertEqual(verdict("Xbox Series X 1 TB, 2 Controller, 15 Spiele", 250), "BUY-EXCELLENT")   # 10.10: правило «≥ 50 € за медіаною» — межі вищі
        self.assertEqual(verdict("Xbox Series X 1TB + 2 Spiele + Controller + OVP - Top Zustand", 390), "BUY")
        self.assertEqual(verdict("Xbox Series X Konsole (1TB) mit Rechnung und drei Controller!", 410), "BUY")
        self.assertEqual(verdict("Xbox Series X mit Originell Kontroller", 450), "NEGOTIATE")
        self.assertEqual(verdict("Microsoft Xbox Series X 1TB Black 4K Wi-Fi inkl. Controller", 490), "SKIP")
        self.assertEqual(evaluate_console("Microsoft Xbox Series X 1TB Black", 470, vb=True)["verdict"], "NEGOTIATE")   # 10.10: правило «≥ 50 € за медіаною»

    def test_market_price_is_skip(self):
        self.assertEqual(verdict("Xbox Series X Konsole mit Controller und OVP", 500), "SKIP")
        self.assertEqual(verdict("Xbox Series X", 550), "SKIP")

    def test_accessories_are_skip(self):
        self.assertEqual(verdict("Xbox Series X Controller Robot White in Ovp", 55), "SKIP")
        self.assertEqual(verdict("Xbox Series X/S Speichererweiterung SSD 1TB Top!Kein Versand!!!", 150), "SKIP")
        self.assertEqual(verdict("Hori Racing Wheel Overdrive für Xbox Series X/S mit Halterung", 200), "SKIP")
        self.assertEqual(verdict("Xbox Series X Battle Beaver Controller", 165), "SKIP")

    def test_combos_swaps_and_other_consoles_skip(self):
        self.assertEqual(verdict("PS5 Digital + Xbox Series X 1TB + Elite Controller 2", 300), "SKIP")
        self.assertEqual(verdict("Tausche Playstation 5 + Edge Controller & Co gegen Xbox Series X", 200), "SKIP")
        self.assertEqual(verdict("Xbox Series X defekt für Bastler", 150), "SKIP")
        self.assertEqual(verdict("Microsoft Xbox Series X 1TB Digital Edition Robot White+Zubehör", 300), "SKIP")

    def test_too_cheap_is_skip(self):
        self.assertEqual(verdict("Xbox Series X", 120), "SKIP")

    def test_not_console_falls_through_to_ram(self):
        self.assertIsNone(evaluate_console("SK Hynix 16GB DDR5 SODIMM 5600MHz", 50))
        self.assertIsNone(evaluate_console("Xbox Series S 512GB", 150))

    def test_suspicious_warning_only_when_very_cheap(self):
        cheap = evaluate_console("Xbox Series X Konsole", SUSPICIOUS_BELOW - 30)
        normal = evaluate_console("Xbox Series X Konsole", SUSPICIOUS_BELOW + 50)
        self.assertTrue(any("Підозріло" in n for n in cheap["notes"]))
        self.assertFalse(any("Підозріло" in n for n in normal["notes"]))


class TestNegotiate(unittest.TestCase):
    def test_negotiate_offer_whole_sum(self):
        # 26.09 (вечір, ціни Terapeak p25 545): трохи понад стелю → торг; 09.10 комісія 0: стеля 403, 390 € → пропозиція 345
        r = evaluate_console("Xbox Series X Console", 450)   # 10.10: правило «≥ 50 € за медіаною» — межі вищі
        self.assertEqual(r["verdict"], "NEGOTIATE")
        self.assertEqual(offer_price(r), 400)
        self.assertIsNone(offer_template(r))   # «торгуйся»: один текст, одразу з пропозицією (29.09)
        t = seller_template(r)
        self.assertIn("400 €", t)
        self.assertTrue(t.startswith("Hallo! Ich nehme die Xbox Series X für 400 €"))   # хук: одразу рішення і сума
        self.assertIn("reservieren", t)
        self.assertIn("Versand und Gebühr übernehme ich", t)
        self.assertNotIn("noch da", t)
        self.assertIn("Xbox Series X", t)
        self.assertLessEqual(len(t), 256)
        self.assertIn("Запропонуй <b>400 €</b>", format_html(r))

    def test_negotiate_offer_not_above_cap(self):
        r = evaluate_console("Xbox Series X 1TB", 470, vb=True)   # 10.10: правило «≥ 50 € за медіаною» — межі вищі
        self.assertEqual(r["verdict"], "NEGOTIATE")
        self.assertLessEqual(buy_cost(offer_price(r), r["ship_in"]), r["cap"])   # разом — не вище стелі
        self.assertEqual(offer_price(r) % 5, 0)
        self.assertIn("ТОРГУЙСЯ", format_html(r))

    def test_good_and_excellent_have_no_offer(self):
        self.assertIsNone(offer_price(evaluate_console("Xbox Series X 1TB", 260)))
        self.assertIsNone(offer_template(evaluate_console("Xbox Series X 1TB", 220)))

    def test_tiny_discount_not_offered(self):
        # 270 € → ціль дала б 265 € (-2%): торгуватись за 5 € не варто, купуй як є
        self.assertIsNone(offer_price(evaluate_console("Xbox Series X 1TB", 270)))

    def test_far_above_cap_skip(self):
        self.assertEqual(verdict("Xbox Series X 1TB", 500), "SKIP")


class TestCard(unittest.TestCase):
    def test_card_uses_console_text_and_notes(self):
        r = evaluate_console("Xbox Series X 1TB mit Controller", 290)
        html = format_html(r)
        self.assertIn("Xbox Series X", html)
        self.assertIn("Sicher bezahlen", html)
        t = seller_template(r)
        self.assertNotIn("RAM", t)
        self.assertLessEqual(len(t), 256)
        # порядок: беру → умови → питання
        self.assertLess(t.index("reservieren"), t.index("Sicher bezahlen"))
        self.assertLess(t.index("Sicher bezahlen"), t.index("Laufwerk"))

    def test_description_answers_skip_questions(self):
        r = evaluate_console("Xbox Series X 1TB mit Controller", 290)
        r["desc"] = "Konsole läuft einwandfrei, Rechnung von MediaMarkt liegt bei."
        t = seller_template(r)
        self.assertNotIn("Laufwerk", t)
        self.assertNotIn("Rechnung", t)
        self.assertIn("справне", format_html(r))
        self.assertIn("є чек", format_html(r))

    def test_description_facts(self):
        self.assertEqual(desc_facts("Nicht getestet, keine Rechnung.")["works"], False)
        self.assertEqual(desc_facts("Nicht getestet, keine Rechnung.")["receipt"], False)
        self.assertEqual(desc_facts("Laufwerk defekt")["works"], False)
        self.assertIsNone(desc_facts("Privatverkauf ohne Gewähr.")["works"])   # юридична формула, не «не тестовано»
        self.assertIsNone(desc_facts(None)["receipt"])

    def test_ram_card_unchanged(self):
        r = evaluate("Crucial 2x32GB DDR4 2666 RAM Kit 64GB", 65)
        self.assertIn("RAM", seller_template(r))


class TestPS5(unittest.TestCase):
    # 26.09: PS5 Slim Disc — слабкий кандидат з міні-дослідження (KA 2 з 18 у зоні торгу)
    def test_disc_and_digital_types(self):
        self.assertEqual(evaluate_console("PS5 Slim Disc Edition 1TB", 300)["type"], "PS5 з дисководом (вживана)")
        self.assertEqual(evaluate_console("Playstation 5 Konsole mit 2 Controllern", 300)["type"], "PS5 з дисководом (вживана)")
        self.assertEqual(evaluate_console("PS5 Digital Edition Slim", 250)["type"], "PS5 Digital (вживана)")

    def test_rejects(self):
        for t, p in [("PS5 Pro 2TB", 700), ("PS5 Controller DualSense", 40), ("PS5 Konsole defekt", 100),
                     ("PS5 Spiele Paket", 60), ("Suche PS5 Slim", 300)]:
            self.assertEqual(evaluate_console(t, p)["verdict"], "SKIP", t)

    def test_negotiate_zone_and_offer(self):
        r = evaluate_console("PS5 Slim Disc Edition 1TB", 370, vb=True)   # 10.10: правило «≥ 50 € за медіаною» — межі вищі
        self.assertEqual(r["verdict"], "NEGOTIATE")
        self.assertIn("die PS5", seller_template(r))
        self.assertIn("PSN", seller_template(r))
        self.assertRegex(seller_template(r), r"für \d+ €")

    def test_not_ps5_goes_to_ram(self):
        self.assertIsNone(evaluate_console("Crucial 32GB DDR5 2x16GB", 100))


class TestSwitch2(unittest.TestCase):
    # 26.09: KA €220–270 при швидкому продажу на eBay €382 (вживані); ігри й аксесуари з «Switch 2» у назві — не консоль
    def test_console_vs_games(self):
        self.assertEqual(evaluate_console("Nintendo Switch 2 + Mario Kart World", 290)["verdict"], "BUY")   # 10.10: правило «≥ 50 € за медіаною» — межі вищі
        self.assertEqual(evaluate_console("Pokémon Legenden: Z-A - Nintendo Switch 2 Edition", 35)["verdict"], "SKIP")
        self.assertEqual(evaluate_console("Nintendo Switch 2 Pro Controller", 60)["verdict"], "SKIP")
        self.assertIsNone(evaluate_console("FeinTech SW212 HDMI 2.1 Switch 2x1 + Audio Extractor", 25))
        self.assertEqual(evaluate_console("Nintendo Switch OLED", 200)["verdict"], "SKIP")   # з 27.09 оцінюється, але дорого

    def test_sealed_cheap_warns(self):
        r = evaluate_console("Nintendo Switch 2 – neu & originalverpackt", 230)
        self.assertIn("шахрай", r["notes"][0])
        self.assertIn("die Switch 2", seller_template(r))


class TestFixes27(unittest.TestCase):
    def test_controller_limited_edition_is_not_ps5(self):
        # 27.09: «PS5 DualSense Controller LeBron James Limited Edition Neu OVP» 200 € прийшов у підписку PS5
        r = evaluate_console("PS5 DualSense Controller LeBron James Limited Edition Neu OVP", 200)
        self.assertEqual(r["verdict"], "SKIP")

    def test_switch_titles_from_share(self):
        # 27.09 «Поділитися → бот»: «Switch 2 zu verkaufen» і «Switch v2 Zelda Fanpaket» не розпізнавались
        self.assertEqual(evaluate_console("Switch 2 zu verkaufen", 180)["type"], "Nintendo Switch 2 (вживана)")
        self.assertEqual(evaluate_console("Nintendo Switch v2 Zelda Fanpaket", 150)["type"], "Nintendo Switch V1/V2 (вживана)")
        self.assertEqual(evaluate_console("Nintendo Switch OLED weiß", 150)["type"], "Nintendo Switch OLED (вживана)")
        self.assertEqual(evaluate_console("Nintendo Switch Lite türkis", 60)["type"], "Nintendo Switch Lite (вживана)")
        self.assertEqual(evaluate_console("Nintendo Switch Spiele Paket", 60)["verdict"], "SKIP")
        self.assertIsNone(evaluate_console("TP-Link Switch 8 Port Gigabit", 25))
        self.assertIsNone(evaluate_console("FeinTech HDMI Switch 2x1", 25))
        # 27.09, eBay: консоль з кабелем HDMI у назві — усе одно консоль; «Nicht OLED» — не OLED
        self.assertEqual(evaluate_console("Nintendo Switch Oled Konsole schwarz mit Dock, 2 Joy-Con, HDMI Kabel", 170)["type"],
                         "Nintendo Switch OLED (вживана)")
        self.assertEqual(evaluate_console("Nintendo Switch 1 Version 2 (Nicht OLED)", 165)["type"], "Nintendo Switch V1/V2 (вживана)")


class TestAccessoryStructure(unittest.TestCase):
    # 27.09: сканування eBay показало аксесуари, що проходили як консолі
    ACC = ["READY 2 GAMING HURRICANE WHEEL PRO Lenkrad mit Pedalen Nintendo Switch",
           "Nintendo Switch Joy-Con Pair 2er-Set Pastel Pink Pastell Rosa NEU in OVP",
           "Nintendo NES Controller 2er-Set für Nintendo Switch NEU/OVP",
           "Nintendo Switch Mini-Dockingstation Switch 4K HDMI USB Ethernet JCD620",
           "Nintendo Switch Joy Cons Pastell-Rosa",
           "Playstation 5 PS5 Faceplate Cover Slim Marvel Wolverine Gelb Limited Edition",
           "PS5 DualSense Controller LeBron James Limited Edition Neu OVP",
           "Xbox Series X Speichererweiterung 1TB Seagate",
           "Disc Laufwerk für PS5 Slim Digital"]
    CON = ["Xbox Series X 1 TB, 2 Controller, 15 Spiele", "Xbox Series X mit Originell Kontroller",
           "Microsoft Xbox Series X 1TB Black 4K Wi-Fi inkl. Controller", "PS5 Slim Disc Edition + Controller",
           "PS5 Slim mit Laufwerk", "Nintendo Switch 2 + Mario Kart World", "Nintendo Switch Konsole mit Joy-Con",
           "Nintendo Switch OLED Konsole mit Dock, 2 Joy-Con, HDMI Kabel", "Playstation 5 Konsole mit 2 Controllern",
           "Nintendo Switch mit 2 Pro Controllern und original LAN Dockingstation"]

    def test_accessories(self):
        for t in self.ACC:
            r = evaluate_console(t, 200)
            self.assertTrue(r is None or r["verdict"] == "SKIP" and not r.get("type"), t)

    def test_consoles(self):
        for t in self.CON:
            self.assertTrue(evaluate_console(t, 300).get("type"), t)


class TestEbayTitles27(unittest.TestCase):
    """Хибні «консолі» з категорії Konsolen на eBay.de (27.09): ігри-колекційки, послуги, дисковод, Portal, Japan."""

    def test_not_consoles(self):
        for t in ["The Blood of Dawnwalker Collector's Edition - [XBOX Series X]",
                  "007 First Light Legacy Edition - [PlayStation 5]",
                  "Gothic Remake Collector‘s Edition Neu Ps5 Playstation 5 Limited",
                  "SONY PlayStation Porta Remote-Player für PS5 -Konsole ! TOP",
                  "Playstation 5 Platinum 5x Legit timestamps",
                  "Sony Disc-Laufwerk für PS5 Digital Edition Konsole (PlayStation 5)",
                  "Gran Turismo Samlung Factory Seald Pixel VGA Wata PS5 4 3 2",
                  "Nintendo Switch 2 Ersatzkonsole | JAPAN only | OVP | wie neu"]:
            self.assertEqual(evaluate_console(t, 200)["verdict"], "SKIP", t)

    def test_real_consoles_still_pass(self):
        for t in ["Sony PlayStation 5 Slim Disc Edition Konsole 1TB", "Nintendo Switch 2 Konsole 256 GB mit OVP",
                  "Microsoft Xbox Series X 1TB Konsole inkl. Controller"]:
            self.assertTrue(evaluate_console(t, 250).get("type"), t)



class TestPass6to8Consoles(unittest.TestCase):
    def test_not_consoles(self):
        for t, p in [("PS5 God of wae Controller's", 300), ("PS5 Portable", 200), ("ACHTUNG PS5 Slim Betrug", 300),
                     ("PlayStation 5 Controller GTA VI Limited", 279),
                     ("Sony PS5 Blu-Ray Edition Spielekonsole - Weiß (Laufwerk liest keine Disks mehr)", 251),
                     ("Playstation PS5 Pulse Explore Wireless Earbuds NEU + OVP", 200),
                     ("PS5 eXcluziv3 Gaming PS-One Retro Edition! ähnl. wie Scuf, AIM, Kings", 180),
                     ("Nintendo Switch OLED | V1/V2 | verschiedene Farben | Auswahl", 115),
                     ("Nintendo Switch 2 Pokemon Legenden Z - A + Vorbesteller Boni - NEU & OVP", 160),
                     ("Nintendo Switch 2 Metroid Prime 4: Beyond – Power-Set + Schlüsselanhänger NEU", 160),
                     ("MARIO TENNIS FEVER + Tennisball Vorbesteller Bonus Nintendo Switch 2", 160),
                     ("Pokémon Scarlet & Violet Dual Pack SteelBook Edition Nintendo Switch", 60),
                     ("Nintendo Switch Mario Kart 8 Deluxe", 55)]:
            self.assertEqual(evaluate_console(t, p)["verdict"], "SKIP", t)
            self.assertFalse(evaluate_console(t, p).get("type"), t)

    def test_still_consoles(self):
        for t, p in [("Nintendo Switch Konsole V2 grau", 100), ("Nintendo Switch mit 3 Spielen", 100),
                     ("Nintendo Switch Komplettset - 7 Spiele", 100), ("Nintendo Switch Lite türkis", 50),
                     ("Nintendo Switch OLED weiß", 110), ("Nintendo Switch 2 + Mario Kart World Bundle", 380),
                     ("Switch 2 – Top Zustand – 2x Pro Controller", 419), ("PS5 Disk Edition mit Controller und 3 Spielen", 300)]:
            self.assertTrue(evaluate_console(t, p).get("type"), t)


class TestPass9Consoles(unittest.TestCase):
    def test_switch_consoles_without_konsole_word(self):
        for t in ["Nintendo Switch Animal Crossing Edition", "Nintendo Switch 2017 .", "Nintendo Switch + 3 Spiele + Controller",
                  "Nintendo Switch Plus 2 Spiele", "Nintendo Switch rot blau", "Nintendo Switch, guter Zustand"]:
            self.assertTrue(evaluate_console(t, 100).get("type"), t)

    def test_game_words_with_console_proof(self):
        for t, p in [("PS5 Slim Disc + 2 DualSense Dual Pack Bundle", 300), ("Xbox Series X 1TB Konsole + Steelbook Starfield", 300)]:
            self.assertTrue(evaluate_console(t, p).get("type"), t)

    def test_models(self):
        self.assertIn("Switch 2", evaluate_console("Nintendo Switch 2Schwarz mit Dock, Pro Controller2 und zwei Spiele", 200)["type"])
        self.assertIn("Digital", evaluate_console("Sony Playstation 5, CFI-1216B mit 1 Controller", 221)["type"])
        self.assertEqual(evaluate_console("Nintendo Switch Konsole 32 GB HAC-001 (01) nur Tablett", 90)["verdict"], "SKIP")



class TestPass11Consoles(unittest.TestCase):
    def test_switch2_with_punctuation(self):
        for t in ["Nintendo Switch 2, 256 GB, schwarz", "Nintendo Switch 2. Top Zustand", "Nintendo Switch 2,OVP"]:
            self.assertIn("Switch 2", evaluate_console(t, 250).get("type") or "", t)

    def test_switch_games_are_not_consoles(self):
        for t in ["Nintendo Switch Mario Kart 8 Deluxe Zustand gut", "Pokemon Karmesin Nintendo Switch Edition",
                  "Mario Kart 8 Deluxe Nintendo Switch gebraucht", "Nintendo Switch Mario Kart 8 Deluxe (2017)",
                  "Nintendo Switch Sports + Beingurt", "Ring Fit Adventure (inkl. Ring-Con & Beingurt) - Nintendo Switch",
                  "Nitro Deck Retro Switch Limitierte Edition, Nintendo Switch",
                  "Konsolen Spiele Konvolut / Xbox360, Ps4 und Nintendo Switch - gebraucht"]:
            for p in (50, 80):
                # pass 12: слабкі ознаки в назві → консоль лише якщо опис підтверджує; гра з описом гри — SKIP
                r = evaluate_console(t, p)
                if r["verdict"] != "SKIP":
                    self.assertTrue(r.get("needs_console_proof"), (t, p))
                    from ram_alert import refine_by_desc
                    ev = lambda tt, pp, vb=False: evaluate_console(tt, pp, vb=vb)
                    for desc in ("Spiel in Originalhülle, Modul läuft einwandfrei.", "", None):
                        self.assertEqual(refine_by_desc(r, t, p, False, desc, ev)["verdict"], "SKIP", (t, p, desc))

    def test_switch_tablet_only(self):
        for t in ["ORIGINAL NINTENDO SWITCH GAMEPAD Tablet HAC-001 (-01) ERSATZ KONSOLE XAJ #2",
                  "Nintendo Switch V2, nur Konsole, XKJ100402xxx"]:
            self.assertEqual(evaluate_console(t, 85)["verdict"], "SKIP", t)

    def test_real_switch_still_ok(self):
        for t in ["Nintendo Switch Konsole V2 grau", "Nintendo Switch mit 3 Spielen", "Nintendo Switch OLED weiß"]:
            self.assertTrue(evaluate_console(t, 100).get("type"), t)

    def test_misc(self):
        self.assertIn("Digital", evaluate_console("Sony PS5 Slim CFI-2016 B01Y Konsole", 250)["type"])
        self.assertEqual(evaluate_console("Xbox Series X Display Riss", 250)["verdict"], "SKIP")



class TestPass12Regressions(unittest.TestCase):
    def _ev(self, t, p, d=None):
        from ram_alert import refine_by_desc
        r = evaluate_console(t, p)
        if d is not None:
            r = refine_by_desc(r, t, p, False, d, lambda tt, pp, vb=False: evaluate_console(tt, pp, vb=vb))
        return r["verdict"]

    def test_weak_switch_confirmed_by_description(self):
        self.assertNotEqual(self._ev("Nintendo Switch mit Schutztasche", 55, "Die Konsole funktioniert, mit Dock und Joy-Cons"), "SKIP")
        self.assertNotEqual(self._ev("Nintendo Switch Grau sehr guter Zustand", 55, "Nintendo Switch Konsole mit Ladekabel"), "SKIP")

    def test_not_rejected(self):
        for t, p in [("Microsoft Xbox Series X 1TB Videospielkonsole - Schwarz", 250), ("PS5 Slim Disc + 2 Controller + 3 Videospiele", 250),
                     ("Nintendo Switch OLED Konvolut mit 5 Spielen", 80), ("Xbox Series X 1TB – kein Riss, keine Kratzer", 250),
                     ("Nintendo Switch 2 NEU OVP Siegel nicht gebrochen", 250), ("PS5 Disc Edition Siegel ungebrochen NEU OVP", 250),
                     ("Nintendo Switch OLED + Ring Fit Adventure + 2 Spiele", 80), ("Xbox Series X Konvolut mit 2 Controllern", 250),
                     ("Nintendo Switch OLED nur Konsole mit Dock und Joy-Cons", 80), ("Nintendo Switch V2 nur Konsole, keine Spiele", 55),
                     ]:
            self.assertNotEqual(self._ev(t, p), "SKIP", t)
        # 10.10: Switch Lite (продається за ~80–100 €) не дає 50 € навіть за безцінь — перевіряємо лише, що консоль розпізнано
        for t in ["Nintendo Switch Lite nur Konsole", "Nintendo Switch Lite grau ohne Zubehör"]:
            self.assertIn("net_q", evaluate_console(t, 40), t)

    def test_description_not_incomplete(self):
        for d in ["Funktioniert einwandfrei ohne Probleme im Dock", "laufen ohne Joy-Con Drift", "Switch wie neu, ohne Kratzer am Dock",
                  "Versand ohne Dock möglich", "Verkauft wird nur die Konsole, Dock, Joy-Cons und Netzteil"]:
            self.assertNotEqual(self._ev("Nintendo Switch Konsole V2 32GB", 55, d), "SKIP", d)
        self.assertEqual(self._ev("Nintendo Switch Konsole V2 32GB", 55, "Lieferung ohne Joycons, Dock und Ladekabel"), "SKIP")


class TestSwitch2Games0110(unittest.TestCase):
    """01.10 (eBay-аукціон): «EA Sports FC 26 - Nintendo Switch 2» за €10 пішов як консоль."""
    def test_game_titles_skipped(self):
        for t in ("EA Sports FC 26 - Nintendo Switch 2", "Donkey Kong Bananza Switch 2",
                  "Pokémon Legends Z-A Nintendo Switch 2 Edition"):
            r = evaluate_console(t, 10)
            self.assertEqual(r["verdict"], "SKIP", t)
            self.assertIn("гру", r["reason"], t)

    def test_console_titles_still_evaluated(self):
        for t in ("Nintendo Switch 2 Konsole", "Mario Kart World Bundle Nintendo Switch 2", "Neue Nintendo Switch 2",
                  "Verkaufe meine Switch 2", "Switch 2 Mario Kart World Set", "Nintendo Switch 2 schwarz neuwertig",
                  "Nintendo Switch 2 + Mario Kart World"):
            self.assertNotEqual(evaluate_console(t, 250)["verdict"], "SKIP", t)


if __name__ == "__main__":
    unittest.main()


class TestBundleTitles(unittest.TestCase):
    """27.09: «PS5 … Disk slim Version 2 Controller Bundle» (€280) бот назвав аксесуаром."""

    def test_console_bundles(self):
        for t in ["PS5 Sony Playstation 5 Disk slim Version 2 Controller Bundle",
                  "PS5 Digital 2 Controller", "Xbox Series X 1TB 2 Controller",
                  "Nintendo Switch 2 256GB Joy-Con Set"]:
            self.assertTrue(evaluate_console(t, 280).get("type"), t)

    def test_still_accessories(self):
        for t in ["PS5 Slim Faceplate Cover", "PS5 Controller Bundle 2x", "PS5 Slim Controller",
                  "PS5 Slim Disc Laufwerk", "Xbox Series X Speichererweiterung 1TB"]:
            self.assertEqual(evaluate_console(t, 200)["verdict"], "SKIP", t)


class TestAudit28(unittest.TestCase):
    """Прогін 626 реальних оголошень KA (28.09)."""

    def test_consoles(self):
        for t in ["Xbox Series X 1TB SSD", "Xbox Series X 1TB SSD Bundle inkl. Forza Horizon 5",
                  "X-Box Serie x   Kaum bespielt - 1 TB", "Nintendo Switch Komplettset - 7 Spiele",
                  "Sony PS5 Slim 1TB SSD Disc Edition"]:
            self.assertTrue(evaluate_console(t, 300).get("type"), t)

    def test_accessories(self):
        for t in ["WD Black SN850 1TB SSD für PS5", "Seagate Speichererweiterung 1TB SSD für Xbox Series X",
                  "PS5 1TB SSD Erweiterung", "Xbox Series X/S Controller schwarz"]:
            self.assertEqual(evaluate_console(t, 200)["verdict"], "SKIP", t)


class TestPass3Ebay(unittest.TestCase):
    """28.09, прохід по ~1000 свіжих оголошень eBay."""

    def test_consoles(self):
        for t in ["Sony PlayStation®5 Digital Edition Spielekonsole Weiß",
                  "Sony PlayStation 5 PS5 Blu-Ray 825GB PAL 4K DualSense Controller Standfuß",
                  "Sony PlayStation 5 Digital Edition 825 GB Weiß 4K HDR DualSense Kabel",
                  "Sony Playstation 5 Disc Edition 825GB CFI-1216A Ohne Spiel Sehr Gut",
                  "Sony PlayStation 5 Digital with Two Controllers",
                  "XBOX SERIES X + Elite Controller Series 2 + Forza Horizon 6", "Play Station 5 / 1 TB"]:
            self.assertTrue(evaluate_console(t, 300).get("type"), t)

    def test_not_consoles(self):
        for t in ["Fn ACC Ps5", "PlayStation 5 Disk Laufwerk", "Sony PlayStation 5 Controller GTA VI Limited schwarz",
                  "PS5 AimControllers Inkl. Paddles, Smart Bumpers L2R2, Grip"]:
            self.assertEqual(evaluate_console(t, 300)["verdict"], "SKIP", t)


class TestPass3bBundles(unittest.TestCase):
    def test_price_aware_bundles(self):
        for t, p in [("Nintendo Switch 2 - Top Zustand - Restgarantie - 2x Switch Pro Controller", 419),
                     ("Nintendo Switch Paket! 4 Joycons, 2 Controller, 3 Spiele und Speicherkarte", 250),
                     ("Nintendo Switch HAC-001 32GB ungepatched | 2 Paar Joy-Cons | 6 Spiele | OVP", 300)]:
            self.assertTrue(evaluate_console(t, p).get("type"), t)
        for t, p in [("Sony PlayStation 5 Controller GTA VI Limited schwarz", 279), ("Nintendo Switch Joy-Con Pair 2er-Set", 80),
                     ("Virtual Boy für Nintendo Switch & Nintendo Switch 2 - NEU & OVP", 160)]:
            self.assertEqual(evaluate_console(t, p)["verdict"], "SKIP", t)



class TestRetro0910(unittest.TestCase):
    """09.10: «Atari 2600 Konsole von 1991 + … PS5 Controller» за 260 € → BUY (eBay-сторож)."""

    def test_retro_with_our_console_word_is_skipped(self):
        from console_alert import evaluate_console
        for t in ["Atari 2600 Konsole von 1991 + Neu ohne Folie + 100 % Komplett und Fun + PS5 Controller",
                  "Sega Mega Drive + Xbox Series X Spiel", "Wii U Konsole + Switch Pro Controller", "PS2 Slim + PS5 Spiele Sammlung"]:
            r = evaluate_console(t, 260)
            self.assertEqual(r["verdict"], "SKIP", t)
            self.assertIn("ретро", r["reason"], t)
        for t, p in [("Sony PlayStation 5 Slim Disc 1TB", 300), ("Xbox Series X 1TB", 300), ("PS5 Konsole + 3 Spiele", 300)]:
            self.assertNotEqual(evaluate_console(t, p)["verdict"], "SKIP", t)


    def test_no_signal_in_title_is_defect(self):
        from console_alert import evaluate_console
        for t in ["Ps5 Slim 1TB hmdi kein signal", "PS5 Konsole kein Bild", "Xbox Series X no signal", "PS5 HDMI defekt"]:
            self.assertEqual(evaluate_console(t, 270)["verdict"], "SKIP", t)
        self.assertNotEqual(evaluate_console("PS5 Slim 1TB mit HDMI Kabel", 300)["verdict"], "SKIP")
