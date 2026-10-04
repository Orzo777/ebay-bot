"""Кейси аудитів eBay/KA (кола 12–13, 29.09): регресії, які вже раз ламались. Без мережі."""
import os as _os
_os.environ.setdefault("RAM_PRICES_OFF", "1")   # цифри Terapeak, а не щоденні ціни сторожа (research/ram_prices.json)
import sys
import unittest

sys.path.insert(0, ".")
sys.path.insert(0, "research")
from console_alert import evaluate_console
from ram_alert import evaluate, refine_by_desc
from ram_parse import parse_title

import console_alert

EV = lambda t, p, vb=False: evaluate_console(t, p, vb=vb) or evaluate(t, p, vb=vb)
bad = 0
_switch1_saved, console_alert.SWITCH1_ON = console_alert.SWITCH1_ON, True   # кейси першої Switch (вимкнена 04.10)


def chk(t, p, want_send, d=None):
    global bad
    r = EV(t, p)
    if d is not None:
        r = refine_by_desc(r, t, p, False, d, EV)
    ok = (r["verdict"] != "SKIP") == want_send
    bad += not ok
    (lambda *a, **k: None)(f"{'OK' if ok else '!!'} {r['verdict']:<13} {p:>4} | {t[:62]:<62} | {(d or '')[:40]:<40} | {(r.get('reason') or r.get('type') or '')[:50]}")


S2 = "Nintendo Switch 2 Konsole"
for d in ["Verkauft wird nur das Tablet, ohne Dock und ohne Joy-Con 2", "ohne Dock und Joy-Cons, nur Handheld", "ohne Zubehör"]:
    chk(S2, 240, False, d)
for t, p in [("Nintendo Switch OLED nur Konsole, keine Joy-Cons", 90), ("Nintendo Switch V2 nur Konsole keine Joy-Con kein Dock", 70),
             ("Nintendo Switch nur Konsole mit Ladekabel", 70), ("Nintendo Switch OLED nur Konsole + Hülle", 90)]:
    chk(t, p, False)
for t, p in [("Nintendo Switch OLED nur Konsole mit Dock und Joy-Cons", 80), ("Nintendo Switch V2 nur Konsole, keine Spiele", 55)]:
    chk(t, p, True)
for t, p in [("Xbox Series X Videospiele", 150), ("PS5 Videospiele Paket 8 Stück", 150), ("Nintendo Switch 2 Videospiele Bundle 3 Stück", 200),
             ("CRKD Nitro Deck Switch OLED", 70), ("Nitro Deck Nintendo Switch & Switch OLED Handheld Controller", 65),
             ("Ring Fit Adventure Nintendo Switch OLED kompatibel", 65), ("Nintendo Switch 2 Displayriss", 250), ("Xbox Series X Displaybruch", 250)]:
    chk(t, p, False)
for t, p in [("PS5 Slim Disc + 2 Controller + 3 Videospiele", 250), ("Nintendo Switch OLED + Ring Fit Adventure + 2 Spiele", 80),
             ("PS5 Slim Disc 1TB Risse: keine", 300), ("Nintendo Switch OLED keine Kratzer, Dellen oder Risse", 80),
             ("Xbox Series X 1TB + 3 Konsolenspiele", 250), ("Microsoft Xbox Series X 1TB Videospielkonsole - Schwarz", 250)]:
    chk(t, p, True)
chk("Konsolen Spiele Konvolut / Xbox360, Ps4 und Nintendo Switch - gebraucht", 60, False)
# 9 + нові 1–2
for t, p, d in [("Mario Kart 8 Deluxe Nintendo Switch Zustand gut", 55, "Spiel in OVP, Konsole nicht enthalten."),
                ("Nintendo Switch Edition Pokemon Let's Go Pikachu", 55, "Die Konsole ist nicht Teil des Angebots"),
                ("Hyrule Warriors Nintendo Switch Edition", 60, "Top Spiel, auch im Handheld-Modus super"),
                ("Super Mario Party Nintendo Switch gebraucht", 55, "Kompatibel mit Switch und Switch OLED"),
                ("Switch Sports Edition mit Beingurt Nintendo Switch", 60, "Für die Joy-Con, Beingurt dabei")]:
    chk(t, p, False, d)
