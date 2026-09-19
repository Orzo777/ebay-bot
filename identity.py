"""Ідентифікація товару за назвою лота (детермінована, без ціни).

Навіщо: старий ключ був «перше слово + перший токен із цифрою» (наприклад
`t:new:pokemon-tcg`) і змішував ETB, бандли, тіни, японські дисплеї та сети в
одну «медіану». Тут ключ = категорія + тип продукту + мова + кількість паків +
код/назва сету/моделі. Незнайоме слово-шум лише ДРОБИТЬ ключ (безпечний напрям:
менше знахідок), але ніколи не ЗЛИВАЄ різні товари (небезпечний напрям).

Також тут — прифільтр придатності лота (`eligibility`): відсів аксесуарів, клонів,
лотів «Nx», передзамовлень, градованих/відкритих/розфарбованих позицій тощо.

Публічне API:
    describe(title, cat) -> Ident      # ключ, ознаки, причини відсіву
    Ident.key                          # рядок ключа або None
    Ident.exclude                      # список причин (порожній = лот придатний)
"""
import re
import unicodedata
from dataclasses import dataclass, field


def norm(s: str) -> str:
    s = (s or "").lower()
    s = (s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
           .replace("ß", "ss").replace("ø", "o").replace("æ", "ae").replace("å", "a")
           .replace("ł", "l").replace("đ", "d").replace("œ", "oe"))
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _tok(n: str) -> list:
    return [t for t in re.split(r"[^a-z0-9]+", n) if t]


# --------------------------------------------------------------------------- #
# Словники (усе в нормалізованій формі: нижній регістр, umlaut→ae/oe/ue/ss)
# --------------------------------------------------------------------------- #
LANGS = {
    "de": {"deutsch", "german", "deu", "de", "deutsche", "deutschen", "deutscher"},
    "en": {"englisch", "english", "eng", "en", "engl", "englische", "englischen",
           "englischer"},
    "jp": {"japanisch", "japanese", "jap", "jp", "japan", "jpn", "japanische",
           "japanischen", "japanischer"},
    "kr": {"koreanisch", "korean", "kr", "koreanische", "koreanischen", "kor"},
    "cn": {"chinesisch", "chinese", "cn", "chinesische", "chinesischen", "zh"},
    "es": {"spanisch", "spanish", "esp", "spanische", "spanischen", "espanol"},
    "fr": {"franzoesisch", "french", "francais", "franzoesische", "fra", "fr"},
    "it": {"italienisch", "italian", "ita", "italiano", "italienische", "italienischen"},
    "pt": {"portugiesisch", "portuguese"},
    "th": {"thai", "thailaendisch"},
    "id": {"indonesisch", "indonesian"},
}
_LANG_OF = {w: k for k, ws in LANGS.items() for w in ws}

