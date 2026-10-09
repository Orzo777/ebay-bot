"""Тести оцінки ПК для ПК-бота (research/pc_alert.py) на реальних назвах Kleinanzeigen. Без мережі."""
import os as _os
_os.environ.setdefault("RAM_PRICES_OFF", "1")   # цифри Terapeak, а не щоденні ціни сторожа (research/ram_prices.json)
import sys
import unittest

sys.path.insert(0, ".")
sys.path.insert(0, "research")
from pc_alert import evaluate_pc, parse_pc, pc_card_lines


class TestParse(unittest.TestCase):
    def test_cpu_gpu_ram(self):
        self.assertEqual(parse_pc("Dell Optiplex 7060 i5-8500 16GB 256GB SSD"), dict(cpu=("i5", 8), gpu=None, ram=16, ssd=256))
        self.assertEqual(parse_pc("Gaming PC Ryzen 5 3600 RTX 3060 16GB")["cpu"], ("r5", 3))
        self.assertEqual(parse_pc("Gaming PC Ryzen 5 3600 RTX 3060 16GB")["gpu"], "rtx 3060")
        self.assertEqual(parse_pc("PC i7-10700 32GB")["cpu"], ("i7", 10))
        self.assertEqual(parse_pc("Fujitsu Esprimo P757 i5 6. Gen 8GB")["cpu"], ("i5", 6))
        self.assertEqual(parse_pc("PC 512GB SSD 8GB RAM")["ram"], 8)   # 512 ГБ — диск, не пам'ять


class TestEvaluate(unittest.TestCase):
    def test_verdicts(self):
        self.assertEqual(evaluate_pc("Dell Optiplex 7060 i5-8500 16GB 256GB SSD", 65)["verdict"], "BUY")   # 09.10: комісія eBay 0 — ціни зсунуті вгору на неї
        self.assertEqual(evaluate_pc("Gaming PC GTX 1060 6GB i5-6500 16GB", 60)["verdict"], "BUY-GOOD")
        self.assertEqual(evaluate_pc("HP ProDesk 600 G3 i5-7500 8GB RAM 256GB SSD", 45)["verdict"], "SKIP")
        self.assertEqual(evaluate_pc("Office PC i3-8100 8GB", 60)["verdict"], "SKIP")

    def test_unknown_is_not_skipped(self):
        r = evaluate_pc("PC + Monitor i7,16GB,500W 85+,GPU", 25)
        self.assertEqual(r["verdict"], "UNKNOWN")
        self.assertIn("Оцінити не вдалося", pc_card_lines(r)[0])

    def test_part_out_all_components(self):
        # 03.10: розбір з усіх деталей; пам'ять відеокарти («RTX 3060 Ti (8 GB)») — не RAM
        p = parse_pc("i7-11700F * NVIDIA GeForce RTX 3060 Ti (8 GB) * 64 GB DDR4 3200 MHz RAM (2x 32 GB Patriot) * 1 TB SSD")
        self.assertEqual((p["cpu"], p["gpu"], p["ram"], p["ssd"]), (("i7", 11), "rtx 3060 ti", 64, 1000))
        self.assertEqual(parse_pc("Gaming PC | RAM: 32GB DDR4 | GPU: RX 6700 XT 12GB | i5-12400F")["ram"], 32)
        self.assertEqual(parse_pc("PC Ryzen 7 5800X, 2x16GB DDR4, RTX 3080 10 GB")["ram"], 32)
        self.assertEqual(parse_pc("Gaming PC GTX 1660 Super 6GB i5-9400F 16GB")["gpu"], "gtx 1660 super")
        self.assertIn(evaluate_pc("PC Ryzen 7 5800X, 2x16GB DDR4, RTX 3080 10 GB, 2TB SSD", 400)["verdict"], ("BUY", "BUY-GOOD"))
        r = evaluate_pc("Office PC i7-10700 64GB RAM 512GB SSD", 150)   # без відеокарти, але 64 ГБ — розбирати
        self.assertIn("RAM 64", r["how"])
        r = evaluate_pc("Gaming PC i7-11700F RTX 3060 Ti 64GB DDR4 1TB SSD", 549)
        self.assertEqual(r["verdict"], "NEGOTIATE")
        self.assertIn("ТОРГУЙСЯ", pc_card_lines(r)[0])
        self.assertEqual(evaluate_pc("Gaming PC i7-11700F RTX 3060 Ti 64GB DDR4 1TB SSD", 900)["verdict"], "SKIP")   # торг не врятує

    def test_card_shows_profit_and_route(self):
        lines = pc_card_lines(evaluate_pc("Gaming PC Ryzen 5 3600 RTX 3060 16GB", 60))
        self.assertIn("БЕРИ", lines[0])
        self.assertIn("RTX 3060", lines[1])


