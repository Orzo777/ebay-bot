"""Оцінка оголошень Xbox Series X з листів Kleinanzeigen — поруч із RAM (ram_alert.py).

Чому Xbox: Microsoft підняла ціну 1.08.2026, вживані на eBay.de подорожчали на +29% за 2–3 міс.
(Terapeak, вживані, 30 днів до 24.09.2026: швидка ціна €509, медіана €561, ≥15 продажів/тиж),
а частина приватних продавців на KA ще продає за старою ціною (5–6 з ~50 оголошень за 7 год нижче стелі).

evaluate_console() повертає None, якщо назва — не Xbox Series X (тоді лист оцінює RAM-логіка),
інакше словник у форматі ram_alert.evaluate(), тож format_html/seller_template працюють без змін.

Ручна перевірка:
    python research/console_alert.py "Xbox Series X 1TB mit Controller" 290
"""
import re

from ram_alert import buy_cost, tier

# Terapeak eBay.de, SOLD, conditionId=3000 (вживані), 30 днів до 24.09.2026.
# 26.09.2026: 83 продані за 30 днів (класифікація цим же оцінювачем) — p25 545, медіана 571
XBOX_SERIES_X = dict(p25=545, med=571, st=30, name="Xbox Series X (вживана)")
# Konsolen: 6,5% (eBay.de з 12.02.2026), €0.45 за замовлення; пересилка DHL Paket до 10 кг зі страховкою.
FEE, ORDER_FEE, SHIP = 0.065, 0.45, 10.49
SHIP_IN = 11.0   # пересилка, яку покупець платить продавцю (DHL Paket до 10 кг через «Sicher bezahlen»)
MIN_PRICE = 150  # дешевше — аксесуар, «Suche», шахрайство або помилка в ціні
SUSPICIOUS_BELOW = 250  # навіть за старими цінами (до 1.08) вживана Series X коштувала €330+

_SERIES_X = re.compile(r"serie[sn]?\s*x\b", re.I)
# «Series» саме по собі НЕ «Series S» (28.09: «Elite Controller Series 2» відкидав справжні Xbox Series X) — потрібен пробіл
_X_AND_S = re.compile(r"serie[sn]?\s*x\s*(?:/|\||&|und|oder|,)\s*s\b|\bserie[sn]?\s+s\s*(?:/|\||&|und|oder|,)\s*x\b", re.I)
_OTHER_CONSOLE = re.compile(r"\bserie[sn]?\s+s\b|\bseries-s\b|playstation|\bps[45]\b|switch|xbox\s*one", re.I)
# «Fn ACC Ps5» за €150 (eBay 28.09) — продаж ігрового акаунта, не консолі
_REJECT = re.compile(r"defekt|bastler|ersatzteil|kaputt|\bsuche\b|\bsuch\b|tausch|gesperrt|gebannt|banned|\bacc\b|\baccount|"
                     r"\bkonto\b|"
                     r"ohne\s+(?:laufwerk|netzteil|konsole)|\bnur\s+(?:die\s+)?(?:ovp|karton|verpackung|controller)|"
                     # eBay (27.09): ігри-колекційки та послуги в категорії Konsolen; японська Switch 2 — лише японська мова
                     r"collector|legacy\s+edition|\bwata\b|\bvga\b|graded|samm?lung|samlung|\blegit\b|"
                     r"timestamp|troph|\bjapan|"
                     # «⚠️ ACHTUNG Betrüger» (KA 28.09) — попередження, не продаж
                     r"\bachtung\b|\bwarnung\b|\bvorsicht\b|betr[uü]e?g|\bscam", re.I)
_DIGITAL = re.compile(r"digital", re.I)
_ACCESSORY = re.compile(r"controller|kontroller|headset|speichererweiterung|festplatte|\bssd\b|lenkrad|wheel|ständer|"
                        r"halterung|kühler|lüfter|skin|folie|hülle|tasche|\bcase\b|netzteil|kabel|\bspiele?\b|\bgame\b|"
                        r"fernbedienung|akku|ladestation|dock", re.I)
