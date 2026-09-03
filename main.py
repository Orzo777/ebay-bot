"""Моніторинг аномально низьких цін на eBay (німецький ринок) → Telegram.

Використовує ТІЛЬКИ офіційний eBay Browse API (без скрапінгу HTML).

Дві незалежні осі оцінки лота:
  1. ЦІНА  — чи дешевше за історичну медіану ЦЬОГО товару (за epid), а не за
     медіану поточного live-пошуку;
  2. ЛІКВІДНІСТЬ — чи товар узагалі швидко купують. Оцінюється наближено з
     власного полінгу (швидкість зникнення відстежених лістингів як проксі
     продажів, з відсіюванням relist-циклів). Без Marketplace Insights API
     точність принципово обмежена → є явний стан UNKNOWN. Сповіщення НЕ
     глушаться за низької ліквідності — лише маркуються, і в кожне вкладається
     посилання на завершені продажі для ручної перевірки.

Запуск:
    python main.py --once
    python main.py --interval 900
    python main.py --simulate-days 35 [--reset-history] [--verbose]
"""

import argparse
import base64
import json
import os
import re
import statistics
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote_plus

import requests

import config

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

VERBOSE = False


# --------------------------------------------------------------------------- #
# Утиліти
# --------------------------------------------------------------------------- #
def log(msg: str = "") -> None:
    if msg == "":
        print(flush=True)
        return
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def vlog(msg: str, quiet: bool) -> None:
    if not quiet:
        log(msg)


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _short(item_or_title, n: int = 55) -> str:
    t = item_or_title.get("title") if isinstance(item_or_title, dict) else item_or_title
    t = t or "?"
    return t if len(t) <= n else t[: n - 1] + "…"


def _days_between(a_iso: str, b_iso: str) -> int:
    return (date.fromisoformat(b_iso[:10]) - date.fromisoformat(a_iso[:10])).days


def _norm_title(s: str) -> str:
    """Нормалізація назви перед токенізацією:
    lower + фолд німецьких умлаутів (ä→ae, ö→oe, ü→ue, ß→ss) + зняття решти
    діакритики (é→e, ñ→n, á→a, ç→c …). Без цього регексп-спліт по не-[a-z0-9äöüß]
    рубав слова НАВПІЛ на акцентованих літерах: "pokémon" → "pok"+"mon",
    "Sián" → "si"+"n" тощо."""
    s = (s or "").lower()
    s = (s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
           .replace("ß", "ss"))
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _tokens(s: str) -> list:
    return [w for w in re.split(r"[^a-z0-9]+", _norm_title(s)) if w]


def _title_sim(a: str, b: str) -> float:
    ta = {w for w in _tokens(a) if len(w) >= 2}
    tb = {w for w in _tokens(b) if len(w) >= 2}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _fmt_v(v):
    return "—" if v is None else f"{v:.1f}"


def _fmt_d(d):
    return "—" if d is None else f"{d:.0f} дн."


def _request_with_backoff(method: str, url: str, **kwargs):
    delay = config.BACKOFF_BASE
    last_err = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=config.HTTP_TIMEOUT, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_err = exc
            log(f"  мережева помилка: {exc} — пауза {delay:.0f}s "
                f"({attempt}/{config.MAX_RETRIES})")
            time.sleep(delay)
            delay *= config.BACKOFF_BASE
            continue

        if resp.status_code == 429:
            retry_after = _to_float(resp.headers.get("Retry-After")) or delay
            last_err = RuntimeError("429 Too Many Requests")
            log(f"  429 rate limit — пауза {retry_after:.0f}s "
                f"({attempt}/{config.MAX_RETRIES})")
            time.sleep(retry_after)
            delay *= config.BACKOFF_BASE
            continue

        if resp.status_code >= 500:
            last_err = RuntimeError(f"{resp.status_code} {resp.reason}")
            log(f"  eBay {resp.status_code} — пауза {delay:.0f}s "
                f"({attempt}/{config.MAX_RETRIES})")
            time.sleep(delay)
            delay *= config.BACKOFF_BASE
            continue

        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        return resp.json()

    raise RuntimeError(f"Запит {url} не вдався після {config.MAX_RETRIES} спроб ({last_err})")


# --------------------------------------------------------------------------- #
# eBay
# --------------------------------------------------------------------------- #
class EbayClient:
    def __init__(self):
        self._token = None
        self._expires_at = 0.0

    def _refresh_token(self):
        creds = f"{config.EBAY_APP_ID}:{config.EBAY_CERT_ID}".encode()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + base64.b64encode(creds).decode(),
        }
        data = {"grant_type": "client_credentials", "scope": config.EBAY_OAUTH_SCOPE}
        payload = _request_with_backoff(
            "POST", config.EBAY_OAUTH_URL, headers=headers, data=data
        )
        self._token = payload["access_token"]
        ttl = int(payload.get("expires_in", 7200))
        self._expires_at = time.time() + ttl - 60
        log(f"Отримано новий eBay OAuth токен (дійсний ~{ttl // 60} хв)")

    def _auth_header(self):
        if not self._token or time.time() >= self._expires_at:
            self._refresh_token()
        return f"Bearer {self._token}"

    def _headers(self):
        return {
            "Authorization": self._auth_header(),
            "X-EBAY-C-MARKETPLACE-ID": config.EBAY_MARKETPLACE_ID,
            "Content-Type": "application/json",
        }

    def search(self, query: str, min_price, sort: str | None = None) -> list:
        params = {
            "q": query,
            "filter": config.search_filter(min_price),
            "limit": config.SEARCH_LIMIT,
        }
        if sort:
            params["sort"] = sort
        data = _request_with_backoff(
            "GET", config.EBAY_BROWSE_SEARCH_URL, headers=self._headers(), params=params
        )
        return data.get("itemSummaries") or []


def fetch_summaries(client, cat, now, day=0, total_days=1, force_mock=False):
    """ПОДВІЙНИЙ запит на категорію:
        A — best_match (без sort): репрезентативна популяція для медіани;
        B — sort=price: найдешевший реальний лот (шум уже відсічено price:[min..]).
    Обʼєднання за item_id. Повертає (merged_list, n_a, n_b, only_b)."""
    query = cat["query"]
    mp = cat.get("min_price", config.MIN_PRICE)
    if force_mock or not config.EBAY_CREDS_OK or client is None:
        from mock_data import mock_search

        a = mock_search(query, now, day, total_days, sort=None, min_price=mp)
        b = mock_search(query, now, day, total_days, sort=config.SEARCH_SORT_B, min_price=mp)
    else:
        a = client.search(query, mp, sort=None)
        b = client.search(query, mp, sort=config.SEARCH_SORT_B)

    a_ids = {i.get("itemId") for i in a}
    merged: dict[str, dict] = {}
    for it in list(a) + list(b):
        iid = it.get("itemId")
        if iid and iid not in merged:
            merged[iid] = it
    only_b = sum(1 for iid in merged if iid not in a_ids)
    return list(merged.values()), len(a), len(b), only_b


