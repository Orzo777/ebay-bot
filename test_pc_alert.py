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