_CONSOLE_WORD = re.compile(r"konsole|console|\d\s?tb\b|\bmit\b|\binkl|\+|\bbundle\b", re.I)
# «1TB» НЕ ознака консолі: «Xbox Series X Speichererweiterung 1TB» — аксесуар
_STRONG_CONSOLE = re.compile(r"konsole|console|handheld|komplett-?(?:set|paket)", re.I)
# «1TB SSD», «825 GB SSD» — пам'ять самої консолі, а не аксесуар «SSD» (28.09: «Xbox Series X 1TB SSD» → «аксесуар»)
_OWN_STORAGE = re.compile(r"\b\d+(?:[.,]\d)?\s?(?:tb|gb)\s+(?:ssd|festplatte|hdd|speicher)\b", re.I)
_BUNDLE_LINK = re.compile(r"\bmit\b|\binkl|\+|\bund\b|&|,|\bbundle\b|\bsamt\b|\bplus\b|\bset\s+mit\b|\bwith\b|\bw/", re.I)
# Явна модель консолі між назвою та «аксесуаром»: це перелік комплекту без «mit» (eBay 28.09: «Sony PlayStation 5 PS5 Blu-Ray
# 825GB PAL 4K DualSense Controller Standfuß», «… Digital Edition 825 GB Weiß 4K HDR DualSense Kabel»)
_STRONG_MODEL = re.compile(r"\b\d{3,4}\s?gb\b|\b\d\s?tb\b|cfi-?\d{4}|(?:disc|disk|digital|blu-?ray)[\s-]*(?:edition|version)",
                           re.I)
_NEGATED = re.compile(r"(?:\bohne|\bkein\w*)\s+(?:\w+\s+)?$", re.I)   # «Ohne Spiel», «ohne Controller» — не аксесуар
_FOR_WORD = re.compile(r"\bfür\b|\bfor\b|\bpassend\b|kompatibel|\bzu[rm]?\b", re.I)


_MODEL_WORD = re.compile(r"\b(?:dis[ck]|digital|slim|oled|cfi-?\d{4}|\d{3,4}\s?gb|\d\s?tb|version|v2)\b", re.I)
_BUNDLABLE = re.compile(r"controller|kontroller|dualsense|joy[\s-]?con|spiel|game|headset|kamera|kabel|ständer|standfu|"
                        r"netzteil|dock|ladestation|tasche|hülle|\bcase\b|schutz|folie|grip|micro\s?sd|speicherkarte|skin|"
                        r"halterung|amiibo", re.I)
_COUNT_BEFORE = re.compile(r"(?:\b\d|zwei|drei|vier|beide[n]?|two|three)\s*x?\s*$", re.I)
_BUNDLE_WORD = re.compile(r"\b(?:bundle|set|paket)\b", re.I)


# «Virtual Boy für Nintendo Switch & Switch 2» (eBay 28.09) — «X für <консоль>» без «Konsole» це аксесуар,
# навіть якщо самого аксесуара немає в нашому списку слів
_FOR_CONSOLE = re.compile(r"\b(?:für|for|passend\s+für|kompatibel\s+mit)\s+(?:die\s+|den\s+|das\s+)?(?:nintendo|sony|microsoft|"
                          r"xbox|x-?box|ps\s?5|playstation|switch)", re.I)