if __name__ == "__main__":
    unittest.main()


class TestPass4Pc(unittest.TestCase):
    """28.09, 300 живих оголошень ПК-бота: шум «невідомо» (iMac, ноутбуки, клавіатури, монітори, старе залізо)."""

    def test_not_whole_pc(self):
        sys.path.insert(0, "research")
        from ram_mail_check import pc_skip_reason
        for t in ["iMac 27 Zoll 2012 (1 TB)", "Lenovo ThinkPad L440 i5 + 22\" Monitor", "OMEN by HP Sequencer Gaming-Tastatur",
                  "Pc Netzteil Inter-Tech Argus RGB-650W CM II, OVP", "Pc Monitor", "Gaming PC Gehäuse mit beleuchteten Lüftern",
                  "HP ProLiant DL380 G7 Server", "Commodore 64 C64 Vintage Retro Computer", "Apple Computer",
                  "Altes MacBook Air 2014 - Defekt", "HP EliteDesk 800 G1 Ultra-Slim Desktop PC"]:
            self.assertIsNotNone(pc_skip_reason(t), t)
        for t in ["Gaming PC mit Netzteil 600W", "Fujitsu Esprimo Desktop PC Tower", "PC Monitor, Intel Tastatur und Maus"]:
            self.assertIsNone(pc_skip_reason(t), t)

    def test_old_hardware(self):
        for t in ["PC Rechner AMD PHENOM 2 X4 945, 4Gb", "Lenovo Desktoprechner mit Core2 Duo 3 GHz", "Intel Core i5‑4460",
                  "HP ProDesk 400 G1 SFF i3", "Computer PC AMD FX 6300 8 Gb Ram"]:
            self.assertEqual(evaluate_pc(t, 30)["verdict"], "SKIP", t)


class TestRamMaxAndOddTotals0910(unittest.TestCase):
    """09.10: «20 GB DDR4-2666 (4 Slots belegt, max. 64 GB)» пішло як 64 ГБ за 269 € → хибне «МОЖНА, +96 €»."""

    def test_board_maximum_is_not_installed_ram(self):
        from pc_alert import _parse_ram
        self.assertEqual(_parse_ram("RAM: 20 GB DDR4-2666 (4 Slots belegt, max. 64 GB)"), 20)
        self.assertEqual(_parse_ram("16GB RAM, erweiterbar auf 64 GB"), 16)
        self.assertEqual(_parse_ram("Mainboard unterstützt bis zu 64GB, verbaut 2x8GB DDR4"), 16)
        self.assertEqual(_parse_ram("RTX 3060 Ti (8 GB) * 64 GB DDR4"), 64)   # справжні 64 — як і раніше

    def test_real_listing(self):
        from pc_alert import evaluate_pc
        t = ("PC i5-9500 | GTX 1050 Ti 4GB | 20GB DDR4 | 500+256GB NVMe | - CPU: Intel Core i5-9500 - Grafik: NVIDIA "
             "GeForce GTX 1050 Ti 4 GB - RAM: 20 GB DDR4-2666 (4 Slots belegt, max. 64 GB)")
        ev = evaluate_pc(t, 200)
        self.assertEqual(ev["parsed"]["ram"], 20)
        self.assertIn("GTX 1050 TI €49", ev["how"])
        self.assertIn("RAM 20 ГБ €", ev["how"])   # 20 = 16 + 4 — ціна як за 16 ГБ, обережно (щоденна ціна сторожа)
        self.assertEqual(ev["verdict"], "SKIP")


