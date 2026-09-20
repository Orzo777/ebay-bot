"""Пакетна перевірка живих акцій (ціни з mydealz, 20.09.2026) через check.py.
Ціни закупівлі — знімок акцій, вони можуть закінчитись; це тест економіки, а не сигнал купувати.
Запуск: python research/deal_batch.py   (~11 викликів API на позицію)"""
import json
import subprocess
import sys

DEALS = [
    # (мітка, аргументи check.py, ціна закупівлі з доставкою до нас)
    ("Sonicare DiamondClean 9000 iBOOD", ["--query", "Philips Sonicare DiamondClean 9000 HX9913", "--must", "9000"], 95.90),
    ("DualSense Midnight Black Joybuy", ["--query", "Sony DualSense Wireless Controller Midnight Black", "--must", "dualsense", "--exclude", "edge", "--exclude", "ladestation"], 53.49),
    ("LEGO 10300 Zeitmaschine Cdiscount", ["--lego", "10300"], 142.98),
    ("LEGO 10318 Concorde Cdiscount", ["--lego", "10318"], 142.98),
    ("LEGO 11380 Rennrad Amazon", ["--lego", "11380"], 80.99),
    ("Toniebox 2 Paw Patrol MM Chemnitz", ["--query", "Toniebox 2 Paw Patrol Starterset", "--must", "toniebox"], 84.99),
    ("Toniebox 2 Pikachu VEDES", ["--query", "Toniebox 2 Pikachu Starterset", "--must", "toniebox"], 109.00),
    ("Braun 9660cc Shoop", ["--query", "Braun Series 9 9660cc Rasierer", "--must", "9660"], 159.99),
    ("Braun 9517s Primedeals", ["--query", "Braun Series 9 9517s Rasierer", "--must", "9517"], 159.99),
    ("Pokemon TTB Fatale Flammen Kaufland", ["--query", "Pokemon Top Trainer Box Fatale Flammen", "--must", "flammen"], 44.99),
]

rows = []
for label, args, buy in DEALS:
    cmd = [sys.executable, "check.py", *args, "--buy", str(buy), "--json"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    try:
        out = json.loads(p.stdout[p.stdout.index("{"):])
    except Exception:
        print(label, "ПОМИЛКА", p.stdout[-200:], p.stderr[-200:])
        continue
    rows.append((label, buy, out))
    json.dump([(l, b, o) for l, b, o in rows], open("research/deal_batch_out.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(label, "→ готово", flush=True)
print("DONE")