def _is_accessory(title: str, acc_re: re.Pattern, name_re: re.Pattern, price: float = 0, p25: float = 0) -> bool:
    """Структурне правило (27.09: «Joy-Con Pair 2er-Set», «Lenkrad mit Pedalen … Switch», «PS5 Faceplate Cover Slim»
    проходили як консолі). Аксесуар — якщо його названо ДО консолі або одразу ПІСЛЯ неї без «mit/inkl/+/und»;
    консоль — якщо є «Konsole/Console/1TB» або аксесуар іде через «mit/inkl/+» («Xbox Series X mit Controller»)."""
    nm = name_re.search(title)   # «1TB SSD» ПІСЛЯ назви консолі — її власна пам'ять; «WD 1TB SSD für PS5» — аксесуар
    if nm and not _FOR_WORD.search(title[:nm.start()]):
        title = title[:nm.end()] + _OWN_STORAGE.sub(" ", title[nm.end():])
    acc = next((m for m in acc_re.finditer(title) if not _NEGATED.search(title[max(0, m.start() - 25):m.start()])), None)
    if not acc:
        fc = _FOR_CONSOLE.search(title)
        return bool(fc and fc.start() > 0 and not _STRONG_CONSOLE.search(title))
    name = name_re.search(title)
    # «Disc-Laufwerk für PS5 Digital Edition Konsole» — «Konsole» тут про те, ДО ЧОГО аксесуар (eBay, 27.09)
    if name and acc.start() < name.start() and _FOR_WORD.search(title[acc.end():name.start()]):
        return True
    if _STRONG_CONSOLE.search(title):
        return False
    if not name:
        return True
    if acc.start() < name.start() or _FOR_WORD.search(title[:name.start()]):
        return True   # «Lenkrad … Nintendo Switch», «Controller für PS5»
    # «PS5 … Disk slim Version 2 Controller Bundle» (27.09): модель консолі + «2 Controller» / «… Bundle» — консоль у наборі
    if (_MODEL_WORD.search(title[name.end():acc.start()]) and _BUNDLABLE.match(acc.group(0))
            and (_COUNT_BEFORE.search(title[:acc.start()]) or _BUNDLE_WORD.search(title[acc.end():]))):
        return False
    if _STRONG_MODEL.search(title[name.end():acc.start()]) and _BUNDLABLE.match(acc.group(0)):
        return False
    # Ціна консолі, а не аксесуара: «Switch 2 – Top Zustand – 2x Pro Controller» за €419, «Switch Paket! 4 Joycons, 2 Controller,
    # 3 Spiele» за €250 (eBay 28.09) — контролери/ігри в наборі. Кермо чи VR сюди не підпадають (їх немає в _BUNDLABLE).
    # Лише коли аксесуар НЕ стоїть одразу після назви: «PlayStation 5 Controller GTA VI Limited» за €279 — контролер.
    gap = title[name.end():acc.start()]
    if (p25 and price >= max(0.6 * p25, 150) and _BUNDLABLE.match(acc.group(0))
            and (re.search(r"\s[-–|/]\s|[|!,]", gap) or _COUNT_BEFORE.search(gap))):   # «Mini-Dockingstation», «God of War Controller's» — не перелік
        return False
    return not _BUNDLE_LINK.search(title[name.end():acc.start()])


PACK = 3.0            # коробка + наповнювач для консолі
INSURE_OVER_500 = 6.99  # DHL базово страхує до €500; Xbox продається дорожче → страховка до €2 500


def costs(sale_price: float, ship: float = SHIP) -> float:
    return (FEE * sale_price + ORDER_FEE + ship + PACK + (INSURE_OVER_500 if sale_price > 500 else 0)
            + 0.03 * (2 * ship + ORDER_FEE))


def _skip(title: str, price: float, reason: str, wrong_type: bool = True) -> dict:
    return dict(verdict="SKIP", reason=reason, title=title, price=price, wrong_type=wrong_type)


