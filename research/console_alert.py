"""Оцінка оголошень Xbox Series X з листів Kleinanzeigen — поруч із RAM (ram_alert.py).

Чому Xbox: Microsoft підняла ціну 1.08.2026, вживані на eBay.de подорожчали на +29% за 2–3 міс.
(Terapeak, вживані, 30 днів до 24.09.2026: швидка ціна €509, медіана €561, ≥15 продажів/тиж),
а частина приватних продавців на KA ще продає за старою ціною (5–6 з ~50 оголошень за 7 год нижче стелі).

evaluate_console() повертає None, якщо назва — не Xbox Series X (тоді лист оцінює RAM-логіка),
інакше словник у форматі ram_alert.evaluate(), тож format_html/seller_template працюють без змін.

Ручна перевірка:
    python research/console_alert.py "Xbox Series X 1TB mit Controller" 290
"""
import os
import re

from ram_alert import buy_cost, tier

# Terapeak eBay.de, SOLD, conditionId=3000 (вживані), 30 днів до 24.09.2026.
# 26.09.2026: 83 продані за 30 днів (класифікація цим же оцінювачем) — p25 545, медіана 571
XBOX_SERIES_X = dict(p25=538, med=571, st=30, name="Xbox Series X (вживана)")   # Terapeak 10.10: 402 продано / 30 дн.
# 09.10.2026: комісії з продажу 0 — довідка eBay «Gebühren für private Verkäufer»: продаж у межах Німеччини
# безкоштовний (320 оголошень на місяць без Angebotsgebühr); Verify при автопублікації: «Final Value Fee waived».
# Рішення користувача 09.10 — прибрати одразу. Була 6,5% + 0,45 €. Буде виплата не повна — повернути тут.
# Konsolen: пересилка DHL Paket до 10 кг зі страховкою.
FEE, ORDER_FEE, SHIP = 0.0, 0.0, 10.49
SHIP_IN = 11.0   # пересилка, яку покупець платить продавцю (DHL Paket до 10 кг через «Sicher bezahlen»)
MIN_PRICE = 150  # дешевше — аксесуар, «Suche», шахрайство або помилка в ціні
SUSPICIOUS_BELOW = 250  # навіть за старими цінами (до 1.08) вживана Series X коштувала €330+

_SERIES_X = re.compile(r"serie[sn]?\s*x\b", re.I)
# «Series» саме по собі НЕ «Series S» (28.09: «Elite Controller Series 2» відкидав справжні Xbox Series X) — потрібен пробіл
_X_AND_S = re.compile(r"serie[sn]?\s*x\s*(?:/|\||&|und|oder|,)\s*s\b|\bserie[sn]?\s+s\s*(?:/|\||&|und|oder|,)\s*x\b", re.I)
_RETRO = re.compile(r"\batari\b|\bsega\b|mega\s?drive|dreamcast|commodore|\bamiga\b|\bc64\b|\bsnes\b|super\s+nintendo|"
                    r"\bnes\b|\bn64\b|nintendo\s*64|gamecube|game\s?boy|\bgba\b|\bwii(?:\s?u)?\b|\bpsp\b|ps\s?vita|"
                    r"playstation\s*[123]\b|\bps[123]\b|xbox\s*360|neo\s?geo|\bretro\b|vintage", re.I)
