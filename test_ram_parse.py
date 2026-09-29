"""Тести розбору назв RAM (ram_parse.py). Кожен випадок — реальний тип назви з eBay.de."""
import unittest

from ram_parse import parse_title, parse_speed, parse_capacity


def P(t):
    r, why = parse_title(t)
    return r if r else why


class ParseTitleTests(unittest.TestCase):
    def test_user_example(self):
        r = P("SK Hynix DDR5 UDIMM 32GB 2x16GB PC5-5600B RAM")
        self.assertEqual((r["gen"], r["form"], r["total"], r["modules"], r["speed"], r["brand"]),
                         ("ddr5", "udimm", 32, 2, 5600, "SK Hynix"))
        self.assertTrue(r["kit"] and r["oem"])

    def test_kit_notations(self):
        for t in ["Crucial 32GB Kit (2x16GB) DDR5-5600 CL46", "Corsair 2 x 16 GB DDR5 5600", "Kit 16GB x2 DDR5 5600 Kingston",
                  "DDR5 32GB (2x16) 5600 Kingston Fury"]:
            r = P(t)
            self.assertEqual((r["total"], r["modules"]), (32, 2), t)

    def test_single_and_ambiguous(self):
        self.assertEqual(P("Kingston Fury Beast 16GB DDR4 3200 MT/s")["modules"], 1)
        self.assertIn("варіац", P("8GB DDR4 16GB DDR4 32GB Auswahl"))        # варіативний лот → відкинути
        self.assertIn("не вказана", P("Kingston DDR4 RAM Arbeitsspeicher"))

    def test_conflicting_capacity_is_rejected_not_guessed(self):
        self.assertIn("суперечливі", P("64GB Kit (2x16GB) DDR4 3200"))          # 64 ≠ 2×16
        self.assertEqual(P("16GB (2x8GB) DDR4 3200")["total"], 16)              # консистентно — ок

    def test_form_factor(self):
        self.assertEqual(P("Samsung 16GB DDR4 SODIMM 2666 Laptop")["form"], "sodimm")
        self.assertEqual(P("16GB DDR4 3200 für iMac 27 Zoll RAM")["form"] if isinstance(P("16GB DDR4 3200 für iMac RAM"), dict) else "x", "sodimm")
        self.assertEqual(P("Micron 64GB DDR4 2666 RDIMM ECC Reg")["form"], "server")
        self.assertEqual(P("Corsair Vengeance 16GB DDR4 3200")["form"], "udimm")
        self.assertTrue(P("Micron 32GB DDR5 ECC UDIMM 5600")["ecc"])

    def test_generation_from_pc_code(self):
        r = P("Hynix 8GB PC4-2666V-UA2 RAM")
        self.assertEqual(r["gen"], "ddr4")
        self.assertEqual(r["speed"], 2666)
        self.assertEqual(P("4GB PC3L-12800S SODIMM")["gen"], "ddr3")

    def test_speed(self):
        self.assertEqual(parse_speed("ddr4-3200", "ddr4"), 3200)
        self.assertEqual(parse_speed("pc4-25600", "ddr4"), 3200)      # МБ/с → MT/s
        self.assertEqual(parse_speed("pc5-44800", "ddr5"), 5600)
        self.assertEqual(parse_speed("pc3-12800", "ddr3"), 1600)
        self.assertEqual(parse_speed("6000 mhz", "ddr5"), 6000)
        self.assertIsNone(parse_speed("ddr4 kit", "ddr4"))

    def test_rejects_non_modules_lots_and_defects(self):
        self.assertIn("не модуль", P("RAM Kühler Heatsink DDR5"))
        self.assertIn("не модуль", P("Lenovo ThinkPad T480 16GB DDR4 RAM 256GB SSD"))
        self.assertIn("лот", P("10x 8GB DDR4 RAM Lot"))
        self.assertIn("лот", P("Konvolut DDR3 RAM 4GB"))
        self.assertIn("дефект", P("16GB DDR5 5600 defekt"))
        self.assertIn("LPDDR", P("16GB LPDDR5 onboard"))

    def test_brand_priority(self):
        self.assertEqual(P("G.Skill Trident Z5 RGB 32GB (2x16GB) DDR5-6000")["brand"], "G.Skill")
        self.assertEqual(P("Kingston Fury Beast 16GB DDR5")["brand"], "Kingston")
        self.assertEqual(P("Crucial Ballistix 16GB DDR4 3200")["brand"], "Crucial")
        self.assertEqual(P("Samsung M471A2K43DB1-CTD 16GB DDR4 SODIMM")["brand"], "Samsung")
        self.assertEqual(P("NoName 8GB DDR4 3200")["brand"], "other")

    def test_regression_gb_followed_by_s_word(self):
        # баг 21.09: «32GB Samsung…» відкидалось, бо S після GB сприймалось як «GB/s»
        for title, gb in [("32GB Samsung Ram PC5-5600B SO 5600Mhz fuer Acer Notebook", 32), ("8GB SK Hynix DDR4 2666 SODIMM", 8),
                          ("16GB Server RAM DDR4 ECC", 16), ("8GB SODIMM DDR3 1600", 8)]:
            r = P(title)
            self.assertIsInstance(r, dict, title)
            self.assertEqual(r["total"], gb, title)
        self.assertIn("не вказана", P("DDR4 3200 25.6 GB/s Arbeitsspeicher"))      # GB/s — це швидкість

    def test_ddr3l_and_multiplication_sign(self):
        self.assertEqual(P("Ramaxel DDR3L 16GB Kit (4x4GB) PC 1600MHz")["gen"], "ddr3")
        r = P("Crucial 32GB DDR4 RAM Kit (2×16GB) DDR4-3200 SO-DIMM")
        self.assertEqual((r["total"], r["modules"], r["form"]), (32, 2, "sodimm"))

    def test_big_server_kit(self):
        r = P("8x 64GB DDR4 3200MHz ECC REG RDIMM Server")
        self.assertEqual((r["total"], r["modules"]), (512, 8))

    def test_brand_ignores_compatibility_phrase(self):
        # «für HP ProDesk» — сумісність, а не виробник модуля (без цього бренд HP мав €80/ГБ)
        self.assertEqual(P("8GB Samsung DDR4 2400 Mhz RAM SO-DIMM für HP Business Desktop")["brand"], "Samsung")
        self.assertEqual(P("16GB DDR4 3200 RAM passend für Dell Optiplex")["brand"], "other")
        self.assertEqual(P("Kingston 16GB DDR4 SODIMM for Lenovo ThinkPad")["brand"], "Kingston")

    def test_capacity_direct(self):
        self.assertEqual(parse_capacity("2x8gb"), (16, 2, None))
        self.assertEqual(parse_capacity("32gb"), (32, 1, None))
        self.assertEqual(parse_capacity("7gb")[0], None)