def evaluate_console(title: str, price: float, shipping: float | None = None, vb: bool = False) -> dict | None:
    title = re.sub(r"[®™©\ufe0f]", "", title or "")   # «PlayStation®5» (eBay 28.09: 7 консолей не розпізнано)
    if not _SERIES_X.search(title):
        return evaluate_ps5(title, price, shipping, vb)
    ship_in = SHIP_IN if shipping is None else shipping
    total = price
    if _X_AND_S.search(title):
        return _skip(title, total, "«Series X/S» — аксесуар для обох консолей, не сама консоль")
    if _REJECT.search(title):
        return _skip(title, total, "дефект / пошук / обмін / лише коробка / гра чи колекційне")
    if _OTHER_CONSOLE.search(_SERIES_X.sub("", title)):
        return _skip(title, total, "разом з іншою консоллю або не Series X")
    if _DIGITAL.search(title):
        return _skip(title, total, "Series X Digital — інша ціна продажу, не виміряна")
    if _is_accessory(title, _ACCESSORY, _SERIES_X, price, XBOX_SERIES_X["p25"]):
        return _skip(title, total, "схоже на аксесуар, а не на консоль")
    if total < MIN_PRICE:
        return _skip(title, total, f"дешевше €{MIN_PRICE} — аксесуар, шахрайство або помилка в ціні", False)
    real = XBOX_SERIES_X
    net_q = real["p25"] - costs(real["p25"])
    cap, good, excellent = net_q / 1.3, net_q / 1.6, net_q / 2.0
    cost = buy_cost(price, ship_in)
    verdict = tier(cost, cap, good, excellent, vb)
    notes = ["Перевір: працює привід і контролер, консоль не заблокована. Лише «Sicher bezahlen» або самовивіз."]
    if total < SUSPICIOUS_BELOW:
        notes.insert(0, "Підозріло дешево: частина таких оголошень — шахраї. Жодних переказів наперед.")
    return dict(verdict=verdict, type=real["name"], price=total, cap=cap, good=good, excellent=excellent,
                quick_sale=real["p25"], median_sale=real["med"], sell_through=real["st"], profit_est=net_q - cost,
                brand="Microsoft", title=title, notes=notes, net_q=net_q,
                item_acc="die Xbox Series X", check_q="Laufen Laufwerk und Controller einwandfrei, keine Sperre?",
                buy_cost=cost, ship_in=ship_in, vb=vb)


# ---------------- PS5 (додано 26.09.2026) ----------------
# Terapeak eBay.de, SOLD, вживані, 30 днів до 26.09.2026 (див. PS5_* нижче). Slim і перша («товста») модель
# продаються майже однаково, тому Disc — один тип; Digital — окремий (дешевший); Pro не купуємо (забирає бюджет,
# на KA вже по ринку).
PS5_DISC = dict(p25=438, med=460, st=30, name="PS5 з дисководом (вживана)")   # 183 продані / 30 дн.
PS5_DIGITAL = dict(p25=399, med=419, st=25, name="PS5 Digital (вживана)")   # 102 продані / 30 дн.
PS5_SUSPICIOUS_BELOW = 280

_PS5 = re.compile(r"\bps\s?-?5\b|play\s?-?station\s*-?5|playst\w*ion\s*5", re.I)   # «Play Station 5», «Playsttstion 5»
_PS5_PRO = re.compile(r"\bpro\b", re.I)
_PS5_OTHER = re.compile(r"portal|\bportable\b|porta\s+remote|remote[\s-]?play|\bvr\s?2?\b|psvr|xbox|switch|\bps4\b|playstation\s*4", re.I)
_PS5_ACCESSORY = re.compile(r"controller|dualsense|headset|laufwerk|disc\s*drive|\bssd\b|festplatte|ständer|halterung|"
                            r"lüfter|kühler|skin|folie|hülle|tasche|\bcase\b|cover|faceplate|netzteil|kabel|\bspiele?\b|"
                            r"\bgame\b|fernbedienung|ladestation|dock|kamera|lenkrad|wheel|pedal|erweiterung|expansion", re.I)
# «Edition» НЕ ознака консолі: «DualSense LeBron James Limited Edition» — контролер (27.09)
_PS5_CONSOLE = re.compile(r"konsole|console|\d\s?tb\b|825\s?gb|\bmit\b|\binkl|\+|\bbundle\b|\bslim\b", re.I)