_ANY_OURS = re.compile(r"ps\s?5|playstation\s*5|xbox\s*series|switch", re.I)
_OTHER_CONSOLE = re.compile(r"\bserie[sn]?\s+s\b|\bseries-s\b|playstation|\bps[45]\b|switch|xbox\s*one", re.I)
# «Fn ACC Ps5» за €150 (eBay 28.09) — продаж ігрового акаунта, не консолі
_REJECT_BASE = re.compile(r"defekt|bastler|ersatzteil|kaputt|\bsuche\b|\bsuch\b|tausch|gesperrt|gebannt|banned|\bacc\b|\baccount|"
                     r"\bkonto\b|reparatur\w*|repair|leerkarton|(?:ovp|verpackung|karton|box)\s+(?:ist\s+)?leer\b|"
                     r"\bleere\s+(?:ovp|verpackung|karton|box)|dummy|attrappe|mainboard|ersatz-?\s?gehäuse|"
                     r"vermiet|\bmiete\b|gutschein|\bcoins\b|\bbann\b|ohne\s+bild|"
                     # 09.10: «Ps5 Slim 1TB hmdi kein signal» за 270 € — BUY (врятував лише фільтр Direkt kaufen)
                     r"kein\w*\s+(?:bild|signal|ton)\b|no\s+signal|h[dm]{2}i\w*\s+(?:defekt|kaputt|geht\s+nicht)|"
                     r"geht\s+(?:ständig|immer|oft|einfach|von\s+alleine|von\s+selbst)\s+aus\b|"
                     r"nicht\s+(?:mehr\s+)?einschalt|lädt\s+nicht|ohne\s+funktion|wasserschaden|"
                     r"ohne\s+(?:laufwerk|netzteil|konsole)|\bnur\s+(?:die\s+)?(?:ovp|karton|verpackung|controller)|"
                     # «Switch … HAC-001 nur Tablett» (eBay 29.09) — без дока/зарядки, не повна консоль
                     r"\bnur\s+(?:das\s+)?tabl?ett?\b|ohne\s+(?:dock|joy-?\s?cons?|ladeger|ladekabel)|"
                     # eBay (27.09): ігри-колекційки та послуги в категорії Konsolen; японська Switch 2 — лише японська мова
                     r"collector|legacy\s+edition|\bwata\b|\bvga\b|graded|samm?lung|samlung|\blegit\b|"
                     r"timestamp|troph|\bjapan|"
                     # «⚠️ ACHTUNG Betrüger» (KA 28.09) — попередження, не продаж
                     r"\bachtung\b|\bwarnung\b|\bvorsicht\b|betr[uü]e?g|\bscam|"
                     # дефект у назві: «(Laufwerk liest keine Disks mehr)» (eBay 29.09)
                     r"liest\s+keine|\bstreikt|laufwerk\s+(?:\w+\s+){0,2}(?:defekt|kaputt|geht\s+nicht)", re.I)
# гра / бонус до передзамовлення, а не консоль: «Switch 2 Pokemon Legenden Z-A + Vorbesteller Boni»,
# «Metroid Prime 4 – Power-Set + Schlüsselanhänger», «Mario Tennis Fever + Tennisball» (eBay 29.09) — якщо немає доказу консолі
_GAME_ONLY = re.compile(r"vorbestell|pre-?order|\bboni\b|steelbook|dual\s?pack|power-?set|schlüsselanhänger|tennisball|"
                        # «Ring Fit Adventure», «Nitro Deck», «Spiele Konvolut», «Beast Quest … Videospiel» — але
                        # «Videospielkonsole», «Konsole + Ring Fit», «OLED Konvolut» — консоль (pass 12)
                        r"\bvideospiele?\b|konvolut", re.I)
_KONSOLENSPIELE = re.compile(r"konsolen-?\s?spiele?\b", re.I)
_KONSOLENSPIELE_BUNDLED = re.compile(r"(?:\+|\bmit\b|\binkl\.?|\bund\b)\s*\d*\s*konsolen-?\s?spiele?\b", re.I)
# «CRKD Nitro Deck Switch OLED», «Ring Fit Adventure … OLED kompatibel» (pass 13) — «OLED/Handheld» тут не доказ консолі;
# консоль лише з «Konsole/HAC/…GB/TB» або коли аксесуар іде через «+ / mit / inkl» («Switch OLED + Ring Fit»)
_ACC_GAME = re.compile(r"ring\s?-?fit|nitro\s?deck", re.I)
_ACC_GAME_BUNDLED = re.compile(r"(?:\+|\bmit\b|\binkl\.?|\bund\b|&)\s*(?:\w+\s+)?(?:crkd\s+)?(?:ring\s?-?fit|nitro\s?deck)", re.I)
_STRONG_PROOF = re.compile(r"konsole\b|console|hac-?\d|cfi-?\d|\b\d{2,3}\s?gb\b|\d\s?tb\b", re.I)
# «Xbox Series X Display Riss» (eBay 29.09) — тріщина; але «kein Riss», «ohne Riss», «Siegel nicht gebrochen», «ungebrochen»
_CRACK = re.compile(r"\briss(?:e|en)?\b|gerissen|gebrochen|\bsprung\b|displayriss|displaybruch|glasbruch|gesprungen|"
                    r"haarriss|zersprungen|beschädigt", re.I)