# --------------------------------------------------------------------------- #
# Розбір лота
# --------------------------------------------------------------------------- #
def total_price(item: dict):
    price = _to_float((item.get("price") or {}).get("value"))
    if price is None:
        return None
    shipping = 0.0
    options = item.get("shippingOptions") or []
    if options:
        cost = _to_float((options[0].get("shippingCost") or {}).get("value"))
        if cost is not None:
            shipping = cost
    return round(price + shipping, 2)


def item_currency(item: dict) -> str:
    return (item.get("price") or {}).get("currency") or config.CURRENCY


def blocklisted(item: dict):
    title_l = (item.get("title") or "").lower()
    return next((w for w in config.CONDITION_BLOCKLIST if w in title_l), None)


# --- Гібридний ключ товару ------------------------------------------------
# epid, якщо є (точна привʼязка до каталогу). Інакше — грубий підпис із назви:
# бренд + модель + місткість/обсяг + бакет стану. Колір, комплектація, "OVP",
# емоційні прикметники — відкидаються. Такі товари позначаються lower_confidence.
# набори проганяються через _norm_title() → усі елементи в тій самій формі,
# що й токени назви (умлаути → ae/oe/ue/ss).
_KEY_STOP = {_norm_title(w) for w in (
    "neu", "new", "ovp", "original", "originalverpackt", "versiegelt", "sealed",
    "set", "kit", "und", "mit", "für", "fur", "the", "der", "die", "das", "von",
    "inkl", "incl", "stück", "stk", "pcs", "pc", "top", "sofort",
    "versand", "blitzversand", "rechnung", "händler", "garantie",
    "gebraucht", "wie", "sehr", "gut", "zustand", "aktion", "angebot", "deal",
    "günstig", "selten", "rar", "komplett", "vollständig",
    "boxed", "box", "karton", "generalüberholt", "refurbished",
    "ersatz", "zubehör", "kleinteil", "teile", "lesen",
    "beschreibung", "fotos", "siehe", "nagelneu", "makelloser",
    # платформи/формати/загальні слова — не несуть ідентичності товару
    "nintendo", "switch", "playstation", "ps3", "ps4", "ps5", "xbox", "konsole",
    "konsolen", "spiel", "spiele", "game", "games", "edition", "deluxe",
    "standard", "disc", "disk", "bluray", "dvd", "code", "key", "download",
    "dlc", "eu", "de", "uk", "usa", "pal", "englisch", "english", "deutsch",
)}
_KEY_COLOR = {_norm_title(w) for w in (
    "schwarz", "weiss", "weiß", "blau", "rot", "grün", "gelb", "rosa",
    "pink", "grau", "silber", "gold", "black", "white", "blue", "red", "green",
    "grey", "gray", "space", "titan", "natur", "beige", "türkis",
    "lila", "violett", "orange", "braun", "anthrazit", "mint",
)}
_KEY_NUMNOISE = {
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "16", "18",
    "24", "36", "48", "1x", "2x", "3x", "4x", "5x", "6x", "2er", "3er", "4er",
    "5er", "6er", "2023", "2024", "2025", "5g", "4g",
}
_KEY_CAP = re.compile(r"(\d{1,4})\s?(gb|tb|kg|ml|l|w|zoll|personen)\b")


def condition_bucket(item: dict) -> str:
    return "refurb" if str(item.get("conditionId") or "") in ("2000", "2500") else "new"


def product_key(item: dict):
    """(key, lower_confidence). key=None → назву не вдалось розібрати."""
    epid = item.get("epid")
    if epid:
        return f"epid:{epid}", False

    title = _norm_title(item.get("title") or "")
    toks = [w for w in re.split(r"[^a-z0-9]+", title) if w]
    caps = sorted({f"{m.group(1)}{m.group(2)}" for m in _KEY_CAP.finditer(title)})
    sig_words = [w for w in toks if w.isalpha() and len(w) >= 3
                 and w not in _KEY_STOP and w not in _KEY_COLOR]
    brand = sig_words[0] if sig_words else None
    model = next((w for w in toks if any(c.isdigit() for c in w)
                  and w not in _KEY_NUMNOISE and 2 <= len(w) <= 12), None)
    # немає номера моделі → беремо друге значуще слово (Booster / Starter / Puzzle …)
    second = sig_words[1] if (model is None and len(sig_words) >= 2) else None
    parts = sorted({p for p in ([brand, model, second] + caps) if p})
    if not parts:
        return None, True
    return "t:" + condition_bucket(item) + ":" + "-".join(parts), True


def seller_ok(item: dict):
    seller = item.get("seller") or {}
    score = int(_to_float(seller.get("feedbackScore")) or 0)
    pct = _to_float(seller.get("feedbackPercentage"))
    pct = pct if pct is not None else 0.0
    if score < config.MIN_SELLER_FEEDBACK_SCORE:
        return False, f"feedbackScore={score} < {config.MIN_SELLER_FEEDBACK_SCORE}"
    if pct < config.MIN_SELLER_FEEDBACK_PCT:
        return False, f"feedbackPercentage={pct:.1f}% < {config.MIN_SELLER_FEEDBACK_PCT}%"
    return True, ""