def evaluate_ps5(title: str, price: float, shipping: float | None = None, vb: bool = False) -> dict | None:
    """None — не PS5 (далі оцінює RAM-логіка); інакше словник у форматі ram_alert.evaluate()."""
    if not _PS5.search(title):
        return evaluate_switch(title, price, shipping, vb)
    ship_in = SHIP_IN if shipping is None else shipping
    if _REJECT.search(title):
        return _skip(title, price, "дефект / пошук / обмін / лише коробка / гра чи колекційне")
    if _PS5_PRO.search(title):
        return _skip(title, price, "PS5 Pro — не купуємо: забирає бюджет, на KA вже по ринку")
    if _PS5_OTHER.search(_PS5.sub("", title)):
        return _skip(title, price, "PS Portal / VR / разом з іншою консоллю")
    digital = bool(_DIGITAL.search(title))
    # «Digital + Laufwerk» / «Laufwerk für PS5» — дисковод окремо: без слова «Konsole» це аксесуар
    if _is_accessory(title, _PS5_ACCESSORY, _PS5, price, (PS5_DIGITAL if digital else PS5_DISC)["p25"]):
        return _skip(title, price, "схоже на аксесуар, а не на консоль")
    if price < MIN_PRICE:
        return _skip(title, price, f"дешевше €{MIN_PRICE} — аксесуар, шахрайство або помилка в ціні", False)
    real = PS5_DIGITAL if digital else PS5_DISC
    net_q = real["p25"] - costs(real["p25"])
    cap, good, excellent = net_q / 1.3, net_q / 1.6, net_q / 2.0
    cost = buy_cost(price, ship_in)
    verdict = tier(cost, cap, good, excellent, vb)
    notes = ["Перевір: " + ("" if digital else "працює дисковод, ") + "контролер, немає PSN-блокування. "
             "Лише «Sicher bezahlen» або самовивіз."]
    if price < PS5_SUSPICIOUS_BELOW:
        notes.insert(0, "Підозріло дешево: частина таких оголошень — шахраї. Жодних переказів наперед.")
    return dict(verdict=verdict, type=real["name"], price=price, cap=cap, good=good, excellent=excellent,
                quick_sale=real["p25"], median_sale=real["med"], sell_through=real["st"], profit_est=net_q - cost,
                brand="Sony", title=title, notes=notes, net_q=net_q, item_acc="die PS5",
                check_q=("Läuft alles einwandfrei (Controller), keine PSN-Sperre?" if digital
                         else "Laufen Laufwerk und Controller einwandfrei, keine PSN-Sperre?"),
                buy_cost=cost, ship_in=ship_in, vb=vb)


# ---------------- Nintendo Switch 2 і перша Switch (26–27.09.2026) ----------------
# Terapeak, продані вживані, 30 днів. Switch 2: ≥34 шт., p25 €382 (−3% за 3 міс.); на KA €180–270 трапляються,
# але «нова в плівці» за пів ціни (роздріб ~€500) — типовий шаблон шахраїв. Перша Switch дешевшає (OLED −17%,
# Lite −23% за рік) і на KA зазвичай дорожча за стелю — оцінюємо, щоб дати пораду «вигідно лише до …»,
# картки йдуть лише при реальній маржі. Легші за Xbox/PS5: DHL Paket до 5 кг €7.69 (Lite — до 2 кг €6.19).
SWITCH2 = dict(p25=382, med=389, st=25, name="Nintendo Switch 2 (вживана)", ship=7.69, ship_in=7.0, min=150)
SWITCH_OLED = dict(p25=162, med=172, st=25, name="Nintendo Switch OLED (вживана)", ship=7.69, ship_in=7.0, min=60)
SWITCH_V2 = dict(p25=131, med=140, st=20, name="Nintendo Switch V1/V2 (вживана)", ship=7.69, ship_in=7.0, min=50)
SWITCH_LITE = dict(p25=82, med=92, st=25, name="Nintendo Switch Lite (вживана)", ship=6.19, ship_in=5.5, min=30)
SHIP_SWITCH, SHIP_IN_SWITCH = SWITCH2["ship"], SWITCH2["ship_in"]
_SWITCH2 = re.compile(r"switch\s?2\b", re.I)
# «Nitendo Switch», «Nintedo Switch», «Switch 1» (eBay 28.09)
_SWITCH1 = re.compile(r"\bni\w{3,6}do\s*switch|switch\s*(?:oled|lite|v\s?[12]\b|[1]\b|konsole|console)", re.I)
_NOT_SWITCH = re.compile(r"hdmi|kvm|netzwerk|\blan\b|\bports?\b|usb|splitter|umschalt|\d\s?x\s?\d|schalter|gigabit|\bpoe\b|"
                         r"tp-?link|netgear|cisco|ubiquiti|mikrotik|zyxel|d-?link", re.I)