_DEFECT_NOUNS = r"(?:kratzer|dellen|macken|beulen|brüche|schäden|risse?|sprünge|gebrauchsspuren|abnutzung)"
_CRACK_OK = re.compile(rf"(?:kein\w*|ohne|nicht)\s+(?:{_DEFECT_NOUNS}\s*(?:,|und|oder|&)?\s*){{0,4}}$|siegel\w*\s+(?:\w+\s+)?$|un$|"
                       # «OVP beschädigt», «Karton leicht beschädigt» — коробка, не консоль
                       r"(?:ovp|verpackung|karton|box|hülle|tasche)\s+(?:\w+\s+)?$", re.I)
_CRACK_OK_AFTER = re.compile(r"^\s*[:\-–]\s*(?:kein\w*|nein)\b", re.I)
_CONSOLE_PROOF = re.compile(r"konsole|console|handheld|\d\s?tb\b|\b\d{3}\s?gb\b|\bslim\b|\bdis[ck]\b|digital\s+edition|"
                            r"cfi-?\d|hac-?\d|\boled\b|\d\s*controller", re.I)
_CONSOLE_PROOF_PLURAL_OK = re.compile(r"konsolen", re.I)   # «Konsolen Spiele» — не доказ


class _Reject:
    """_REJECT_BASE + ігрові слова без доказу консолі; інтерфейс як у re.Pattern (.search)."""
    def search(self, title: str):
        proof = _CONSOLE_PROOF.search(re.sub(r"konsolen\w*", " ", title, flags=re.I))
        m = _REJECT_BASE.search(title) or (None if proof else _GAME_ONLY.search(title))
        if m:
            return m
        k = _KONSOLENSPIELE.search(title)
        if k and not _KONSOLENSPIELE_BUNDLED.search(title):
            return k
        a = _ACC_GAME.search(title)
        if a:
            # «… für Nintendo Switch Konsole» — «Konsole» тут про те, до чого аксесуар
            t2 = re.sub(r"\bf[üu]r\s+[\w\s]{0,30}?konsole\b|konsolen\w*", " ", title, flags=re.I)
            name = re.search(r"switch|konsole|console", title, re.I)
            listed = (name and name.start() < a.start()
                      and re.search(r"(?:\+|\bmit\b|\binkl\.?|\bund\b|&|,|\||\s[-–]\s)", title[name.end():a.start()])
                      and re.search(r"oled|\blite\b|\bv[12]\b|dock|controller|konvolut|\d+\s*spiele|tasche|joy|"
                                    r"paket|bundle|set\b|komplett|edition|\bgrau\b|\bneon\b|\brot\b|\bblau\b|schwarz|wei(?:ß|ss)",
                                    title, re.I))
            if not (_STRONG_PROOF.search(t2) or listed):
                return a
        for c in _CRACK.finditer(title):
            if not (_CRACK_OK.search(title[max(0, c.start() - 60):c.start()]) or _CRACK_OK_AFTER.search(title[c.end():c.end() + 12])):
                return c
        return None


_REJECT = _Reject()
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
    if _RETRO.search(title) and _ANY_OURS.search(title):
        # 09.10: «Atari 2600 Konsole … + PS5 Controller» (eBay) пішла як BUY за 260 € — ретро-консоль, а наша — лише згадка
        return _skip(title, price, "ретро / інша консоль (Atari, Sega, Wii, PS1–3 …) — не наш товар")
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
    weak = weak_console_title(title, _SERIES_X)
    if weak:
        return _skip(title, total, weak)
    if total < MIN_PRICE:
        return _skip(title, total, f"дешевше €{MIN_PRICE} — аксесуар, шахрайство або помилка в ціні", False)
    real = XBOX_SERIES_X
    net_q = real["p25"] - costs(real["p25"])
    cap, good, excellent = net_q / 1.3, net_q / 1.6, net_q / 2.0
    cost = buy_cost(price, ship_in)
    verdict = tier(cost, cap, good, excellent, vb)
    notes = ["Перевір привід, контролер, блокування · лише «Sicher bezahlen» або самовивіз"]
    if total < SUSPICIOUS_BELOW:
        notes.insert(0, "Підозріло дешево — частина таких оголошень шахраї, жодних переказів наперед")
    return dict(verdict=verdict, type=real["name"], price=total, cap=cap, good=good, excellent=excellent,
                quick_sale=real["p25"], median_sale=real["med"], sell_through=real["st"], profit_est=net_q - cost,
                brand="Microsoft", title=title, notes=notes, net_q=net_q, needs_console_proof=weak == "",
                item_acc="die Xbox Series X", check_q="Laufen Laufwerk und Controller einwandfrei, keine Sperre?",
                buy_cost=cost, ship_in=ship_in, vb=vb)