for t, p, d in [("Nintendo Switch - Pikachu & Evoli Edition Spielepacket+ 2 Controller u. Pokeball", 50,
                 "Nintendo Switch Konsole mit Dockingstation. Das Spiel Renzo Racing (nur Modul) ist dabei."),
                ("Nintendo Switch In Topzustand", 55, "Mit Ladestation, Netzkabel und zusätzlichen Controllern"),
                ("Nintendo Switch mit Schutztasche", 55, "Die Konsole funktioniert einwandfrei")]:
    chk(t, p, True, d)
(lambda *a, **k: None)("RAM:")
cases = {"Kingston Fury DDR5 32GB (2x16GB) – keine Einzelriegel": (32, 2), "Kingston Fury DDR5 32GB (2x16GB), Einzelriegel auch möglich": (32, 2),
         "G.Skill DDR4 32GB aus Set 2x16GB": (32, 2), "Corsair DDR5 16GB Einzelmodul aus 32GB Kit CMK32GX5M2B5600C36": (16, 1),
         "Kingston Fury Beast DDR5 Kit of 2 16GB Module": (32, 2), "Corsair DDR5 K2 16GB Module": (32, 2),
         "Crucial Pro DDR5 K2 32GB 5600 Desktop RAM": (32, 2)}
for t, exp in cases.items():
    r, why = parse_title(t)
    got = (r["total"], r["modules"]) if r else why
    ok = got == exp
    bad += not ok
    (lambda *a, **k: None)(f"{'OK' if ok else '!!'} {t[:60]:<60} -> {got}")
forms = {"Kingston Fury Beast DDR5 32GB nicht für Laptop": "udimm", "DDR5 32GB kein Laptop RAM 6000": "udimm",
         "Desktop RAM DDR5 32GB not for Laptop": "udimm", "DDR5 32GB 2x16GB - no Laptop RAM - Desktop": "udimm",
         "DDR4 32GB Desktop – kein SO-DIMM": "udimm", "DDR4 32GB UDIMM kein SODIMM": "udimm",
         "Kingston Fury Impact DDR5 32GB kein Notebook mehr": "sodimm", "Samsung M425R2GA3BB0 16GB DDR5 nicht mehr benötigt Laptop": "sodimm",
         "Crucial DDR5 32GB SO-DIMM 5600": "sodimm"}
for t, f in forms.items():
    r, why = parse_title(t)
    ok = r and r["form"] == f
    bad += not ok
    (lambda *a, **k: None)(f"{'OK' if ok else '!!'} {t[:60]:<60} -> {r['form'] if r else why}")



# ---- коло 14 (29.09) ----
for t, p in [("Nintendo Switch OLED Reparatur", 100), ("PS5 Reparatur HDMI", 150), ("Nintendo Switch 2 Reparatur Service", 150),
             ("PlayStation 5 Originalverpackung leer", 200), ("PS5 Slim Disc Leerkarton", 300), ("Xbox Series X Attrappe Deko", 150),
             ("PS5 Konsole zu vermieten", 150), ("Xbox Series X lässt sich nicht einschalten", 150), ("Switch OLED Wasserschaden", 60),
             ("Nintendo Switch OLED keine Kratzer, Display hat einen Riss", 100), ("Nintendo Switch OLED Riss nicht störend", 100),
             ("PS5 Slim Disc Riss kein Problem", 250), ("PS5 Disc Konsolenspiele Paket 10 Stück", 150),
             ("PS5 Slim Spiele Konvolut Konsolenspiele", 150), ("Nintendo Switch Sports + Ring Fit Adventure", 55),
             ("CRKD Nitro Deck für Nintendo Switch OLED Konsole", 60), ("Ring Fit Adventure für Nintendo Switch Konsole", 55),
             ("Switch OLED Display Haarriss", 100), ("PS5 Slim Display zersprungen", 250)]:
    chk(t, p, False)