# Шум продавця / рекламні слова / служб. слова — НЕ несуть ідентичності товару.
STOP = {
    # стан / упаковка / рекламні
    "neu", "new", "brandneu", "nagelneu", "neuware", "ovp", "original", "originalverpackt",
    "verpackt", "versiegelt", "sealed", "factory", "werkseitig", "fabrikversiegelt",
    "fabrikneu", "ungeoeffnet", "unopened", "mint", "top", "sofort", "lieferbar",
    "verfuegbar", "available", "instock", "stock", "lager", "versand", "versandkostenfrei",
    "kostenloser", "kostenlos", "free", "fast", "dispatch", "delivery", "shipping",
    "express", "schnell", "schneller", "schneller", "siehe", "beschreibung",
    "description", "see", "inkl", "mwst", "ust", "rechnung", "garantie", "verkaeufer",
    "seller", "shop", "haendler", "limited", "limitiert", "rare", "selten", "exklusiv",
    "offiziell", "official", "authentic", "genuine", "echt", "guter", "gut", "sehr",
    "zustand", "condition", "aus", "in", "im", "der", "die", "das", "den", "dem", "des",
    "the", "of", "and", "und", "mit", "with", "fuer", "for", "von", "zu", "zum", "zur",
    "a", "an", "at", "on", "to", "by", "or", "oder", "auf", "ab", "bei", "eu",
    "uk", "usa", "us", "wow", "sale", "angebot", "aktion", "deal", "preis", "price",
    "hot", "best", "super", "tolle", "toll", "perfekt", "perfect", "ideal", "geschenk",
    "gift", "sammler", "sammlung", "collectors", "collector",  # collector вертається як bkind нижче
    "eur", "euro", "stueck", "stk", "pcs", "pc", "st", "no", "nr", "x",
    "inklusive", "plus", "set", "kit", "box", "boxen", "boxes", "karton", "packung",
    "product", "produkt", "artikel", "item", "neue", "neuer", "neues", "neuen",
    "gesiegelt", "sigillato", "versiegelte", "versiegelten", "sealed", "shrink",
    "schrumpffolie", "folie", "wrapped", "ungeoeffnete", "brand", "bnib", "nib",
    "mib", "misb", "bnip", "vorrat", "genau", "abgebildet", "abbildung", "foto",
    "fotos", "bild", "bilder", "tcg", "ccg", "trading", "card", "cards", "karte",
    "karten", "sammelkarten", "sammelkartenspiel", "kartenspiel", "cardgame", "game",
    "spiel", "spiele", "games", "workshop", "hersteller", "marke", "brands",
    "verkauf", "biete", "verkaufe", "angeboten", "wie", "abzugeben", "privat",
    "privatverkauf", "nur", "einmalig", "einzigartig", "unique", "special", "spezial",
    "edition", "version", "ver", "typ", "type", "art", "modell", "model", "serie",
    "series", "ed", "ausgabe", "die", "ist", "sind", "wir", "sie", "ihr", "ich",
}
# ці слова важать у назві (типи бустерів МТГ тощо), тож повертаємо зі STOP
_KEEP_FROM_STOP = {"collector", "collectors", "edition", "set", "plus"}

PTYPE_RULES = [  # (ptype, ознаки-токени, ознаки-фрази-regex) — порядок = пріоритет
    ("case",  {"case", "cases", "karton"}, r"\b(4|6|8|12)\s?(x\s?)?(displays?|boxen|boxes|booster ?box)\b"),
    ("etb",   {"etb", "toptrainerbox", "trainerbox"}, r"\b(elite|top)[ -]?trainer\b"),
    ("bundle", {"bundle", "bundles", "boosterbundle", "boosterbundles", "fatpack", "geschenkpaket",
                "geschenkbox", "giftbox", "giftset"}, r"\bfat ?pack\b|\bgift bundle\b"),
    ("tin",   {"tin", "tins", "dose", "metalldose"}, r""),
    ("collection", {"collection", "kollektion", "abenteuerkoffer", "koffer", "premiumkollektion",
                    "binder", "ordner", "album", "schatzkiste"}, r"\bpremium collection\b"),
    ("blister", {"blister", "blisterpack", "3pack", "sleeved"}, r"\bcheck ?lane\b"),
    ("deck",  {"deck", "decks", "commander", "starter", "structure", "prebuilt", "trainerdeck",
               "themendeck", "kampfdeck"}, r""),
    ("display", {"display", "displays", "boosterbox", "boosterdisplay"}, r"\bbooster ?(box|display)\b"),
    ("pack",  {"pack", "packs", "booster", "boosters", "packung", "umschlag"}, r""),
]

_QTY = re.compile(r"(?:^|\s)(\d{1,2})\s?x(?=\s|$|\b)|\bx\s?(\d{1,2})(?=\s|$)|\b(\d{1,2})er[ -]?set\b|\blot of (\d{1,2})\b|\bset of (\d{1,2})\b")
_PACKS = re.compile(r"(\d{1,3})\s?(?:er\b|packs?\b|boosters?\b|umschlaege?\b|umschlag\b|packungen\b|booster packs?\b)")
_PACKS2 = re.compile(r"\b(\d{1,3})\s?(?:x\s?)?(?:booster|packs?)\b")
_SETNO = re.compile(r"\b(?:set|kapitel|chapter|serie|series)\s?-?\s?(\d{1,2})\b")
_CODES = [
    (re.compile(r"\b(op|st|eb|prb)\s?-?\s?0?(\d{1,2})\b"), lambda m: f"{m.group(1)}{int(m.group(2))}"),
    (re.compile(r"\b(sv\d{1,2}[a-z]{0,2}|swsh\d{1,2}|sm\d{1,2}[a-z]?|xy\d{1,2}|csv\d{1,2}[a-z]?|kp\d{2}|s\d{1,2}[a-z]{1,2}|m\d[a-z]?)\b"),
     lambda m: m.group(1)),
]

