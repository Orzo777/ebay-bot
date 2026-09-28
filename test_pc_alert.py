"""Тести оцінки ПК для ПК-бота (research/pc_alert.py) на реальних назвах Kleinanzeigen. Без мережі."""
import sys
import unittest

sys.path.insert(0, ".")
sys.path.insert(0, "research")
from pc_alert import evaluate_pc, parse_pc, pc_card_lines


class TestParse(unittest.TestCase):
    def test_cpu_gpu_ram(self):
        self.assertEqual(parse_pc("Dell Optiplex 7060 i5-8500 16GB 256GB SSD"), dict(cpu=("i5", 8), gpu=None, ram=16))
        self.assertEqual(parse_pc("Gaming PC Ryzen 5 3600 RTX 3060 16GB")["cpu"], ("r5", 3))
        self.assertEqual(parse_pc("Gaming PC Ryzen 5 3600 RTX 3060 16GB")["gpu"], "rtx 3060")
        self.assertEqual(parse_pc("PC i7-10700 32GB")["cpu"], ("i7", 10))
        self.assertEqual(parse_pc("Fujitsu Esprimo P757 i5 6. Gen 8GB")["cpu"], ("i5", 6))
        self.assertEqual(parse_pc("PC 512GB SSD 8GB RAM")["ram"], 8)   # 512 ГБ — диск, не пам'ять


class TestEvaluate(unittest.TestCase):
    def test_verdicts(self):
        self.assertEqual(evaluate_pc("Dell Optiplex 7060 i5-8500 16GB 256GB SSD", 50)["verdict"], "BUY")
        self.assertEqual(evaluate_pc("Gaming PC GTX 1060 6GB i5-6500 16GB", 60)["verdict"], "BUY-GOOD")
        self.assertEqual(evaluate_pc("HP ProDesk 600 G3 i5-7500 8GB RAM 256GB SSD", 45)["verdict"], "SKIP")
        self.assertEqual(evaluate_pc("Office PC i3-8100 8GB", 60)["verdict"], "SKIP")

    def test_unknown_is_not_skipped(self):
        r = evaluate_pc("PC + Monitor i7,16GB,500W 85+,GPU", 25)
        self.assertEqual(r["verdict"], "UNKNOWN")
        self.assertIn("Оцінити не вдалося", pc_card_lines(r)[0])

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