# --------------------------------------------------------------------------- #
# Історія цін + життєвий цикл лістингів
# --------------------------------------------------------------------------- #
class HistoryStore:
    """price_history.json (ключ = product_key: "epid:<N>" або "t:<стан>:<підпис>"):
        history      : { key: [ {date, price_total, item_id, currency} ] }
        listings     : { key: { item_id: {first_seen,last_seen,first_seen_price,
                          last_seen_price,seller,title,item_creation_date,status,
                          gone_date,relisted_from} } }
        alerted_item_ids : [ item_id, ... ]        # дедублікація Telegram
        discovery_seen   : [ item_id, ... ]        # дедублікація discovery-логу
        key_map      : { query: канонічний product_key }
        key_query    : { product_key: query, під яким уперше побачили }
        key_conf     : { product_key: true }       # true = визначено за назвою (нижча впевненість)
    status ∈ active | gone | relisted
    """

    def __init__(self, path: str):
        self.path = path
        self.history: dict[str, list] = {}
        self.listings: dict[str, dict] = {}
        self.alerted: set[str] = set()          # item_id, надіслані в Telegram
        self.discovery_seen: set[str] = set()   # item_id, залоговані в discovery
        self.key_map: dict[str, str] = {}
        self.key_query: dict[str, str] = {}
        self.key_conf: dict[str, bool] = {}
        self._pindex: dict[str, set] = {}
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            log("price_history.json пошкоджений — починаю з чистого стану")
            return
        self.history = data.get("history", {}) or {}
        self.listings = data.get("listings", {}) or {}
        self.alerted = set(data.get("alerted_item_ids", []) or [])
        self.discovery_seen = set(data.get("discovery_seen", []) or [])
        self.key_map = data.get("key_map", {}) or {}
        self.key_query = data.get("key_query", {}) or {}
        self.key_conf = data.get("key_conf", {}) or {}
        for key, points in self.history.items():
            self._pindex[key] = {p["item_id"] for p in points}

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "history": self.history,
                "listings": self.listings,
                "alerted_item_ids": sorted(self.alerted),
                "discovery_seen": sorted(self.discovery_seen),
                "key_map": self.key_map,
                "key_query": self.key_query,
                "key_conf": self.key_conf,
            }, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    # --- цінова історія ---
    def points_in_window(self, epid, now, days) -> list:
        cutoff = (now - timedelta(days=days)).date().isoformat()
        return [p for p in self.history.get(epid, []) if p["date"] >= cutoff]

    def has_point(self, epid, item_id) -> bool:
        return item_id in self._pindex.get(epid, ())

    def record_point(self, epid, item_id, price_total, currency, now) -> bool:
        if self.has_point(epid, item_id):
            return False
        self.history.setdefault(epid, []).append({
            "date": now.date().isoformat(),
            "price_total": round(price_total, 2),
            "item_id": item_id,
            "currency": currency,
        })
        self._pindex.setdefault(epid, set()).add(item_id)
        return True

    # --- життєвий цикл лістингів ---
    def observe_listing(self, epid, item, now) -> bool:
        d = now.date().isoformat()
        price = total_price(item)
        book = self.listings.setdefault(epid, {})
        iid = item["itemId"]
        ent = book.get(iid)
        if ent is None:
            book[iid] = {
                "first_seen": d,
                "last_seen": d,
                "first_seen_price": price,
                "last_seen_price": price,
                "seller": (item.get("seller") or {}).get("username") or "?",
                "title": item.get("title") or "",
                "item_creation_date": (item.get("itemCreationDate") or "")[:10] or None,
                "status": "active",
                "gone_date": None,
                "relisted_from": None,
            }
            return True
        ent["last_seen"] = d
        ent["last_seen_price"] = price
        if ent["status"] != "active":       # знову з'явився → рахуємо активним
            ent["status"] = "active"
            ent["gone_date"] = None
        return False

    def sweep_gone(self, now) -> int:
        today = now.date().isoformat()
        n = 0
        for book in self.listings.values():
            for ent in book.values():
                if ent["status"] != "active":
                    continue
                if _days_between(ent["last_seen"], today) >= config.LISTING_GONE_AFTER_DAYS:
                    ent["status"] = "gone"
                    ent["gone_date"] = today
                    n += 1
        return n

    def match_relists(self, quiet: bool) -> int:
        n = 0
        for key, book in self.listings.items():
            for iid, e in book.items():
                if e["status"] != "gone":
                    continue
                for cid, c in book.items():
                    if cid == iid or c.get("relisted_from") or c["seller"] != e["seller"]:
                        continue
                    gap = _days_between(e["last_seen"], c["first_seen"])
                    if gap < -1 or gap > config.RELIST_WINDOW_DAYS:
                        continue
                    p0, p1 = e["last_seen_price"], c["first_seen_price"]
                    if not p0 or abs(p1 - p0) / p0 > config.RELIST_PRICE_TOLERANCE:
                        continue
                    if _title_sim(e["title"], c["title"]) < config.RELIST_TITLE_SIMILARITY:
                        continue
                    e["status"] = "relisted"
                    c["relisted_from"] = iid
                    n += 1
                    vlog(f"  ↻ relist [{key}] {e['seller']}: {iid} → {cid} "
                         f"(розрив {gap} дн., {p0:.2f}→{p1:.2f})", quiet)
                    break
        return n

    def query_for(self, key) -> str:
        return self.key_query.get(key, "?")


# --------------------------------------------------------------------------- #
# Ліквідність
# --------------------------------------------------------------------------- #
@dataclass
class LiquidityReport:
    tier: str            # OK | MEDIUM | LOW | UNKNOWN
    headline: str
    velocity_per_week: float | None
    median_days_to_sell: float | None
    active_now: int
    sold_proxy: int
    relisted: int
    track_days: int
    note: str
    sold_url: str


_MARKET_DOMAIN = {
    "EBAY_DE": "ebay.de", "EBAY_AT": "ebay.at", "EBAY_GB": "ebay.co.uk",
    "EBAY_US": "ebay.com", "EBAY_FR": "ebay.fr", "EBAY_IT": "ebay.it",
    "EBAY_ES": "ebay.es", "EBAY_CA": "ebay.ca", "EBAY_AU": "ebay.com.au",
    "EBAY_NL": "ebay.nl", "EBAY_PL": "ebay.pl", "EBAY_IE": "ebay.ie",
}


def sold_search_url(query: str) -> str:
    dom = _MARKET_DOMAIN.get(config.EBAY_MARKETPLACE_ID, "ebay.com")
    return (f"https://www.{dom}/sch/i.html?_nkw={quote_plus(query)}"
            f"&LH_Sold=1&LH_Complete=1&_sop=13")