# градація актуальна лише для колекційних категорій (в електроніці «PSA-1» — кронштейн)
GRADED_TOK = {"psa", "bgs", "cgc", "wata", "vga", "graded", "gradiert", "slab", "pixelgrading"}

# глобальні причини відсіву (токен або фраза)
GLOBAL_EXCLUDE_TOK = {
    "proxy": "proxy/custom", "proxies": "proxy/custom", "custom": "proxy/custom",
    "replica": "proxy/custom", "replika": "proxy/custom", "repro": "proxy/custom",
    "nachbau": "proxy/custom", "kopie": "proxy/custom", "fake": "proxy/custom",
    "handmade": "proxy/custom", "gedruckt": "proxy/custom", "printed": "proxy/custom",
    "kompatibel": "compat/accessory", "compatible": "compat/accessory",
    "inspired": "compat/accessory", "inspiriert": "compat/accessory",
    "alternative": "compat/accessory", "alternativ": "compat/accessory",
    "unofficial": "compat/accessory", "fanmade": "compat/accessory",
    "leer": "empty/opened", "leere": "empty/opened", "leeren": "empty/opened",
    "empty": "empty/opened", "opened": "empty/opened", "geoeffnet": "empty/opened",
    "unsealed": "empty/opened", "resealed": "empty/opened", "reseal": "empty/opened",
    "beschaedigt": "damaged", "defekt": "damaged", "damaged": "damaged", "dellen": "damaged",
    "vorbestellung": "preorder", "vorbestellen": "preorder", "preorder": "preorder",
    "praeorder": "preorder", "vorverkauf": "preorder", "erscheint": "preorder",
    "sleeves": "accessory", "huellen": "accessory", "kartenhuellen": "accessory",
    "playmat": "accessory", "spielmatte": "accessory", "deckbox": "accessory",
    "acryl": "accessory", "acrylic": "accessory", "vitrine": "accessory",
    "staender": "accessory", "halter": "accessory", "holder": "accessory",
    "sockel": "accessory",
    "protector": "accessory", "protektor": "accessory", "schutzhuelle": "accessory",
    "lot": "lot", "konvolut": "lot", "sammlung": "lot", "posten": "lot",
    "mystery": "mystery", "ueberraschung": "mystery", "wundertuete": "mystery",
    "auswahl": "variation", "wahl": "variation", "waehlen": "variation",
    "verschiedene": "variation", "select": "variation", "choose": "variation",
}
_FOR_WORDS = {"fuer", "for", "passend", "fits", "pour", "per"}

_DENY_SUBSTR = ("ohrkissen", "ohrpolster", "ohrmuschel", "earpad", "deskstand", "headsetstaender",
                "handledsstotte", "handballenauflage", "wristrest", "wrist rest", "tastenkappen",
                "keycaps", "schutzhuelle", "schutzfolie", "displayschutz")

GLOBAL_EXCLUDE_RE = [
    (re.compile(r"\bpro ?painted\b|\bbemalt\b|\bpainted\b|\bkommission|\bcommission\b|\bfarbig\b"), "painted"),
    (re.compile(r"\b(zusammengebaut|assembled|aufgebaut|gebaut)\b"), "assembled"),
    (re.compile(r"\bohne (box|ovp|verpackung|booster|karten|kabel|zubehoer|ladekabel|spiel|game|modul|cartridge|code|inhalt|netzteil|ladegeraet)\b|\bwithout (box|packaging|game|cartridge|code|contents?)\b"), "incomplete"),
    (re.compile(r"\bnur (box|karton|verpackung|huelle|cover|kabel|zubehoer|spiel|steelbook|hoerspiel)\b|\b(box|case|steelbook|sleeve|packaging) only\b|\bempty\b"), "incomplete"),
    (re.compile(r"\bempty (box|display)\b|\bleer(e|er)? (box|display)\b"), "empty/opened"),
]


@dataclass
class Ident:
    key: str | None
    ptype: str = "?"
    lang: str = "unk"
    packs: int | None = None
    qty: int = 1
    names: tuple = ()
    code: str | None = None
    exclude: list = field(default_factory=list)
    spec_ok: bool = True


def _first(patterns, n):
    for rx, fn in patterns:
        m = rx.search(n)
        if m:
            return fn(m)
    return None