class TestWholePcByGpu0910(unittest.TestCase):
    """09.10: «Ryzen 5 5600X + GTX 1060» за 350 € → було «торгуйся до 270» (ціна «ПК з R5 5-го» = ПК з RTX 3060)."""

    def test_gpu_decides_whole_pc_price(self):
        from pc_alert import evaluate_pc
        ev = evaluate_pc("Gaming-PC | Ryzen 5 5600X | GTX 1060 6GB | 16GB RAM | 500GB NVMe", 350)
        self.assertEqual(ev["verdict"], "SKIP")
        self.assertIn("ігровий ПК з GTX 1060", ev["how"])
        self.assertNotIn("R5 5-го покоління цілим", ev["how"])
        self.assertLess(evaluate_pc("Gaming-PC | Ryzen 5 5600X | GTX 1060 6GB | 16GB RAM", 160)["profit"],
                        evaluate_pc("Gaming-PC | Ryzen 5 5600X | GTX 1060 6GB | 16GB RAM", 140)["profit"])

    def test_ryzen_apu_office_pc_not_overvalued(self):
        from pc_alert import evaluate_pc
        self.assertEqual(evaluate_pc("PC Ryzen 5 5600G 16GB 512GB SSD", 215)["verdict"], "SKIP")   # було BUY-GOOD +153 € (200 €; 09.10 комісія 0 → 215)


class TestPartsRefresh0910(unittest.TestCase):
    """09.10: щомісячний замір цін деталей ПК (price_refresh.pc_parts): 0.65 × p25 / 0.7 × p25, крок ±25%, раз на місяць."""

    def test_refresh(self):
        import price_refresh as pr

        def fetch(q, cond, cat):
            if q == "rtx 3060":   # 10 справжніх «RTX 3060» + шум (Ti, ПК, дефект) — шум не рахується
                return ([{"title": f"MSI RTX 3060 12GB {i}", "total": 290.0 + 10 * i} for i in range(10)] +
                        [{"title": "RTX 3060 Ti", "total": 50.0}, {"title": "Gaming PC RTX 3060", "total": 30.0},
                         {"title": "RTX 3060 defekt", "total": 20.0}])
            if q == "gtx 1060":
                return [{"title": "GTX 1060 6GB", "total": 500.0} for _ in range(10)]   # стрибок — обмежиться +25%
            if q == "i5-9400":
                return [{"title": "Intel Core i5 9400 CPU", "total": 60.0} for _ in range(3)]   # замало — без змін
            return []
        data = {}
        out = pr.pc_parts(data, fetch, "2026-10-09")
        g, c = data["pc_parts"]["gpu"], data["pc_parts"]["cpu"]
        self.assertEqual(g["rtx 3060"], round(0.65 * 310))        # p25 з 10 цін 290..380 = 310 (у межах ±25% від 247)
        self.assertEqual(g["gtx 1060"], round(49 * 1.25))          # не більше +25% за раз
        self.assertNotIn("i5|9", c)
        self.assertIn("RTX 3060", "\n".join(out))
        self.assertEqual(pr.pc_parts(data, fetch, "2026-10-20"), [])   # раз на місяць

    def test_override_loaded_only_without_flag(self):
        import pc_alert
        g, c = pc_alert._parts_refresh(pc_alert.GPU_PART_BASE, pc_alert.CPU_PART_BASE)   # RAM_PRICES_OFF=1 у тестах
        self.assertEqual(g, pc_alert.GPU_PART_BASE)