class TestPass6to8(unittest.TestCase):
    """Проходи 6–8 (29.09, живі KA + eBay)."""
    def test_rejected(self):
        for t in ["DDR 4 4 8 16 32 gb Arbeitsspeicher", "8/16/32GB DDR4 RAM", "ACHTUNG DDR5 32GB Betrüger"]:
            self.assertIsInstance(P(t), str, t)

    def test_forms_and_counts(self):
        cases = {"Kingston Fury Impact DDR5 32GB 5600": ("sodimm", 32, 1),
                 "Kingston 32GB DDR4 DIMM KCP426SD8/32": ("sodimm", 32, 1),   # «S» після частоти — SO-DIMM
                 "Crucial Pro DDR5 32GB RAM CP32G56C46U5.C8B": ("udimm", 32, 1),
                 "Crucial 32GB CT16G4DFRA32A DDR4": ("udimm", 32, 2),
                 "Samsung 1*32 GB sodimm ddr 4m, 2666er": ("sodimm", 32, 1),
                 "Patriot Viper 4 | 32GB (2x16GB) | 3200 MHz | PV432G320C6K | DDR4 RAM": ("udimm", 32, 2),
                 "SK Hynix 32GB HMAA4GS6AJR8N-XN DDR4 260-pin": ("sodimm", 32, 1),
                 "16GB DDR4 Ram SK Hynix HMA82GS6JJR8N": ("sodimm", 16, 1),
                 "SO DDR5 16GB CORSAIR VENGEANCE": ("sodimm", 16, 1), "Samsung DDR5 S0Dimm 32GB": ("sodimm", 32, 1)}
        for t, exp in cases.items():
            r = P(t)
            self.assertEqual((r["form"], r["total"], r["modules"]), exp, t)
        self.assertTrue(P("Kingston 32GB DDR4 DIMM KCP426SD8/32")["explicit_single"])
        self.assertTrue(P("1 x Dell / Kingston DDR5 RAM 32gb - 5600 MHz - Dual Rank")["explicit_single"])

    def test_non_ecc(self):
        for t in ["Crucial 32GB (2x16GB) DDR4-3200 UDIMM Non-ECC", "16 GB DDR5 RAM KINGSTON UDIMM 4800 MHZ NON ECC", "DDR4 32GB nonecc"]:
            self.assertFalse(P(t)["ecc"], t)
        self.assertTrue(P("DDR4 32GB ECC UDIMM")["ecc"])