def _qty(n: str) -> int:
    for m in _QTY.finditer(n):
        for g in m.groups():
            if g:
                v = int(g)
                if v >= 2:
                    return v
    return 1


def _ptype(toks: set, n: str) -> str:
    for name, marks, rx in PTYPE_RULES:
        if toks & marks or (rx and re.search(rx, n)):
            return name
    return "other"


_COND_WORDS = {"neu", "new", "nagelneu", "brandneu", "ovp", "sealed", "versiegelt", "original",
               "originalverpackt", "unopened", "mint", "verpackt", "folie", "shrink",
               "ungeoeffnet", "top", "gut", "used", "wie", "neuware", "mwst", "inkl"}


def _has_combo_plus(n: str) -> bool:
    """«A + B» = комбо-лот. Але «Neu + OVP», «Sealed+Neu» — лише умови, не комбо."""
    for m in re.finditer(r"\+", n):
        left = re.findall(r"[a-z0-9]+", n[: m.start()])[-1:]
        right = re.findall(r"[a-z0-9]+", n[m.end():])[:1]
        if left and left[0] in _COND_WORDS:
            continue
        if right and right[0] in _COND_WORDS:
            continue
        return True
    return False



def describe(title: str, cat: dict | None = None) -> Ident:
    """cat = елемент config.CATEGORIES (може містити ключ 'profile')."""
    prof = profile_for(cat)
    n = norm(title)
    for rx, repl in prof.get("pre_rx", ()):
        n = rx.sub(repl, n)
    toks = _tok(n)
    tset = set(toks)
    reasons = []

    # --- глобальні + профільні причини відсіву ---
    for t in toks:
        r = GLOBAL_EXCLUDE_TOK.get(t)
        if r:
            reasons.append(r)
    for rx, r in GLOBAL_EXCLUDE_RE:
        if rx.search(n):
            reasons.append(r)
    for d in prof["deny"]:
        if (d in n) if len(d) >= 6 else (d in tset):
            reasons.append("deny:" + d)
    for d in _DENY_SUBSTR:
        if d in n:
            reasons.append("accessory:" + d)
    first_must = None
    if prof["must"]:
        model_grp = max(prof["must"], key=lambda g: max(len(w) for w in g))   # найспецифічніша група
        for i_, t_ in enumerate(toks):
            if any(t_ == w or (len(w) >= 3 and t_.startswith(w)) for w in model_grp):
                first_must = i_
                break
    if first_must and any(t in _FOR_WORDS for t in toks[:first_must]):
        reasons.append("accessory-for")

    if prof.get("graded"):
        for t in toks:
            if t in GRADED_TOK:
                reasons.append("graded")
    for grp in prof.get("form", ()):
        if not (tset & grp):
            reasons.append("no-form:" + "/".join(sorted(grp))[:20])
            break
    for grp in prof["must"]:
        hit = any((w in n) if len(w) >= 5 else (w in tset) for w in grp)
        if not hit and not any(rx.search(n) for rx in prof.get("must_rx", ())):
            reasons.append("irrelevant:" + "/".join(sorted(grp))[:24])
            break

    plus = _has_combo_plus(n)
    if plus and prof["plus_policy"] == "exclude":
        reasons.append("combo+")

    qty = _qty(n)
    ptype = _ptype(tset, n)
    if qty >= 2 and ptype != "case":
        reasons.append(f"qty{qty}")
    if prof["allow_ptypes"] and ptype not in prof["allow_ptypes"]:
        reasons.append("ptype:" + ptype)

    lang_hits = {_LANG_OF[t] for t in toks if t in _LANG_OF}
    lang = next(iter(lang_hits)) if len(lang_hits) == 1 else ("multi" if lang_hits else "unk")

    packs = None
    for rx in (_PACKS, _PACKS2):
        for m in rx.finditer(n):
            v = int(m.group(1))
            if 4 <= v <= 48:
                packs = v
                break
        if packs:
            break

    code = _first(_CODES, n) if prof["codes"] else None
    setno = _SETNO.search(n)
    if setno and not code:
        code = "set" + str(int(setno.group(1)))

    # --- імена: залишок токенів після відкидання шуму ---
    used = set()
    if code:
        m = re.findall(r"[a-z]+|\d+", code)
        used.update(m)
    strip = prof["strip"] | (STOP - _KEEP_FROM_STOP)
    names = []
    for t in toks:
        if t in strip or t in _LANG_OF or t in used:
            continue
        if any(t.startswith(w) and len(w) >= 5 and (not t[len(w):] or t[len(w):] in strip)
               for w in strip):
            continue                                   # «lorcanatcg», «pokemontcg» …
        if t.isdigit() and not (len(t) == 4 and t.startswith("20")):
            continue                              # голі числа (24, 36, 85…) — не ідентичність
        if len(t) < 2:
            continue
        # маркери типу вже враховані у ptype
        if any(t in marks for _, marks, _ in PTYPE_RULES):
            continue
        names.append(t)
    if prof.get("allow_variants") is not None:
        names = [t for t in names if t in prof["allow_variants"]]
    names = tuple(sorted(set(names)))[: prof["max_names"]]

    spec_ok = True
    if prof.get("need_spec"):
        spec_ok = bool(set(names) & prof["need_spec"])
    if code and prof["codes"]:
        names = ()
    if reasons:
        return Ident(None, ptype, lang, packs, qty, names, code, sorted(set(reasons)))

    parts = [prof["id"], ptype, lang]
    if plus:
        parts.append("bnd")
    if packs:
        parts.append(f"{packs}p")
    if code:
        parts.append(code)
    parts.extend(names)
    return Ident("|".join(parts), ptype, lang, packs, qty, names, code, [], spec_ok)