class SelfTrackedLiquidity:
    """Наближена оцінка попиту з власного полінгу бота."""

    label = "власний proxy"

    def __init__(self, store: HistoryStore):
        self.store = store

    def assess(self, epid: str, query: str, now: datetime) -> LiquidityReport:
        url = sold_search_url(query)
        book = self.store.listings.get(epid, {})
        if not book:
            return LiquidityReport("UNKNOWN", "невідомо", None, None, 0, 0, 0, 0,
                                   "лістинги ще не відстежувались", url)

        today = now.date().isoformat()
        win_start = (now - timedelta(days=config.LIQUIDITY_WINDOW_DAYS)).date().isoformat()
        track_days = _days_between(min(e["first_seen"] for e in book.values()), today)

        active = sold = relisted = 0
        durations = []
        for e in book.values():
            st = e["status"]
            if st == "active":
                active += 1
            elif st == "relisted" and (e["gone_date"] or "") >= win_start:
                relisted += 1
            elif st == "gone" and (e["gone_date"] or "") >= win_start:
                sold += 1
                ref = e.get("item_creation_date") or e["first_seen"]
                durations.append(_days_between(ref, e["gone_date"]))

        eff_days = min(track_days, config.LIQUIDITY_WINDOW_DAYS)
        velocity = (sold / (eff_days / 7)) if eff_days >= 7 else None
        median_dts = statistics.median(durations) if durations else None

        tier, headline, note = self._classify(
            track_days, sold, relisted, velocity, median_dts, active
        )
        return LiquidityReport(tier, headline, velocity, median_dts, active,
                               sold, relisted, track_days, note, url)

    @staticmethod
    def _classify(track_days, sold, relisted, velocity, median_dts, active):
        glut = active >= config.SUPPLY_GLUT_MIN and sold <= 1
        if track_days < config.MIN_LIQUIDITY_TRACK_DAYS:
            return ("UNKNOWN", "невідомо",
                    f"відстеження лише {track_days} дн. "
                    f"(треба ≥ {config.MIN_LIQUIDITY_TRACK_DAYS})")
        if (sold + relisted) < config.MIN_LIQUIDITY_EVENTS and not glut:
            return ("UNKNOWN", "невідомо",
                    f"завершень циклу лише {sold + relisted} "
                    f"(треба ≥ {config.MIN_LIQUIDITY_EVENTS})")
        if glut and (velocity is None or velocity < config.LIQ_HIGH_PER_WEEK):
            return ("LOW", "затоварення",
                    f"{active} активних пропозицій, продажів-проксі майже нема ({sold})")
        if (velocity is not None and velocity >= config.LIQ_HIGH_PER_WEEK
                and (median_dts is None or median_dts <= config.LIQ_FAST_DAYS)):
            return ("OK", "швидко продається",
                    f"~{_fmt_v(velocity)}/тиждень, час до продажу {_fmt_d(median_dts)}")
        if ((velocity is not None and velocity < config.LIQ_LOW_PER_WEEK)
                or (median_dts is not None and median_dts > config.LIQ_SLOW_DAYS)):
            return ("LOW", "повільний перепродаж",
                    f"~{_fmt_v(velocity)}/тиждень, час до продажу {_fmt_d(median_dts)}")
        return ("MEDIUM", "помірний попит",
                f"~{_fmt_v(velocity)}/тиждень, час до продажу {_fmt_d(median_dts)}")


class MarketplaceInsightsLiquidity:
    """Заглушка під реальні sold-дані. Активний sold-endpoint eBay
    (buy/marketplace_insights/v1_beta/item_sales/search) потребує окремого
    схвалення через App Check ticket на developer.ebay.com."""

    label = "Marketplace Insights API"

    def __init__(self, store: HistoryStore, client=None):
        self.store = store
        self.client = client

    def assess(self, epid: str, query: str, now: datetime) -> LiquidityReport:
        raise NotImplementedError(
            "Marketplace Insights API не підключено. Коли eBay надасть доступ — "
            "реалізувати виклик item_sales/search за epid і зібрати LiquidityReport "
            "з реальних продажів за останні 90 днів."
        )


def build_liquidity_provider(store: HistoryStore, client):
    if config.MARKETPLACE_INSIGHTS_ENABLED:
        return MarketplaceInsightsLiquidity(store, client)
    return SelfTrackedLiquidity(store)


# --------------------------------------------------------------------------- #
# Сповіщення
# --------------------------------------------------------------------------- #
def format_message(a: dict) -> str:
    item = a["item"]
    seller = item.get("seller") or {}
    r: LiquidityReport = a["liq"]
    cur = a["currency"]
    conf = ("⚠ товар визначено ЗА НАЗВОЮ (немає epid) — нижча впевненість\n"
            if a.get("lower_confidence") else "")
    return (
        f"🔻 PRICE OK · LIQUIDITY {r.tier} — {a['query']}\n"
        f"ключ: {a['key']}\n"
        f"{conf}\n"
        f"{item.get('title', '—')}\n"
        f"Стан: {item.get('condition', '?')} (conditionId {item.get('conditionId', '?')})\n\n"
        f"Ціна з доставкою: {a['price_total']:.2f} {cur}\n"
        f"Історична медіана ({a['hist_points']} спост. / {config.HISTORY_WINDOW_DAYS} дн.): "
        f"{a['hist_median']:.2f} {cur}\n"
        f"Нижче медіани на: {a['pct_below']:.0f}%\n"
        f"Продавець: {seller.get('username', '—')} "
        f"(score {seller.get('feedbackScore')}, {seller.get('feedbackPercentage')}%)\n\n"
        f"Ліквідність ({a['liq_provider']}, вікно {config.LIQUIDITY_WINDOW_DAYS} дн.):\n"
        f"  рівень: {r.tier} — {r.headline}\n"
        f"  продажів-проксі: {r.sold_proxy}   relisted виключено: {r.relisted}\n"
        f"  швидкість: {_fmt_v(r.velocity_per_week)}/тиждень   "
        f"медіанний час до зникнення: {_fmt_d(r.median_days_to_sell)}\n"
        f"  активних пропозицій зараз: {r.active_now}   відстеження: {r.track_days} дн.\n"
        f"  ⚠ ПЕРЕВІР реальні продажі перед купівлею:\n  {r.sold_url}\n\n"
        f"{item.get('itemWebUrl', '')}"
    )


def send_telegram(text: str) -> None:
    url = f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text,
               "disable_web_page_preview": False}
    data = _request_with_backoff("POST", url, json=payload)
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API: {data}")


def deliver(a: dict) -> None:
    text = format_message(a)
    if config.TELEGRAM_CREDS_OK:
        send_telegram(text)
    else:
        print("\n[DRY RUN] Замість відправки в Telegram:\n"
              "-------------------------------------------\n"
              f"{text}\n"
              "-------------------------------------------\n")