class TestPass9(unittest.TestCase):
    def test_parse(self):
        cases = {"G.Skill Ripjaws V DDR4 CL16 32GB Kit": ("udimm", 32),
                 "Kingston HyperX Predator 32 GB (2x16GB) HX426C13PB3K2/32 DDR4": ("udimm", 32),
                 "2x 32GiB DDR4-3200 SODIMM CRUCIAL": ("sodimm", 64),
                 "Samsung 32GB DDR4 M393A4K40EB3-CWEBY 2Rx4 PC4-3200AA": ("server", 32),
                 "SK hynix 32GB DDR4 UDIMM HMAA4GU6CJR8N-XN": ("udimm", 32),
                 "Crucial DDR5 5600MHz 32GB C46 K2 SO-RAM": ("sodimm", 32),
                 "Crucial DDR4 32GB Laptopspeicher": ("sodimm", 32),
                 }
        self.assertIsInstance(P("DDR4 16GB 2x8GB für Gigabyte Aero 15X"), dict)   # «15X» — модель, не лот
        for t, exp in cases.items():
            r = P(t)
            self.assertIsInstance(r, dict, (t, r))
            self.assertEqual((r["form"], r["total"]), exp, t)
        self.assertFalse(P("Kingston HyperX Predator 32 GB (2x16GB) HX426C13PB3K2/32 DDR4")["mixed"])
        self.assertTrue(P("SK hynix 32GB DDR4 UDIMM HMAA4GU6CJR8N-XN")["explicit_single"])
        self.assertTrue(P("Corsair Vengeance 32GB DDR5 6000 Einzelmodul")["explicit_single"])



class TestPass11Ram(unittest.TestCase):
    def test_forms(self):
        cases = {"32GB DDR4 3200 RAM Kit (2x16GB) Desktop – nicht für Laptop": "udimm",
                 "32GB DDR5 RAM 2x16GB für Gaming PC, kein Notebook RAM": "udimm",
                 "Samsung 16GB DDR5 4800 M321R2GA3BB6-CQKVS": "server",
                 "32GB DDR5 RAM für ASUS ROG Zephyrus G14": "sodimm",
                 "Crucial DDR5 5600MHz 32GB C46 K2 SO-RAM": "sodimm"}
        for t, f in cases.items():
            self.assertEqual(P(t)["form"], f, t)

    def test_kits(self):
        self.assertEqual(P("Crucial DDR5 5600MHz 32GB C46 K2 SO-RAM")["modules"], 2)
        self.assertEqual(P("Kingston FURY 32GB DDR5 6400 KF564C32RSK2-32")["modules"], 2)
        self.assertEqual(P("G.Skill Trident Z5 RGB DDR5 32GB F5-6000J3040F16GX2-TZ5RK")["modules"], 2)
        # продають одну планку з набору: номер набору не робить із неї кіт
        self.assertEqual(P("Kingston FURY Beast DDR5 4800MHz 16GB DIMM KF548C38BBK2-32")["total"], 16)


if __name__ == "__main__":
    unittest.main()