# --------------------------------------------------------------------------- #
# Профілі категорій
# --------------------------------------------------------------------------- #
def _slug(q: str) -> str:
    return "-".join(_tok(norm(q)))[:28]


def _fs(*xs):
    return frozenset(norm(x) for x in xs)


_DEFAULT = {
    "must": [], "deny": frozenset(), "allow_ptypes": None, "strip": frozenset(),
    "codes": False, "max_names": 6, "allow_variants": None,
            "graded": False, "must_rx": (), "plus_policy": "split", "form": (),
            "need_spec": None, "pre_rx": (),
}

# TCG «Booster Box/Display»: лише ptype=display
_TCG = dict(_DEFAULT, allow_ptypes={"display"}, codes=True, graded=True, plus_policy="exclude",
            strip=_fs("booster", "display", "displays", "box", "pack", "packs", "boosters",
                      "packungen", "umschlaege", "sammelkarten", "trading"),
            deny=_fs("waifu", "anime", "cosplay", "doujin", "tasche", "tragetasche", "einzelkarte", "einzelkarten", "single", "singles", "code", "playtest",
                     "proxy", "signed", "signiert", "autogramm", "topper"))
_MINI = dict(_DEFAULT, allow_ptypes=None, codes=False, max_names=6, plus_policy="exclude",
             deny=_fs("tasche", "tragetasche", "bits", "bitz", "umbau", "conversion", "stl", "resin", "3d",
                      "druck", "gussrahmen", "sprue", "einzeln", "einzelne", "einzelteile",
                      "dice", "wuerfel", "codex", "regelbuch", "rulebook", "buch", "book",
                      "katalog", "cards", "karten", "zubehoer", "gelaende", "terrain",
                      "scenery", "bases", "basen", "magnet", "magnete"))


def _mk(id_, base, **kw):
    d = dict(base)
    for k in ("deny", "strip", "allow_variants"):
        if k in kw:
            d[k] = frozenset(base.get(k) or ()) | frozenset(norm(x) for x in kw.pop(k))
    d.update(kw)
    d["id"] = id_
    d["must"] = [frozenset(norm(x) for x in g) for g in d.get("must", [])]
    d["form"] = [frozenset(norm(x) for x in g) for g in d.get("form", [])]
    return d


_ELEC_DENY = _fs("ohrpolster", "polster", "kissen", "cushion", "cushions", "earpad", "earpads",
              "ersatz", "cover", "kabel", "cable", "dongle", "adapter",
              "keycaps", "keycap", "tastenkappen", "handballenauflage", "wrist", "rest",
              "gehaeuse", "schalter", "switches", "stabilizer", "stabilisator", "plate",
              "pcb", "abdeckung", "staubschutz", "sockel", "halterung", "spinne", "shockmount",
              "popschutz", "windschutz", "stativ", "ausstellungssockel", "kapsel", "capsule",
              "skin", "aufkleber", "sticker", "schutzfolie", "case", "etui")