# ---------------- PS5 (додано 26.09.2026) ----------------
# Terapeak eBay.de, SOLD, вживані, 30 днів до 26.09.2026 (див. PS5_* нижче). Slim і перша («товста») модель
# продаються майже однаково, тому Disc — один тип; Digital — окремий (дешевший); Pro не купуємо (забирає бюджет,
# на KA вже по ринку).
PS5_DISC = dict(p25=450, med=475, st=30, name="PS5 з дисководом (вживана)")   # Terapeak 10.10: 399 продано / 30 дн.
PS5_DIGITAL = dict(p25=414, med=444, st=25, name="PS5 Digital (вживана)")   # Terapeak 10.10: 129 продано / 30 дн.
PS5_SUSPICIOUS_BELOW = 280

_PS5 = re.compile(r"\bps\s?-?5\b|play\s?-?station\s*-?5|playst\w*ion\s*5", re.I)   # «Play Station 5», «Playsttstion 5»
_PS5_PRO = re.compile(r"(?<!preis )\bpro\b(?!\s+(?:stück|stk|st\.|riegel))", re.I)
_PS5_OTHER = re.compile(r"portal|\bportable\b|porta\s+remote|remote[\s-]?play|\bvr\s?2?\b|psvr|xbox|switch|\bps4\b|playstation\s*4", re.I)
_PS5_ACCESSORY = re.compile(r"controller|dualsense|headset|laufwerk|disc\s*drive|\bssd\b|festplatte|ständer|halterung|"
                            r"lüfter|kühler|skin|folie|hülle|tasche|\bcase\b|cover|faceplate|netzteil|kabel|\bspiele?\b|"
                            r"\bgame\b|fernbedienung|ladestation|dock|kamera|lenkrad|wheel|pedal|erweiterung|expansion|"
                            # «PS5 Pulse Explore Wireless Earbuds», «PS5 … ähnl. wie Scuf, AIM» — навушники, кастомні контролери
                            r"earbuds|kopfh[öo]rer|\bpulse\b|scuf|\baim\b|paddles?", re.I)
# «Edition» НЕ ознака консолі: «DualSense LeBron James Limited Edition» — контролер (27.09)
_PS5_CONSOLE = re.compile(r"konsole|console|\d\s?tb\b|825\s?gb|\bmit\b|\binkl|\+|\bbundle\b|\bslim\b", re.I)


# 30.09 (eBay): «Baldur's Gate 3 Deluxe Edition PS5 - Neu, Versiegelt» €170 і «PS5 Bundle: Pulse Elite Headset (NEU/OVP) +
# HD-Kamera» €160 пішли як «PS5 — БЕРИ». Сильний доказ консолі — модель/пам'ять/«Konsole»; без нього:
#  • гра — назва платформи ПІСЛЯ власної назви («<Гра> PS5») + ознаки гри (Edition, versiegelt, USK…) → SKIP;
#  • аксесуари, що не йдуть у комплекті з консоллю (Headset, Kamera, Lenkrad…) → SKIP;
#  • інакше — консоль лише якщо опис це підтвердить (needs_console_proof, див. ram_alert.refine_by_desc).
_STRONG_CONSOLE_TITLE = re.compile(r"konsole\b|console|spielkonsole|\bslim\b|\bdis[ck]\b|digital|\b\d{3}\s?gb\b|\d\s?tb\b|"
                                   r"cfi-?\d|laufwerk|\bfat\b|\bstandard\b", re.I)
_GAME_SIGNAL = re.compile(r"edition|deluxe|\bgoty\b|versiegelt|sealed|\busk\b|\bpegi\b|\bspiel\b|\bgame\b|steelbook|"
                          r"\bdlc\b|remaster|\bcollection\b", re.I)