_SWITCH_ACCESSORY = re.compile(r"controller|joy[\s-]?cons?\b|\bspiele?\b|\bgame\b|dock|tasche|hülle|"
                               r"\bcase\b|schutz|folie|grip|ladestation|kamera|micro\s?sd|\bssd\b|amiibo|halterung|kabel|"
                               r"netzteil|ständer|skin|lenkrad|wheel|pedal|faceplate|cover|erweiterung", re.I)
_SWITCH_CONSOLE = re.compile(r"konsole|console|\bmit\b|\binkl|\+|\bbundle\b|fanpaket|konsolenpaket|mario kart world\b(?!\s*(?:code|spiel))|"
                             r"\bset\b", re.I)
_NEW_SEALED = re.compile(r"\bneu\b|originalverpackt|versiegelt|ungeöffnet|\bovp\b", re.I)


def evaluate_switch(title: str, price: float, shipping: float | None = None, vb: bool = False) -> dict | None:
    """Switch 2 або перша Switch (V1/V2, OLED, Lite). None — не Switch (далі оцінює RAM-логіка)."""
    # «HDMI-Switch 2x1», «TP-Link Switch 8 Port» — не консоль; але «Nintendo Switch … mit HDMI-Kabel» — консоль
    if _NOT_SWITCH.search(title) and not re.search(r"nintendo", title, re.I):
        return None
    if _SWITCH2.search(title):
        real = SWITCH2
    elif _SWITCH1.search(title):
        oled = re.search(r"\boled\b", title, re.I) and not re.search(r"(?:nicht|kein)\s+oled", title, re.I)
        real = SWITCH_OLED if oled else SWITCH_LITE if re.search(r"\blite\b", title, re.I) \
            else SWITCH_V2
    else:
        return None
    ship_in = real["ship_in"] if shipping is None else shipping
    if _REJECT.search(title):
        return _skip(title, price, "дефект / пошук / обмін / лише коробка / гра чи колекційне")
    if _is_accessory(title, _SWITCH_ACCESSORY, _SWITCH2 if real is SWITCH2 else _SWITCH1, price, real["p25"]):
        return _skip(title, price, "схоже на гру чи аксесуар, а не на консоль")
    if price < real["min"]:
        return _skip(title, price, f"дешевше €{real['min']} — гра, аксесуар, шахрайство або помилка в ціні", False)
    net_q = real["p25"] - costs(real["p25"], real["ship"])
    cap, good, excellent = net_q / 1.3, net_q / 1.6, net_q / 2.0
    cost = buy_cost(price, ship_in)
    verdict = tier(cost, cap, good, excellent, vb)
    two = real is SWITCH2
    notes = [("Перевір: консоль і Joy-Con 2 працюють" if two else "Перевір: Joy-Con без дрифту, екран і роз'єм цілі")
             + ", немає блокування акаунта Nintendo. Сильно вживана (подряпини, дрифт) продається дешевше за «швидку» ціну. "
             "Лише «Sicher bezahlen» або самовивіз."]
    if two and _NEW_SEALED.search(title) and price < 350:
        notes.insert(0, "«Нова / в плівці» за таку ціну — майже напевно шахрай (нова коштує ~€500). Лише Sicher bezahlen!")
    return dict(verdict=verdict, type=real["name"], price=price, cap=cap, good=good, excellent=excellent,
                quick_sale=real["p25"], median_sale=real["med"], sell_through=real["st"], profit_est=net_q - cost,
                brand="Nintendo", title=title, notes=notes, net_q=net_q, item_acc="die Switch 2" if two else "die Switch",
                check_q="Funktionieren Konsole und Joy-Con einwandfrei (kein Drift), keine Kontosperre?",
                buy_cost=cost, ship_in=ship_in, vb=vb)


evaluate_switch2 = evaluate_switch   # стара назва (тести, ka_share)


if __name__ == "__main__":
    import sys

    r = evaluate_console(sys.argv[1], float(sys.argv[2]))
    print(r if r else "не Xbox Series X / PS5 — оцінює ram_alert")