# --------------------------------------------------------------------------- #
# Обробка
# --------------------------------------------------------------------------- #
def new_stats() -> dict:
    return {
        "keys_seen": set(),
        "keys_lowconf": set(),
        "skipped_seller": 0,
        "skipped_blocklist": 0,
        "no_key": 0,
        "only_b": 0,
        "new_listings": 0,
        "gone": 0,
        "relisted_now": 0,
        "alerts": [],
        "discovery": [],
        "by_tier": {"OK": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0},
    }


def merge_stats(dst: dict, src: dict) -> None:
    dst["keys_seen"] |= src["keys_seen"]
    dst["keys_lowconf"] |= src["keys_lowconf"]
    for k in ("skipped_seller", "skipped_blocklist", "no_key", "only_b",
              "new_listings", "gone", "relisted_now"):
        dst[k] += src[k]
    dst["alerts"].extend(src["alerts"])
    dst["discovery"].extend(src["discovery"])
    for t, v in src["by_tier"].items():
        dst["by_tier"][t] += v


def append_discovery(record: dict) -> None:
    with open(config.DISCOVERY_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def process_category(cat, summaries, store, liq, now, stats, quiet) -> None:
    query = cat["query"]
    min_price = cat.get("min_price", config.MIN_PRICE)

    by_key: dict[str, list] = {}
    no_key = 0
    for item in summaries:
        key, low_conf = product_key(item)
        if key is None:
            no_key += 1
            continue
        by_key.setdefault(key, []).append(item)
        store.key_conf[key] = low_conf

    stats["no_key"] += no_key
    if no_key:
        vlog(f"[{query}] {no_key} лот(ів) без розбірливого ключа — поза статистикою", quiet)
    if not by_key:
        vlog(f"[{query}] жодного придатного ключа — пропускаю", quiet)
        return

    canonical = max(by_key, key=lambda k: len(by_key[k]))
    cat["key"] = canonical
    store.key_map[query] = canonical
    n_lc = sum(1 for k in by_key if store.key_conf.get(k))
    vlog(f"[{query}] ключів у видачі: {len(by_key)} ({n_lc} за назвою); "
         f"канонічний {canonical} ({len(by_key[canonical])} лот.)", quiet)

    for key, items in by_key.items():
        store.key_query.setdefault(key, query)
        _process_key(query, key, items, store, liq, now, stats, quiet, min_price)


def _process_key(query, key, items, store, liq, now, stats, quiet, min_price):
    stats["keys_seen"].add(key)
    low_conf = store.key_conf.get(key, False)
    if low_conf:
        stats["keys_lowconf"].add(key)
    tag = f"{key}" + ("~назва" if low_conf else "")

    valid = []
    for item in items:
        item_id = item.get("itemId")
        tp = total_price(item)
        if item_id is None or tp is None or tp < min_price:
            continue

        bl = blocklisted(item)
        if bl:
            stats["skipped_blocklist"] += 1
            vlog(f"  [{tag}] «{_short(item)}» — стоп-слово стану “{bl}”, пропуск", quiet)
            continue

        # життєвий цикл трекаємо ДО фільтра репутації: продаж лота від
        # слабкого продавця — теж ринковий сигнал.
        if store.observe_listing(key, item, now):
            stats["new_listings"] += 1

        ok, reason = seller_ok(item)
        if not ok:
            stats["skipped_seller"] += 1
            vlog(f"  [{tag}] «{_short(item)}» — низька репутація продавця "
                 f"({reason}), пропуск", quiet)
            continue
        valid.append((item, tp))

    if not valid:
        return

    window = store.points_in_window(key, now, config.HISTORY_WINDOW_DAYS)
    have = len(window)

    if have < config.MIN_HISTORY_POINTS:
        added = sum(store.record_point(key, it["itemId"], tp, item_currency(it), now)
                    for it, tp in valid)
        vlog(f"  Накопичення історії {key} ({query}): "
             f"{have + added}/{config.MIN_HISTORY_POINTS} (+{added})", quiet)
        return

    hist_median = statistics.median(p["price_total"] for p in window)
    vlog(f"  {key} ({query}): історична медіана {hist_median:.2f} "
         f"за {have} спост. / {config.HISTORY_WINDOW_DAYS} дн.", quiet)

    seen_set = store.discovery_seen if config.DISCOVERY_MODE else store.alerted

    for item, tp in valid:
        item_id = item["itemId"]
        is_new = not store.has_point(key, item_id)
        store.record_point(key, item_id, tp, item_currency(item), now)
        if not is_new or item_id in seen_set:
            continue
        ratio = tp / hist_median if hist_median else 1.0
        if ratio >= config.ANOMALY_THRESHOLD:
            continue

        try:
            report = liq.assess(key, query, now)
        except Exception as exc:  # noqa: BLE001
            log(f"  ⚠ оцінка ліквідності не вдалась для {key}: {exc}")
            report = LiquidityReport("UNKNOWN", "невідомо", None, None, 0, 0, 0, 0,
                                     f"помилка провайдера: {exc}", sold_search_url(query))

        a = {
            "query": query, "key": key, "lower_confidence": low_conf, "item": item,
            "price_total": tp, "currency": item_currency(item),
            "hist_median": hist_median, "hist_points": have,
            "pct_below": (1 - ratio) * 100,
            "liq": report, "liq_provider": liq.label,
        }
        stats["by_tier"][report.tier] = stats["by_tier"].get(report.tier, 0) + 1

        if config.DISCOVERY_MODE:
            append_discovery({
                "ts": now.isoformat(),
                "date": now.date().isoformat(),
                "category": query,
                "product_key": key,
                "lower_confidence": low_conf,
                "item_id": item_id,
                "price_total": tp,
                "currency": a["currency"],
                "hist_median": round(hist_median, 2),
                "hist_points": have,
                "pct_below": round(a["pct_below"], 1),
                "liquidity_tier": report.tier,
                "liquidity_velocity": (None if report.velocity_per_week is None
                                       else round(report.velocity_per_week, 2)),
                "liquidity_days_to_sell": (None if report.median_days_to_sell is None
                                           else round(report.median_days_to_sell, 1)),
                "liquidity_active": report.active_now,
                "liquidity_note": report.note,
            })
            store.discovery_seen.add(item_id)
            stats["discovery"].append(a)
            vlog(f"  🔎 DISCOVERY [{report.tier}]{'·LC' if low_conf else ''} "
                 f"{query} [{key}]: {tp:.2f} {a['currency']} "
                 f"(-{a['pct_below']:.0f}%, медіана {hist_median:.2f})", quiet)
            continue

        try:
            deliver(a)
        except Exception as exc:  # noqa: BLE001
            log(f"  ✗ доставка сповіщення не вдалась ({item_id}): {exc}")
            continue
        store.alerted.add(item_id)
        stats["alerts"].append(a)
        log(f"  🔔 PRICE OK · LIQUIDITY {report.tier}{'·LC' if low_conf else ''} "
            f"[{key}] {query}: {tp:.2f} {a['currency']} vs медіана {hist_median:.2f} "
            f"(-{a['pct_below']:.0f}%) | {report.headline}")


# --------------------------------------------------------------------------- #
# Прогони
# --------------------------------------------------------------------------- #
def run_pass(client, cats, store, liq, now, *, day=0, total_days=1,
             force_mock=False, quiet=False) -> dict:
    stats = new_stats()
    for cat in cats:
        try:
            summaries, n_a, n_b, only_b = fetch_summaries(
                client, cat, now, day, total_days, force_mock)
            stats["only_b"] += only_b
        except Exception as exc:  # noqa: BLE001
            log(f"[{cat['query']}] запит не вдався: {exc}")
            continue
        try:
            process_category(cat, summaries, store, liq, now, stats, quiet)
            if only_b:
                vlog(f"[{cat['query']}] запит B (price-asc) додав {only_b} "
                     f"лот(ів) поза топ-50 best_match", quiet)
        except Exception as exc:  # noqa: BLE001
            log(f"[{cat['query']}] обробка не вдалась: {exc}")
            continue

    stats["gone"] = store.sweep_gone(now)
    stats["relisted_now"] = store.match_relists(quiet)
    store.save()
    return stats


def simulate(cats, store, liq, n_days: int) -> dict:
    log(f"Симуляція {n_days} віртуальних днів на mock-даних "
        f"(аномалії — в останній день; ліквідність прогрівається поступово)")
    agg = new_stats()
    base = datetime.now(timezone.utc)
    for d in range(n_days):
        now = base - timedelta(days=(n_days - 1 - d))
        quiet = not VERBOSE and d < n_days - 1
        if not quiet:
            log()
            log(f"═══ Віртуальний день {d + 1}/{n_days} — {now.date().isoformat()} ═══")
        s = run_pass(None, cats, store, liq, now, day=d, total_days=n_days,
                     force_mock=True, quiet=quiet)
        merge_stats(agg, s)
        if quiet:
            n_cand = len(s["discovery"]) + len(s["alerts"])
            tail = ""
            if n_cand:
                tail = "  → " + ", ".join(f"{t}×{n}" for t, n in s["by_tier"].items() if n)
            label = "кандидатів" if config.DISCOVERY_MODE else "аномалій"
            log(f"день {d + 1:>2}/{n_days} {now.date().isoformat()}: "
                f"+{s['new_listings']} лот. (B+{s['only_b']}), {s['gone']} зникнень, "
                f"{s['relisted_now']} relist, {label}: {n_cand}{tail}")
    return agg


# --------------------------------------------------------------------------- #
# Підсумок
# --------------------------------------------------------------------------- #
def _key_kind(key: str) -> str:
    return "epid" if key.startswith("epid:") else "назва"


def print_summary(store: HistoryStore, liq, now: datetime, agg: dict) -> None:
    ready, accumulating = [], []
    for key in sorted(store.history):
        n = len(store.points_in_window(key, now, config.HISTORY_WINDOW_DAYS))
        (ready if n >= config.MIN_HISTORY_POINTS else accumulating).append((key, n))

    n_epid = sum(1 for k in store.history if k.startswith("epid:"))
    n_title = len(store.history) - n_epid

    log()
    log("═════════════════════════ ПІДСУМОК ═════════════════════════")
    log(f"Маркетплейс: {config.EBAY_MARKETPLACE_ID}   поріг ціни: "
        f"{config.ANOMALY_THRESHOLD:.2f} × історична медіана")
    log(f"Провайдер ліквідності: {liq.label}   вікно: {config.LIQUIDITY_WINDOW_DAYS} дн.")
    log(f"Унікальних ключів товару: {len(store.history)}  "
        f"(epid: {n_epid}, за назвою: {n_title})   "
        f"готові: {len(ready)}   накопичують: {len(accumulating)}")
    log(f"Запит B (price-asc) додав лотів поза топ-50 best_match: {agg['only_b']}")
    if accumulating:
        for key, n in accumulating[:12]:
            log(f"    накопичення  {key:<26.26} {store.query_for(key):<28} "
                f"{n}/{config.MIN_HISTORY_POINTS}")

    log()
    log("Ліквідність по всіх відстежених ключах:")
    log(f"    {'kind':<6} {'ключ':<28.28} {'категорія':<26} {'LIQ':<8} "
        f"vel/тижд  ~днів  актив  прод  relist")
    for key in sorted(set(store.history) | set(store.listings)):
        try:
            r = liq.assess(key, store.query_for(key), now)
        except Exception as exc:  # noqa: BLE001
            log(f"    {_key_kind(key):<6} {key:<28.28} {store.query_for(key):<26} ПОМИЛКА ({exc})")
            continue
        log(f"    {_key_kind(key):<6} {key:<28.28} {store.query_for(key):<26} {r.tier:<8} "
            f"{_fmt_v(r.velocity_per_week):>7}  {_fmt_d(r.median_days_to_sell):>6}  "
            f"{r.active_now:>5}  {r.sold_proxy:>4}  {r.relisted:>5}")

    log()
    log(f"Відсіяно за репутацією продавця: {agg['skipped_seller']}   "
        f"стоп-листом стану: {agg['skipped_blocklist']}   "
        f"лотів без ключа: {agg['no_key']}")
    log(f"Життєвий цикл: relist-подій виявлено (виключено з velocity): "
        f"{sum(1 for b in store.listings.values() for e in b.values() if e['status'] == 'relisted')}")

    log()
    tiers = agg["by_tier"]
    lc_cand = sum(1 for a in (agg["discovery"] + agg["alerts"]) if a.get("lower_confidence"))
    if config.DISCOVERY_MODE:
        d = agg["discovery"]
        log(f"РЕЖИМ РОЗВІДКИ (DISCOVERY_MODE=true) — Telegram-сповіщення вимкнені.")
        log(f"Кандидатів дописано в {config.DISCOVERY_LOG_FILE}: {len(d)}  "
            f"(OK×{tiers['OK']}  MEDIUM×{tiers['MEDIUM']}  LOW×{tiers['LOW']}  "
            f"UNKNOWN×{tiers['UNKNOWN']};  за назвою/нижча впевненість: {lc_cand})")
        log(f"Аналіз за весь період: python main.py --discovery-report")
    else:
        log(f"СПОВІЩЕНЬ: {len(agg['alerts'])}  "
            f"(OK×{tiers['OK']}  MEDIUM×{tiers['MEDIUM']}  LOW×{tiers['LOW']}  "
            f"UNKNOWN×{tiers['UNKNOWN']};  нижча впевненість: {lc_cand})")
        for a in agg["alerts"]:
            r = a["liq"]
            mark = " ·LC(назва)" if a.get("lower_confidence") else ""
            log(f"  🔔 {a['query']} [{a['key']}]{mark}  {a['price_total']:.2f} {a['currency']}  "
                f"vs медіана {a['hist_median']:.2f}  (-{a['pct_below']:.0f}%)")
            log(f"       ЦІНА: {a['hist_points']} спост. / {config.HISTORY_WINDOW_DAYS} дн.   "
                f"ЛІКВІДНІСТЬ: {r.tier} — {r.note}")
            log(f"       {a['item'].get('title', '')}")
            log(f"       перевірка продажів: {r.sold_url}")
    log("═══════════════════════════════════════════════════════════")


# --------------------------------------------------------------------------- #
# Звіт розвідки
# --------------------------------------------------------------------------- #
def _load_discovery_rows():
    rows = []
    if not os.path.exists(config.DISCOVERY_LOG_FILE):
        return rows
    with open(config.DISCOVERY_LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def discovery_report() -> int:
    store = HistoryStore(config.PRICE_HISTORY_FILE)
    rows = _load_discovery_rows()
    if not rows:
        log(f"{config.DISCOVERY_LOG_FILE} порожній або відсутній — немає що аналізувати. "
            f"Спочатку зберіть дані (--interval / --simulate-days) у DISCOVERY_MODE.")
        return 1

    dates = sorted(r["date"] for r in rows if r.get("date"))
    span_days = max(7, _days_between(dates[0], dates[-1]) + 1)
    span_weeks = span_days / 7

    key_by_cat: dict[str, dict] = {}
    for key, q in store.key_query.items():
        d = key_by_cat.setdefault(q, {"epid": 0, "title": 0})
        d["epid" if key.startswith("epid:") else "title"] += 1

    cats: dict[str, dict] = {}
    blank = lambda: {"cand": 0, "tiers": {}, "disc": [], "lc": 0}
    for c in config.CATEGORIES:
        cats.setdefault(c["query"], blank())
    for r in rows:
        c = cats.setdefault(r.get("category", "?"), blank())
        c["cand"] += 1
        if r.get("lower_confidence"):
            c["lc"] += 1
        t = r.get("liquidity_tier", "UNKNOWN")
        c["tiers"][t] = c["tiers"].get(t, 0) + 1
        c["disc"].append(r.get("pct_below", 0.0))

    table = []
    for q, c in cats.items():
        n = c["cand"]
        per_week = n / span_weeks
        ok = c["tiers"].get("OK", 0)
        med = c["tiers"].get("MEDIUM", 0)
        low = c["tiers"].get("LOW", 0)
        unk = c["tiers"].get("UNKNOWN", 0)
        share = (ok + med) / n if n else 0.0
        kb = key_by_cat.get(q, {"epid": 0, "title": 0})
        table.append({
            "q": q,
            "k_epid": kb["epid"], "k_title": kb["title"],
            "cand": n, "lc": c["lc"],
            "per_week": per_week,
            "OK": ok, "MEDIUM": med, "LOW": low, "UNKNOWN": unk,
            "med_disc": statistics.median(c["disc"]) if c["disc"] else 0.0,
            "share": share,
            "score": per_week * share,
        })
    table.sort(key=lambda x: (x["score"], x["cand"]), reverse=True)

    hdr = (f"{'категорія':<32} {'epid':>4} {'ttl':>4} {'канд':>5} {'LC':>3} "
           f"{'/тижд':>6} {'OK':>3} {'MED':>3} {'LOW':>3} {'UNK':>3} "
           f"{'медз%':>6} {'score':>7}")
    lines = [hdr, "-" * len(hdr)]
    for t in table:
        lines.append(
            f"{t['q']:<32} {t['k_epid']:>4} {t['k_title']:>4} {t['cand']:>5} "
            f"{t['lc']:>3} {t['per_week']:>6.2f} {t['OK']:>3} {t['MEDIUM']:>3} "
            f"{t['LOW']:>3} {t['UNKNOWN']:>3} {t['med_disc']:>6.0f} {t['score']:>7.2f}"
        )

    recommended = [t for t in table if t["score"] > 0 and t["share"] >= 0.4][:12]

    log()
    log("═══════════════════ ЗВІТ РОЗВІДКИ (DISCOVERY) ═══════════════════")
    log(f"Період спостережень: {dates[0]} … {dates[-1]}  ({span_days} дн., "
        f"{span_weeks:.1f} тижн.)   рядків у лозі: {len(rows)}")
    log("epid / ttl = ключів товару за epid / за назвою.  LC = кандидатів "
        "нижчої впевненості (ключ за назвою).")
    log("score = (кандидатів/тиждень) × (частка з ліквідністю OK+MEDIUM)")
    log()
    for ln in lines:
        log("  " + ln)
    log()
    if recommended:
        log("Рекомендований звужений CATEGORIES (score>0, частка OK+MED ≥ 0.4):")
        for t in recommended:
            lc = f"  ⚠ {t['lc']}/{t['cand']} кандидатів за назвою" if t["lc"] else ""
            log(f'    {{"query": "{t["q"]}", "min_price": <?>}},   '
                f'# score {t["score"]:.2f}{lc}')
    else:
        log("Жодна категорія поки не має score>0 з достатньою часткою — зберіть "
            "більше даних або послабте пороги ліквідності.")
    log("═══════════════════════════════════════════════════════════════")

    # --- discovery_report.md ---
    md = []
    md.append(f"# Звіт розвідки — {config.EBAY_MARKETPLACE_ID}\n")
    md.append(f"- Період: **{dates[0]} … {dates[-1]}** ({span_days} дн., "
              f"{span_weeks:.1f} тижн.)")
    md.append(f"- Рядків у `{config.DISCOVERY_LOG_FILE}`: **{len(rows)}**")
    md.append(f"- `score` = (кандидатів/тиждень) × (частка кандидатів з "
              f"ліквідністю OK+MEDIUM)")
    md.append(f"- `epid` / `ttl` — ключів товару за epid / за назвою; "
              f"`LC` — кандидатів нижчої впевненості (ключ за назвою)\n")
    md.append("| # | Категорія | epid | ttl | Канд. | LC | /тижд | OK | MED | "
              "LOW | UNK | Медз% | Score |")
    md.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for i, t in enumerate(table, 1):
        md.append(f"| {i} | {t['q']} | {t['k_epid']} | {t['k_title']} | "
                  f"{t['cand']} | {t['lc']} | {t['per_week']:.2f} | {t['OK']} | "
                  f"{t['MEDIUM']} | {t['LOW']} | {t['UNKNOWN']} | "
                  f"{t['med_disc']:.0f}% | **{t['score']:.2f}** |")
    md.append("")
    if recommended:
        md.append("## Рекомендований звужений `CATEGORIES`\n")
        md.append("Категорії зі `score > 0` і часткою OK+MEDIUM ≥ 0.4 — "
                  "перенесіть у `config.py` (підставте `min_price`) і поставте "
                  "`DISCOVERY_MODE=false`. `⚠` — групування переважно за назвою:\n")
        md.append("```python")
        md.append("CATEGORIES = [")
        for t in recommended:
            warn = f"  # ⚠ {t['lc']}/{t['cand']} за назвою" if t["lc"] else ""
            md.append(f'    {{"query": "{t["q"]}", "min_price": <?>}},'
                      f'   # score {t["score"]:.2f}{warn}')
        md.append("]")
        md.append("```")
    with open(config.DISCOVERY_REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    log(f"Звіт збережено: {config.DISCOVERY_REPORT_FILE}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_client():
    return EbayClient() if config.EBAY_CREDS_OK else None


def describe_mode() -> str:
    ebay = "реальний Browse API" if config.EBAY_CREDS_OK else "MOCK-дані (dry-run)"
    tg = "реальна відправка" if config.TELEGRAM_CREDS_OK else "[DRY RUN] у консоль"
    return f"eBay: {ebay} | Telegram: {tg}"


def main() -> int:
    global VERBOSE

    p = argparse.ArgumentParser(
        description="eBay (EBAY_DE) price+liquidity anomaly monitor → Telegram"
    )
    p.add_argument("--once", action="store_true", help="один прогін і вихід")
    p.add_argument("--interval", type=int, nargs="?", const=0, metavar="SECONDS",
                   help=f"циклічний режим; без значення = POLL_INTERVAL_SECONDS "
                        f"({config.POLL_INTERVAL_SECONDS})")
    p.add_argument("--simulate-days", type=int, metavar="N", dest="simulate_days",
                   help="прожити N віртуальних днів на mock (ліквідність: ~21+)")
    p.add_argument("--discovery-report", action="store_true", dest="discovery_report",
                   help="проаналізувати discovery_log.jsonl → консоль + discovery_report.md")
    p.add_argument("--reset-history", action="store_true",
                   help="очистити price_history.json (+ discovery-файли) перед запуском")
    p.add_argument("--verbose", action="store_true",
                   help="повний лог кожного віртуального дня в --simulate-days")
    args = p.parse_args()

    VERBOSE = args.verbose
    if args.interval == 0:                       # прапорець без значення
        args.interval = config.POLL_INTERVAL_SECONDS

    actions = (args.once, args.interval is not None, args.simulate_days,
               args.discovery_report)
    if not any(actions):
        p.error("вкажіть --once, --interval [SECONDS], --simulate-days N "
                "або --discovery-report")
    if args.interval is not None and args.interval < 1:
        p.error("--interval має бути додатнім числом секунд")
    if args.simulate_days is not None and args.simulate_days < 1:
        p.error("--simulate-days має бути ≥ 1")

    if args.reset_history:
        for path in (config.PRICE_HISTORY_FILE, config.DISCOVERY_LOG_FILE,
                     config.DISCOVERY_REPORT_FILE):
            if os.path.exists(path):
                os.remove(path)
                log(f"{path} очищено")

    if args.discovery_report:
        return discovery_report()

    mode = ("РОЗВІДКА → discovery_log.jsonl" if config.DISCOVERY_MODE
            else "БОЙОВИЙ → Telegram")
    log(f"Маркетплейс: {config.EBAY_MARKETPLACE_ID} ({config.CURRENCY}) | "
        f"{describe_mode()} | режим: {mode}")
    store = HistoryStore(config.PRICE_HISTORY_FILE)
    client = build_client()
    liq = build_liquidity_provider(store, client)
    cats = [dict(c) for c in config.CATEGORIES]
    log(f"Категорій: {len(cats)} | ключів товару в базі: {len(store.history)} | "
        f"відстежується лістингів: {sum(len(b) for b in store.listings.values())} | "
        f"надісланих сповіщень: {len(store.alerted)} | "
        f"кандидатів у розвідці: {len(store.discovery_seen)}")

    if args.simulate_days:
        now = datetime.now(timezone.utc)
        agg = simulate(cats, store, liq, args.simulate_days)
        print_summary(store, liq, now, agg)
        if config.DISCOVERY_MODE:
            log("Далі: `python main.py --discovery-report`")
        return 0

    force_mock = not config.EBAY_CREDS_OK
    if args.once:
        now = datetime.now(timezone.utc)
        stats = run_pass(client, cats, store, liq, now, force_mock=force_mock)
        print_summary(store, liq, now, stats)
        if force_mock:
            log("Порада: прожити період розвідки на mock-даних — "
                "`python main.py --simulate-days 21 --reset-history`, "
                "потім `python main.py --discovery-report`")
        return 0

    log(f"Циклічний режим, інтервал {args.interval}s. Ctrl+C — вихід.")
    while True:
        try:
            now = datetime.now(timezone.utc)
            stats = run_pass(client, cats, store, liq, now, force_mock=force_mock)
            print_summary(store, liq, now, stats)
            log(f"Наступний прогін через {args.interval}s.")
            time.sleep(args.interval)
        except KeyboardInterrupt:
            log("Зупинено користувачем.")
            return 0
        except Exception as exc:  # noqa: BLE001
            log(f"Неочікувана помилка прогону: {exc}. Продовжую після паузи.")
            time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