_TITLE_FILLER = re.compile(r"^(?:sony|playstation|microsoft|xbox|nintendo|original|neue?s?|neuwertige?|gebrauchte?|verkaufe|"
                           r"meine|biete|top|wie|die|eine?|der|das|und|mit|zustand|sehr|gut|guter|super|ps|series|x|s)$", re.I)
_NON_BUNDLE_ACC = re.compile(r"headset|kopfh[öo]rer|earbuds|\bpulse\b|kamera|camera|lenkrad|wheel|pedal|\bvr\b|ständer|"
                             r"halterung|lüfter|kühler|ladestation|fernbedienung|media\s?remote|faceplate|cover|skin", re.I)


def weak_console_title(title: str, name_re: re.Pattern) -> str | None:
    """→ причина SKIP («гра» / «аксесуари») або "" (слабкі ознаки — треба підтвердження в описі) або None (сильний доказ)."""
    if _STRONG_CONSOLE_TITLE.search(re.sub(r"konsolen\w*", " ", title, flags=re.I)):
        return None
    name = name_re.search(title)
    if name:
        words = [w for w in re.findall(r"[A-Za-zÄÖÜäöüß'][\wÄÖÜäöüß']+", title[:name.start()]) if not _TITLE_FILLER.match(w)]
        if len(words) >= 2 and _GAME_SIGNAL.search(title):
            return "схоже на гру для консолі (назва гри перед назвою платформи)"
    if _NON_BUNDLE_ACC.search(title):
        return "схоже на аксесуари без самої консолі"
    return ""


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
    digital = bool(_DIGITAL.search(title) or re.search(r"cfi-?\s?\d{4}\s?b(?:\d{0,2}[a-z]?)?\b", title, re.I))
    # «Digital + Laufwerk» / «Laufwerk für PS5» — дисковод окремо: без слова «Konsole» це аксесуар
    if _is_accessory(title, _PS5_ACCESSORY, _PS5, price, (PS5_DIGITAL if digital else PS5_DISC)["p25"]):
        return _skip(title, price, "схоже на аксесуар, а не на консоль")
    weak = weak_console_title(title, _PS5)
    if weak:
        return _skip(title, price, weak)
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
        notes.insert(0, "Підозріло дешево — частина таких оголошень шахраї, жодних переказів наперед")
    return dict(verdict=verdict, type=real["name"], price=price, cap=cap, good=good, excellent=excellent,
                quick_sale=real["p25"], median_sale=real["med"], sell_through=real["st"], profit_est=net_q - cost,
                brand="Sony", title=title, notes=notes, net_q=net_q, item_acc="die PS5", needs_console_proof=weak == "",
                check_q=("Läuft alles einwandfrei (Controller), keine PSN-Sperre?" if digital
                         else "Laufen Laufwerk und Controller einwandfrei, keine PSN-Sperre?"),
                buy_cost=cost, ship_in=ship_in, vb=vb)