PROFILES = {
    # --- одиничні SKU: електроніка / ігри ---
    "Jabra Evolve2 65": _mk("jab65", _DEFAULT,
        must=[("jabra",), ("evolve2", "evolve"), ("65",)],
        form=[("headset", "kopfhoerer", "headphones", "stereo", "mono", "duo", "uc", "ms", "flex")],
        pre_rx=((re.compile(r"\bevolve\s+2\b"), "evolve2"),),
        need_spec=_fs("ms", "uc", "stereo", "mono", "duo", "flex"),
        deny=_ELEC_DENY - _fs("adapter", "halterung", "ladestation", "tasche", "case", "etui", "cover"),
        strip=_fs("jabra", "evolve2", "evolve", "65", "headset", "headphones", "kopfhoerer",
                  "wireless", "bluetooth", "noise", "cancelling", "anc", "business",
                  "buero", "office", "zertifiziert", "certified", "teams", ),
        allow_variants=_fs("flex", "uc", "ms", "duo", "mono", "stereo", "usba", "usbc", "usb",
                           "a", "c", "link380", "380", "stand", "ladestation", "ladestaender", "charging", "halterung",
                           "adapter", "tasche", "beige",
                           "schwarz", "black")),
    "Rode NT1": _mk("nt1", _DEFAULT,
        must=[("rode", "roede"), ("nt1", "nt1a")],
        pre_rx=((re.compile(r"\bnt1\s*-?\s*a\b"), "nt1a"),
                (re.compile(r"\b5(th|\.)?\s*gen(eration)?\b"), "5thgen")),
        form=[("mikrofon", "microphone", "kondensatormikrofon", "grossmembran", "xlr", "condenser",
               "studiomikrofon", "mic", "kit", "complete", "signature", "5th", "kondensator")],
        deny=_fs("kapsel", "capsule", "ersatz", "skin", "aufkleber", "sticker",
                 "ausstellungssockel", "sockel", "palisander"),
        strip=_fs("rode", "roede", "mikrofon", "microphone", "kondensatormikrofon", "condenser",
                  "studio", "studiomikrofon", "xlr", "nt1"),
        allow_variants=_fs("nt1a", "5thgen", "kit", "complete", "signature", "hybrid", "usb", "silent",
                           "black", "white", "v2")),
    "Keychron Tastatur": _mk("kchr", _DEFAULT,
        must=[("keychron",)],
        form=[("tastatur", "keyboard", "tastaturen", "mechanische", "mechanical", "teclado")],
        must_rx=(re.compile(r"\b[kqvclbs]\d{1,2}\b"),),
        deny=(_ELEC_DENY - _fs("schalter", "switches", "case", "etui", "cover")) | _fs("maus", "mouse"),
        strip=_fs("keychron", "tastatur", "keyboard", "mechanische", "mechanical", "wireless",
                  "bluetooth", "gaming", "layout", "hot", "swap", "hotswap", "rgb", "backlit",
                  "beleuchtet", "mac", "windows", "iso", "ansi", "de", "deutsch", "qwertz"),
        allow_variants=_fs("pro", "max", "se", "he", "ultra", "plus", "mini", "wired", "l",
                           "custom", "keychron")),
    "Electro-Harmonix Big Muff": _mk("bigmuff", _DEFAULT,
        must=[("big",), ("muff",)], deny=_ELEC_DENY,
        strip=_fs("electro", "harmonix", "ehx", "big", "muff", "pedal", "effektgeraet", "effect",
                  "effects", "gitarre", "guitar", "fuzz", "distortion", "verzerrer"),
        allow_variants=_fs("pi", "nano", "deluxe", "bass", "sovtek", "russian", "triangle", "little",
                           "ram", "tone", "wicker", "op", "amp", "opamp", "reissue", "mini",
                           "sustainer", "head", "germanium", "ge", "hall", "pedal_")),
    "Ravensburger tiptoi Starterset": _mk("tiptoi", _DEFAULT,
        must=[("tiptoi",)],
        deny=_fs("wimmelbuch", "puzzle", "globus", "cd", "hoerspiel",
                 "adapter", "tasche", "huelle", "kopfhoerer", "ladekabel", "batterie"),
        strip=_fs("ravensburger", "tiptoi", "starterset", "starter", "set", "stift", "lernstift",
                  "audio", "digital", "kinder", "spielzeug")),
    "Zelda Tears of the Kingdom Collector's Edition": _mk("zeldace", _DEFAULT, graded=True,
        must=[("zelda",), ("tears", "totk"), ("collector", "collectors", "sammleredition",
                                              "sammler")],
        deny=_fs("amiibo", "steelbook", "guide", "loesungsbuch", "artbook", "leer", "cover"),
        strip=_fs("nintendo", "switch", "the", "legend", "of", "zelda", "tears", "kingdom",
                  "totk", "collector", "collectors", "edition", "sammleredition", "spiel",
                  "game", "oled", "lite", "deutsch", "pal", "eu", "version", "sammler")),
    # --- Warhammer / мініатюри ---
    "Warhammer 40k Combat Patrol": _mk("wh-cp", _MINI,
        must=[("combat", "kampfpatrouille"), ("patrol", "kampfpatrouille")],
        strip=_fs("warhammer", "40k", "40000", "40", "000", "combat", "patrol", "kampfpatrouille",
                  "games", "workshop", "gw", "miniaturen", "tabletop", "starter", "armee", "army",
                  "box", "set", "edition", "2022", "2023", "2024", "2025", "de", "deutsch")),
    "Warhammer Necromunda": _mk("wh-necro", _MINI,
        must=[("necromunda",)],
        strip=_fs("warhammer", "40k", "necromunda", "games", "workshop", "gw", "forge", "world",
                  "miniaturen", "tabletop")),
    "Warhammer 40k Start Collecting": _mk("wh-sc", _MINI,
        must=[("start",), ("collecting", "collection")],
        strip=_fs("warhammer", "40k", "40000", "start", "collecting", "collection", "games",
                  "workshop", "gw", "miniaturen", "tabletop", "set", "box")),
    "Warhammer 40k Leviathan": _mk("wh-lev", _MINI,
        must=[("leviathan",)],
        strip=_fs("warhammer", "40k", "40000", "leviathan", "games", "workshop", "gw",
                  "miniaturen", "tabletop", "set", "box", "starter", "spiel", "game")),
    "Warhammer Underworlds": _mk("wh-uw", _MINI,
        must=[("underworlds",)],
        strip=_fs("warhammer", "underworlds", "games", "workshop", "gw", "miniaturen", "tabletop",
                  "set", "box", "spiel", "game")),
    "Star Wars Legion": _mk("swl", _MINI,
        must=[("legion",), ("star", "starwars")],
        strip=_fs("star", "wars", "legion", "atomic", "mass", "games", "amg", "fantasy", "flight",
                  "miniaturen", "tabletop", "erweiterung", "expansion")),
    "Magic The Gathering Booster Box sealed": _mk("mtg", _TCG,
        must=[("magic", "mtg", "gathering")],
        strip=_fs("magic", "mtg", "gathering", "wizards", "coast", "wotc", "the")),
    "Pokemon Booster Box versiegelt": _mk("pkm", _TCG,
        must=[("pokemon", "pokeman")],
        strip=_fs("pokemon", "nintendo", "tcg", "the")),
    "Disney Lorcana Booster Display": _mk("lorc", _TCG,
        must=[("lorcana",)],
        strip=_fs("disney", "lorcana", "ravensburger", "kapitel", "chapter", "set")),
    "One Piece Card Game Display OP": _mk("onep", _TCG,
        must=[("piece", "onepiece")], must_rx=(re.compile(r"\bop\s?-?\s?\d{1,2}\b"),),
        strip=_fs("one", "piece", "bandai", "namco", "game")),
    "Star Wars Unlimited Booster Box": _mk("swu", _TCG,
        must=[("unlimited",), ("star", "starwars")],
        strip=_fs("star", "wars", "unlimited", "fantasy", "flight", "games", "asmodee")),
    "Flesh and Blood Booster Box": _mk("fab", _TCG,
        must=[("flesh",), ("blood",)],
        strip=_fs("flesh", "and", "blood", "legend", "story", "fab")),
}


def profile_for(cat: dict | None) -> dict:
    q = (cat or {}).get("query", "")
    p = PROFILES.get(q)
    if p is None:
        p = _mk(_slug(q) or "gen", _DEFAULT)
    return p