for t, p in [("Xbox Series X 1TB + 3 Konsolenspiele", 250), ("Nintendo Switch OLED ohne Kratzer, Dellen, Brüche oder Risse", 80),
             ("Nintendo Switch, 3 Controller, RingFit, Tasche und Dock", 55), ("Nintendo Switch OLED weiß, CRKD Nitro Deck", 80),
             ("Nintendo Switch nur Konsole & Dock", 55), ("Nintendo Switch nur Konsole, Joy-Cons und Dock", 55),
             ("PS5 Slim Disc 1TB, Preis pro Stück", 280), ("PS5 Konsole unbeschädigt", 280),
             ("Xbox Series X Verkauf geht aus gesundheitlichen Gründen", 280)]:
    chk(t, p, True)
for t, p, d in [("Nintendo Switch mit Schutztasche", 55, "Konsole keine Kratzer und im Top-Zustand, TV- und Handheld-Modus"),
                ("Nintendo Switch mit Schutztasche", 55, "Die Konsole ist nicht im Originalkarton, Dock und Joy-Cons dabei")]:
    chk(t, p, True, d)
cases14 = {"DDR5 32GB Einzelriegel (aus 2x32GB Kit) Corsair": (32, 1), "Kingston Fury DDR5 16GB Einzelmodul aus 2x16GB Kit": (16, 1),
           "G.Skill DDR5 16GB Einzelriegel (1x16GB aus 2x16GB)": (16, 1), "G.Skill DDR4 32GB aus Set 2x16GB": (32, 2)}
for t, exp in cases14.items():
    r, why = parse_title(t)
    bad += ((r["total"], r["modules"]) if r else why) != exp
forms14 = {"Crucial 32GB DDR5 SO-DIMM 5600 nicht gebrauchter Laptop RAM": "sodimm", "DDR5 32GB SO-DIMM (2x16GB) not used Notebook": "sodimm",
           "Tecno Laptop DDR5 16GB SODIMM": "sodimm", "Kingston Fury Beast DDR5 32GB nicht für Laptop": "udimm",
           "DDR4 32GB Desktop – kein SO-DIMM": "udimm"}
for t, f in forms14.items():
    r, why = parse_title(t)
    bad += not (r and r["form"] == f)


# ---- 30.09: картки eBay від користувача (гра й аксесуари як «PS5 — БЕРИ») ----
for t, p in [("Baldur's Gate 3 Deluxe Edition PS5 - Neu, Versiegelt", 170),
             ("PS5 Bundle: Pulse Elite Headset (NEU/OVP) + HD-Kamera wurde schon mal benutzt", 160),
             ("Starfield Premium Edition Xbox Series X", 60), ("Final Fantasy 16 - Limited Deluxe Edition - PS5 - Deutsche Verpackung", 150)]:
    chk(t, p, False)
for t, p in [("Sony PlayStation 5 Slim Disc 1TB", 300), ("PS5 Konsole weiß", 280), ("Sony PlayStation 5 Disc Edition Videospielkonsole", 300)]:
    chk(t, p, True)
chk("PlayStation 5 mit 2 Controllern", 300, True, "Konsole läuft einwandfrei, zwei Controller dabei")
chk("PlayStation 5 mit 2 Controllern", 300, False, "Spiel für die PS5 Konsole")
chk("Verkaufe meine PS5", 300, True, "Die PS5 mit Controller und Kabel")


console_alert.SWITCH1_ON = _switch1_saved


class TestAuditCases(unittest.TestCase):
    def test_all_cases_pass(self):
        self.assertEqual(bad, 0)


if __name__ == "__main__":
    unittest.main()