# ---------------- Nintendo Switch 2 і перша Switch (26–27.09.2026) ----------------
# Terapeak, продані вживані, 30 днів. Switch 2: ≥34 шт., p25 €382 (−3% за 3 міс.); на KA €180–270 трапляються,
# але «нова в плівці» за пів ціни (роздріб ~€500) — типовий шаблон шахраїв. Перша Switch дешевшає (OLED −17%,
# Lite −23% за рік) і на KA зазвичай дорожча за стелю — оцінюємо, щоб дати пораду «вигідно лише до …»,
# картки йдуть лише при реальній маржі. Легші за Xbox/PS5: DHL Paket до 5 кг €7.69 (Lite — до 2 кг €6.19).
# 04.10: продані вживані на eBay.de за 30 днів (сторінка «Verkaufte Artikel», 7 166 продажів за 90 днів, класифіковано за
# моделлю): Switch 2 — 363 шт. (p25 370, медіана 390, ціна росте; активних лише ~60 — розкуповують за дні);
# OLED — 386 (150/170, −4% за 3 міс.), V1/V2 — 716 (107/125), Lite — 239 (85/98). st — % проданих від (продані + активні):
# Switch 2 85%, решта 31–37% — продається, якщо ціна на рівні продажів (активні оголошення — на 30–50% дорожчі і висять).
SWITCH2 = dict(p25=381, med=403, st=85, name="Nintendo Switch 2 (вживана)", ship=7.69, ship_in=7.0, min=150)   # Terapeak 10.10: 322 / 30 дн.
SWITCH_OLED = dict(p25=150, med=170, st=37, name="Nintendo Switch OLED (вживана)", ship=7.69, ship_in=7.0, min=60)
SWITCH_V2 = dict(p25=107, med=125, st=31, name="Nintendo Switch V1/V2 (вживана)", ship=7.69, ship_in=7.0, min=50)
SWITCH_LITE = dict(p25=85, med=98, st=35, name="Nintendo Switch Lite (вживана)", ship=6.19, ship_in=5.5, min=30)
SHIP_SWITCH, SHIP_IN_SWITCH = SWITCH2["ship"], SWITCH2["ship_in"]
SWITCH1_ON = os.getenv("SWITCH1_ON", "0") == "1"   # перша Switch вимкнена 04.10; «1» — повернути оцінку
_SWITCH2 = re.compile(r"switch\s?-?\s?2(?!\d|[.,]\d)", re.I)   # «Switch 2Schwarz», «Switch-2» (eBay 29.09)
# «Nitendo Switch», «Nintedo Switch», «Switch 1» (eBay 28.09)
_SWITCH1 = re.compile(r"\bni\w{3,6}do\s*switch|switch\s*(?:oled|lite|v\s?[12]\b|[1]\b|konsole|console)", re.I)
# Сильні ознаки консолі (модель, пам'ять, Joy-Con, док) — на відміну від «Zustand/Edition/+», які пишуть і в назвах ігор
_SWITCH1_STRONG = re.compile(r"oled|lite|\bv\s?[12]\b|switch\s*1\b|konsole|console|hac-?\d|\d{2,3}\s?gb|joy-?\s?cons?|"
                             r"\bdock|neon|handheld", re.I)
# «nur Konsole», «Ersatzkonsole», «ohne Zubehör», «Tablet … Ersatz» (eBay 29.09: три такі «вигідні» Switch — лише планшет)
_SWITCH1_INCOMPLETE = re.compile(r"\bnur\s+(?:die\s+)?konsole\b(?!\W*(?:(?:und|mit|\+|inkl\.?)\s*(?:\w+\s+)?(?:dock|joy)|"
                                 r"(?:keine|ohne)\s+spiele?\b(?!.*(?:kein|ohne)\w*\s+(?:dock|joy))))|"
                                 r"ersatz-?\s?konsole|ohne\s+zubeh[öo]r", re.I)
_SWITCH1_EVIDENCE = re.compile(r"oled|lite|\bv\s?[12]\b|switch\s*1\b|konsole|console|hac-?\d|\d{2,3}\s?gb|joy-?\s?cons?|"
                               r"\bdock|neon|grau|\bmit\b|\bin[ck]l|bundle|set\b|paket|komplett|handheld|"
                               # 29.09 (eBay, 10 з 14 «без доказу» були консолями): «Animal Crossing Edition», «Switch 2017»,
                               # «+ 3 Spiele + Controller», «Plus 2 Spiele», «rot blau», «gebraucht», «guter Zustand»
                               r"edition|\b20(?:1[7-9]|2\d)\b|\bplus\b|\d+\s*spiele|controller|\brot\b|\bblau\b|schwarz|"
                               r"gebraucht|zustand|switch\b.*\+", re.I)
# 01.10 (eBay-аукціон): «EA Sports FC 26 - Nintendo Switch 2» за ставку €10 пішов як консоль. Назва гри перед
# «Switch 2» без жодної ознаки консолі/комплекту — гра. «Edition» тут НЕ ознака консолі (ігри теж «Edition»).
_SWITCH2_EVIDENCE = re.compile(r"konsole|console|\d{3}\s?gb|joy-?\s?cons?|\bdock|\bmit\b|\bin[ck]l|bundle|\bset\b|paket|"
                               r"komplett|handheld|\+|controller|gebraucht|zustand|\bovp\b", re.I)
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
    weak = False
    if _SWITCH2.search(title):
        real = SWITCH2
        pre = [w for w in re.findall(r"[A-Za-zÄÖÜäöüß'][\wÄÖÜäöüß']+", title[:_SWITCH2.search(title).start()])
               if not _TITLE_FILLER.match(w) and not re.match(r"ni\w{3,6}do$", w, re.I)]
        if len(pre) >= 2 and not _SWITCH2_EVIDENCE.search(title):
            return _skip(title, price, "схоже на гру для Switch 2 (назва гри перед назвою платформи)")
    elif _SWITCH1.search(title):
        # «Nintendo Switch Mario Kart 8 Deluxe» — гра з назвою платформи; консоль видно лише з моделі чи комплекту
        if not _SWITCH1_EVIDENCE.search(title):
            return _skip(title, price, "з назви не видно, що це консоль (схоже на гру для Switch)")
        # 04.10 (рішення користувача): першу Switch не купуємо — заробіток 15–30 €, ціна падає, на аукціонах іде за ринковою
        # (V1/V2 за ~80 €), а V1/V2 плутають зі Switch 2. Розпізнаємо лише, щоб не прийняти за Switch 2 / RAM.
        if not SWITCH1_ON:
            return _skip(title, price, "перша Switch (V1/V2, OLED, Lite) — не купуємо: маржа мінімальна (04.10)")
        weak = not _SWITCH1_STRONG.search(title)
        inc = _SWITCH1_INCOMPLETE.search(title)
        if inc and inc.group(0).lower().startswith("nur"):
            rest = title[inc.end():]
            if re.search(r"\b(?:dock|joy)", rest, re.I) and not re.search(r"(?:kein\w*|ohne)\s+(?:\w+\s+)?(?:dock|joy)", rest, re.I):
                inc = None
        if inc and not re.search(r"\blite\b", title, re.I):
            return _skip(title, price, "лише планшет — без дока, Joy-Con чи зарядки")
        if re.search(r"\boled\b", title, re.I) and re.search(r"\bv\s?[12]\b|\blite\b", title, re.I):
            return _skip(title, price, "кілька моделей в одному оголошенні — ціна найдешевшої")
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
                needs_console_proof=(real is not SWITCH2 and weak), brand="Nintendo", title=title, notes=notes, net_q=net_q, item_acc="die Switch 2" if two else "die Switch",
                check_q="Funktionieren Konsole und Joy-Con einwandfrei (kein Drift), keine Kontosperre?",
                buy_cost=cost, ship_in=ship_in, vb=vb)


evaluate_switch2 = evaluate_switch   # стара назва (тести, ka_share)


# ---------------- Сторож цін (01.10): поточні ціни продажу консолей з research/ram_prices.json ----------------
# research/price_refresh.py щодня зсуває p25/медіану разом з цінами вживаних консолей на eBay (як для RAM). Словники типів
# оновлюються НА МІСЦІ (на них посилаються за тотожністю: «real is SWITCH2»). Знімок Terapeak у коді змінився —
# поправку з файлу ігноруємо, доки сторож не перерахує. RAM_PRICES_OFF=1 — лише цифри Terapeak (тести).
CONSOLE_TYPES = {"XBOX_SERIES_X": XBOX_SERIES_X, "PS5_DISC": PS5_DISC, "PS5_DIGITAL": PS5_DIGITAL, "SWITCH2": SWITCH2}
if SWITCH1_ON:
    CONSOLE_TYPES.update(SWITCH_OLED=SWITCH_OLED, SWITCH_V2=SWITCH_V2, SWITCH_LITE=SWITCH_LITE)
CONSOLE_BASE = {k: (v["p25"], v["med"]) for k, v in CONSOLE_TYPES.items()}


def _apply_refresh(path: str | None = None):
    import json
    import os
    if os.getenv("RAM_PRICES_OFF") == "1":
        return
    try:
        with open(path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "ram_prices.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    for k, e in (data.get("consoles") or {}).items():
        t = CONSOLE_TYPES.get(k)
        if t and (e.get("p25_tp"), e.get("med_tp")) == CONSOLE_BASE[k] and e.get("p25") and e.get("med"):
            t["p25"], t["med"] = e["p25"], e["med"]


_apply_refresh()


if __name__ == "__main__":
    import sys

    r = evaluate_console(sys.argv[1], float(sys.argv[2]))
    print(r if r else "не Xbox Series X / PS5 — оцінює ram_alert")
