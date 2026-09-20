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
import identity
import quality

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


# Лічильник викликів Browse API (квота eBay 5000/добу спільна для бота, ноутбука і зондів).
# Рахуємо кожну спробу HTTP, включно з повторами після 429/5xx.
API_USAGE = {"browse": 0, "429": 0, "5xx": 0}


def _count_call(url: str, kind: str = "browse") -> None:
    if "/buy/browse/" in url:
        API_USAGE[kind] += 1


def _request_with_backoff(method: str, url: str, **kwargs):
    delay = config.BACKOFF_BASE
    last_err = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        _count_call(url)
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
            _count_call(url, "429")
            log(f"  429 rate limit — пауза {retry_after:.0f}s "
                f"({attempt}/{config.MAX_RETRIES})")
            time.sleep(retry_after)
            delay *= config.BACKOFF_BASE
            continue

        if resp.status_code >= 500:
            last_err = RuntimeError(f"{resp.status_code} {resp.reason}")
            _count_call(url, "5xx")
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

    def search_ex(self, query: str, min_price, sort: str | None = None,
                  country: str | None = None):
        """(items, total). country → itemLocationCountry (одна країна на виклик)."""
        flt = config.search_filter(min_price)
        if country:
            flt += f",itemLocationCountry:{country}"
        params = {"q": query, "filter": flt, "limit": config.SEARCH_LIMIT}
        if sort:
            params["sort"] = sort
        data = _request_with_backoff(
            "GET", config.EBAY_BROWSE_SEARCH_URL, headers=self._headers(), params=params
        )
        return (data.get("itemSummaries") or []), int(data.get("total") or 0)

    def search(self, query: str, min_price, sort: str | None = None) -> list:
        return self.search_ex(query, min_price, sort)[0]


def fetch_summaries(client, cat, now, day=0, total_days=1, force_mock=False):
    """Знімок ринку ПОКУПЦЯ (config.MARKET_COUNTRIES) для категорії.

    Для кожної країни: запит A (best_match); якщо лотів більше за сторінку
    (total > 200) — додатково B (sort=price, найдешевші). Глобальний sort=price
    давав у вибірці до 87% лотів із Великої Британії (найдешевші) і майже не бачив
    німецьких; тепер запит іде по країні. Повертає
    (merged_list, n_a, n_b, only_b, complete): complete=True, коли КОЖНА країна
    отримана повністю (total <= отримано) — лише тоді зникнення лота можна
    вважати продажем/зняттям."""
    query = cat["query"]
    mp = cat.get("min_price", config.MIN_PRICE)
    if force_mock or not config.EBAY_CREDS_OK or client is None:
        from mock_data import mock_search

        a = mock_search(query, now, day, total_days, sort=None, min_price=mp)
        b = mock_search(query, now, day, total_days, sort=config.SEARCH_SORT_B, min_price=mp)
        a_ids = {i.get("itemId") for i in a}
        merged: dict[str, dict] = {}
        for it in list(a) + list(b):
            iid = it.get("itemId")
            if iid and iid not in merged:
                merged[iid] = it
        only_b = sum(1 for iid in merged if iid not in a_ids)
        return list(merged.values()), len(a), len(b), only_b, True

    merged = {}
    n_a = n_b = only_b = 0
    complete = True
    for cc in config.MARKET_COUNTRIES:
        a, total = client.search_ex(query, mp, None, cc)
        n_a += len(a)
        got = {}
        for it in a:
            iid = it.get("itemId")
            if iid:
                got.setdefault(iid, it)
        if total > len(a):
            b, _ = client.search_ex(query, mp, config.SEARCH_SORT_B, cc)
            n_b += len(b)
            for it in b:
                iid = it.get("itemId")
                if iid and iid not in got:
                    got[iid] = it
                    only_b += 1
        if total > len(got):
            complete = False
        for iid, it in got.items():
            merged.setdefault(iid, it)
    return list(merged.values()), n_a, n_b, only_b, complete


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
        self.seeded: set[str] = set()           # категорії з початковим знімком
        self.legacy_v1 = None                   # копія стану до переходу на ідентифікацію v2
        self.api_usage: dict[str, int] = {}     # {дата UTC: викликів Browse API ботом}
        self.last_run_api = (0, 0, 0)           # (виклики, 429, 5xx) останнього прогону
        self.migrated = False
        self._fam = None                        # кеш родин ключів (на прогін)
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
        self.alerted = set(data.get("alerted_item_ids", []) or [])
        self.discovery_seen = set(data.get("discovery_seen", []) or [])
        self.seeded = set(data.get("seeded", []) or [])
        self.legacy_v1 = data.get("legacy_v1")
        self.api_usage = {str(k): int(v) for k, v in (data.get("api_usage") or {}).items()}
        if int(data.get("schema", 1)) < config.STATE_SCHEMA:
            # Перехід на ідентифікацію v2: старі ключі змішували різні товари, а
            # знімок ринку був неповним → «медіани» і «продажі» недостовірні.
            # Стартуємо з чистої історії, стару зберігаємо як legacy_v1.
            self.legacy_v1 = {k: data.get(k) for k in
                              ("history", "listings", "key_map", "key_query", "key_conf")}
            self.migrated = True
            return
        self.history = data.get("history", {}) or {}
        self.listings = data.get("listings", {}) or {}
        self.key_map = data.get("key_map", {}) or {}
        self.key_query = data.get("key_query", {}) or {}
        self.key_conf = data.get("key_conf", {}) or {}
        for key, points in self.history.items():
            self._pindex[key] = {p["item_id"] for p in points}

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "schema": config.STATE_SCHEMA,
                "history": self.history,
                "listings": self.listings,
                "alerted_item_ids": sorted(self.alerted),
                "discovery_seen": sorted(self.discovery_seen),
                "seeded": sorted(self.seeded),
                "key_map": self.key_map,
                "key_query": self.key_query,
                "key_conf": self.key_conf,
                "legacy_v1": self.legacy_v1,
                "api_usage": self.api_usage,
            }, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, self.path)

    def record_api_usage(self, now, calls: int, keep_days: int = 14) -> None:
        day = now.astimezone(timezone.utc).date().isoformat()
        self.api_usage[day] = self.api_usage.get(day, 0) + int(calls)
        cutoff = (now - timedelta(days=keep_days)).astimezone(timezone.utc).date().isoformat()
        self.api_usage = {d: n for d, n in self.api_usage.items() if d >= cutoff}

    # --- цінова історія ---
    def points_in_window(self, epid, now, days) -> list:
        cutoff = (now - timedelta(days=days)).date().isoformat()
        return [p for p in self.history.get(epid, []) if p["date"] >= cutoff]

    def has_point(self, epid, item_id) -> bool:
        return item_id in self._pindex.get(epid, ())

    def record_point(self, epid, item_id, price_total, currency, now,
                     seller=None, country=None) -> bool:
        if self.has_point(epid, item_id):
            return False
        self.history.setdefault(epid, []).append({
            "date": now.date().isoformat(),
            "price_total": round(price_total, 2),
            "item_id": item_id,
            "currency": currency,
            "seller": seller,
            "country": country,
        })
        self._pindex.setdefault(epid, set()).add(item_id)
        return True

    def remove_point(self, epid, item_id) -> None:
        pts = [p for p in self.history.get(epid, []) if p["item_id"] != item_id]
        if pts:
            self.history[epid] = pts
        else:
            self.history.pop(epid, None)
        self._pindex.get(epid, set()).discard(item_id)

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

    def sweep_gone(self, now, complete=None) -> int:
        """Позначає зниклі лістинги. complete = {query: bool}: зникнення рахуємо
        лише для категорій, знімок яких у цьому прогоні був ПОВНИМ (інакше лот міг
        просто не потрапити у видачу → фальшивий «продаж»)."""
        today = now.date().isoformat()
        n = 0
        for key, book in self.listings.items():
            if complete is not None and not complete.get(self.key_query.get(key)):
                continue
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

    @staticmethod
    def family_of(key: str) -> str:
        """Родина ключа v2 = «категорія|тип|мова» (без назви сету/моделі)."""
        return "|".join(key.split("|")[:3])

    def family_book(self, key: str) -> dict:
        """Обʼєднаний життєвий цикл усіх лістингів родини (для ліквідності, коли
        у самого ключа замало завершених циклів)."""
        if self._fam is None:
            fam = {}
            for k in self.listings:
                fam.setdefault(self.family_of(k), []).append(k)
            self._fam = fam
        merged = {}
        for k in self._fam.get(self.family_of(key), []):
            merged.update(self.listings.get(k, {}))
        return merged


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

    def assess(self, epid: str, query: str, now: datetime,
               sold_url: str | None = None) -> LiquidityReport:
        url = sold_url or sold_search_url(query)
        book = self.store.listings.get(epid, {})
        r = self._assess_book(book, url, now)
        if r.tier == "UNKNOWN" and config.IDENTITY_V2 and "|" in epid:
            fam = self.store.family_book(epid)
            if fam and len(fam) > len(book):
                r2 = self._assess_book(fam, url, now)
                if r2.tier != "UNKNOWN":
                    r2.note = (f"рівень РОДИНИ {self.store.family_of(epid)} "
                               f"(у ключа замало даних): {r2.note}")
                    return r2
        return r

    def _assess_book(self, book: dict, url: str, now: datetime) -> LiquidityReport:
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

    def assess(self, epid: str, query: str, now: datetime,
               sold_url: str | None = None) -> LiquidityReport:
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
def _age_txt(created: str | None) -> str:
    try:
        dt = datetime.fromisoformat((created or "").replace("Z", "+00:00"))
        hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except ValueError:
        return "невідомо"
    hours = max(0.0, hours)
    return f"{hours:.0f} год тому" if hours < 48 else f"{hours / 24:.0f} дн. тому"


def format_message(a: dict) -> str:
    item = a["item"]
    v = a["v"]
    r: LiquidityReport = a["liq"]
    idn = a.get("ident")
    cur = a["currency"]
    seller = item.get("seller") or {}
    loc = (item.get("itemLocation") or {}).get("country", "?")
    offer = " · Best Offer (можна торгуватись)" if "BEST_OFFER" in (item.get("buyingOptions") or []) else ""
    what = ""
    if idn is not None:
        bits = [f"тип {idn.ptype}", f"мова {idn.lang}"]
        if idn.packs:
            bits.append(f"{idn.packs} паків")
        if idn.code:
            bits.append(f"код {idn.code}")
        what = "Бот вважає товаром: " + ", ".join(bits) + "\n"
    return (
        f"🔻 ЗНАХІДКА · {a['query']}\n"
        f"{item.get('title', '—')}\n\n"
        f"Ціна з доставкою: {a['price_total']:.2f} {cur}\n"
        f"Еталон (ІНШІ лоти цього товару, ринок {'+'.join(config.MARKET_COUNTRIES)}): "
        f"медіана {v.median:.2f} {cur} — {v.n_comps} лот. від {v.n_sellers} продавців, "
        f"типовий діапазон {v.q1:.0f}–{v.q3:.0f}\n"
        f"Нижче медіани на {(1 - v.ratio) * 100:.0f}% · економія {v.saving:.2f} {cur} · "
        f"найдешевший інший лот: {v.lowest_other:.2f}\n"
        f"{what}\n"
        f"Ліквідність: {r.tier} — {r.headline}\n"
        f"  {r.note}\n\n"
        f"Продавець: {seller.get('username', '—')} (відгуків {seller.get('feedbackScore')}, "
        f"{seller.get('feedbackPercentage')}%) · країна {loc} · "
        f"лот створено {_age_txt(item.get('itemCreationDate'))}{offer}\n\n"
        f"ПЕРЕВІР ПЕРЕД КУПІВЛЕЮ:\n"
        f"  1) у назві/фото той самий сет, мова, видання, запечатано\n"
        f"  2) реальні продажі цього товару: {r.sold_url}\n"
        f"  3) немає «ohne/leer/nur Box», відгуки продавця\n\n"
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
def _bump(d: dict, k: str, n: int = 1) -> None:
    d[k] = d.get(k, 0) + n


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
        "skipped_flags": {},
        "skipped_ident": {},
        "verdicts": {"ALERT": 0, "HOLD": 0},
        "hold_reasons": {},
        "shadow_rows": 0,
        "complete": {},
        "seeded_now": [],
    }


def merge_stats(dst: dict, src: dict) -> None:
    dst["keys_seen"] |= src["keys_seen"]
    dst["keys_lowconf"] |= src["keys_lowconf"]
    for k in ("skipped_seller", "skipped_blocklist", "no_key", "only_b",
              "new_listings", "gone", "relisted_now", "shadow_rows"):
        dst[k] += src[k]
    dst["alerts"].extend(src["alerts"])
    dst["discovery"].extend(src["discovery"])
    for t, v in src["by_tier"].items():
        dst["by_tier"][t] += v
    for name in ("skipped_flags", "skipped_ident", "verdicts", "hold_reasons"):
        for k, v in src[name].items():
            _bump(dst[name], k, v)
    dst["complete"].update(src["complete"])
    dst["seeded_now"].extend(src["seeded_now"])


def append_discovery(record: dict) -> None:
    with open(config.DISCOVERY_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _seller_name(item: dict) -> str:
    return (item.get("seller") or {}).get("username") or "?"


def seller_alert_ok(item: dict) -> bool:
    s = item.get("seller") or {}
    score = int(_to_float(s.get("feedbackScore")) or 0)
    pct = _to_float(s.get("feedbackPercentage")) or 0.0
    return score >= config.ALERT_MIN_SELLER_SCORE and pct >= config.ALERT_MIN_SELLER_PCT


def _sold_url(item: dict, query: str) -> str:
    """Пошук ПРОДАНИХ саме цього товару (за назвою лота), а не всієї категорії."""
    words = re.findall(r"[\w'’-]+", item.get("title") or "")[:9]
    return sold_search_url(" ".join(words) if words else query)


def process_category(cat, summaries, store, liq, now, stats, quiet, *, strict=True) -> None:
    query = cat["query"]
    min_price = cat.get("min_price", config.MIN_PRICE)
    use_v2 = config.IDENTITY_V2 and strict
    store._fam = None                    # кеш родин ліквідності: знімок міг змінитись

    by_key: dict[str, list] = {}
    no_key = 0
    for item in summaries:
        if use_v2:
            fl = quality.listing_flags(item)
            if fl:
                for f in fl:
                    _bump(stats["skipped_flags"], f.split(":")[0])
                continue
            idn = identity.describe(item.get("title"), cat)
            if idn.exclude:
                for e in idn.exclude:
                    _bump(stats["skipped_ident"], e.split(":")[0])
                continue
            key, low_conf = idn.key, True
        else:
            key, low_conf = product_key(item)
            if key is None:
                no_key += 1
                continue
        by_key.setdefault(key, []).append(item)
        store.key_conf[key] = low_conf

    stats["no_key"] += no_key
    if not by_key:
        vlog(f"[{query}] жодного придатного ключа — пропускаю", quiet)
        return

    canonical = max(by_key, key=lambda k: len(by_key[k]))
    cat["key"] = canonical
    store.key_map[query] = canonical
    vlog(f"[{query}] ключів у видачі: {len(by_key)}; канонічний {canonical} "
         f"({len(by_key[canonical])} лот.)", quiet)

    # Перший знімок категорії лише наповнює історію (усі лоти «нові» для нас, але не
    # нові на ринку) — без оцінки, щоб не було вибуху фальшивих знахідок.
    seeding = use_v2 and query not in store.seeded
    for key, items in by_key.items():
        store.key_query.setdefault(key, query)
        _process_key(query, key, items, store, liq, now, stats, quiet, min_price,
                     cat, seeding, use_v2)
    if seeding:
        store.seeded.add(query)
        stats["seeded_now"].append(query)


def _age_days(created: str | None, now: datetime):
    try:
        dt = datetime.fromisoformat((created or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (now - dt).total_seconds() / 86400)


def _judge(item, tp, key, query, cat, ref, store, liq, now, stats, quiet, use_v2):
    """Повний вердикт для нового лота. Повертає dict; {'deliver_failed': True},
    якщо сповіщення не вдалося доставити (тоді лот лишається «новим» до наступного
    прогону)."""
    iid = item["itemId"]
    comps = [(p, sel, i) for i, (p, sel) in ref.items() if i != iid]
    v = quality.assess(tp, comps, _seller_name(item))
    idn = identity.describe(item.get("title"), cat) if use_v2 else None

    reasons = list(v.reasons)
    report = None
    age = _age_days(item.get("itemCreationDate"), now)
    if v.ok:
        if age is None:
            reasons.append("age-unknown")
        elif age > config.ALERT_MAX_AGE_DAYS:
            reasons.append("stale-listing")
        try:
            report = liq.assess(key, query, now, sold_url=_sold_url(item, query))
        except Exception as exc:  # noqa: BLE001
            log(f"  ⚠ оцінка ліквідності не вдалась для {key}: {exc}")
            report = LiquidityReport("UNKNOWN", "невідомо", None, None, 0, 0, 0, 0,
                                     f"помилка провайдера: {exc}", _sold_url(item, query))
        stats["by_tier"][report.tier] = stats["by_tier"].get(report.tier, 0) + 1
        if report.tier not in config.ALERT_TIERS:
            reasons.append(f"liq-{report.tier.lower()}")
        if not seller_alert_ok(item):
            reasons.append("seller")
        if idn is not None and not idn.spec_ok:
            reasons.append("spec-missing")             # у назві немає варіанта (MS/UC/Stereo…)
    verdict = "ALERT" if (v.ok and not reasons) else "HOLD"
    stats["verdicts"][verdict] += 1
    for r_ in reasons:
        _bump(stats["hold_reasons"], r_)

    near_miss = (v.median is not None and v.ratio is not None
                 and v.ratio <= config.ANOMALY_THRESHOLD * 1.25)
    if v.ok or near_miss:
        s = item.get("seller") or {}
        append_discovery({
            "ts": now.isoformat(), "date": now.date().isoformat(), "kind": "shadow",
            "category": query, "product_key": key, "item_id": iid,
            "verdict": verdict, "reasons": reasons,
            "price_total": tp, "currency": item_currency(item),
            "median": None if v.median is None else round(v.median, 2),
            "n_comps": v.n_comps, "n_sellers": v.n_sellers,
            "dispersion": None if v.dispersion is None else round(v.dispersion, 3),
            "ratio": None if v.ratio is None else round(v.ratio, 3),
            "saving": None if v.saving is None else round(v.saving, 2),
            "near": v.near, "near_own": v.near_own, "lowest_other": v.lowest_other,
            "liquidity_tier": None if report is None else report.tier,
            "seller": s.get("username"), "seller_score": s.get("feedbackScore"),
            "country": (item.get("itemLocation") or {}).get("country"),
            "created": item.get("itemCreationDate"),
            "age_days": None if age is None else round(age, 1),
            "title": (item.get("title") or "")[:110],
            "url": item.get("itemWebUrl"),
        })
        stats["shadow_rows"] += 1

    if verdict != "ALERT":
        return {}

    a = {"query": query, "key": key, "item": item, "price_total": tp,
         "currency": item_currency(item), "v": v, "liq": report,
         "ident": idn, "liq_provider": liq.label}
    if config.ALERT_MODE == "live":
        try:
            deliver(a)
        except Exception as exc:  # noqa: BLE001
            log(f"  ✗ доставка сповіщення не вдалась ({iid}): {exc}")
            return {"deliver_failed": True}
        store.alerted.add(iid)
        stats["alerts"].append(a)
        log(f"  🔔 ЗНАХІДКА [{key}] {query}: {tp:.2f} vs медіана {v.median:.2f} "
            f"(-{(1 - v.ratio) * 100:.0f}%, еталон {v.n_comps}/{v.n_sellers} прод.) "
            f"| {report.tier}")
    else:
        store.discovery_seen.add(iid)
        stats["discovery"].append(a)
        vlog(f"  🔎 SHADOW ALERT [{key}] {query}: {tp:.2f} vs медіана {v.median:.2f} "
             f"(-{(1 - v.ratio) * 100:.0f}%) | {report.tier}", quiet)
    return {}


def _process_key(query, key, items, store, liq, now, stats, quiet, min_price,
                 cat, seeding, use_v2):
    stats["keys_seen"].add(key)
    if store.key_conf.get(key):
        stats["keys_lowconf"].add(key)
    tag = key

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

    if not use_v2:
        _process_key_legacy(query, key, valid, store, liq, now, stats, quiet)
        return

    # Еталон = історія ключа у вікні + актуальні ціни поточного знімка (кандидат
    # виключається на етапі оцінки).
    window = store.points_in_window(key, now, config.HISTORY_WINDOW_DAYS)
    ref = {p["item_id"]: (p["price_total"], p.get("seller") or "?") for p in window}
    for it, tp in valid:
        ref[it["itemId"]] = (tp, _seller_name(it))
    seen_set = store.discovery_seen if config.ALERT_MODE == "shadow" else store.alerted

    for item, tp in valid:
        item_id = item["itemId"]
        is_new = not store.has_point(key, item_id)
        res = {}
        if is_new and not seeding and item_id not in seen_set:
            res = _judge(item, tp, key, query, cat, ref, store, liq, now, stats,
                         quiet, use_v2)
        if res.get("deliver_failed"):
            continue                                   # лишаємо «новим» → повтор наступного прогону
        store.record_point(key, item_id, tp, item_currency(item), now,
                           seller=_seller_name(item),
                           country=(item.get("itemLocation") or {}).get("country"))


def _process_key_legacy(query, key, valid, store, liq, now, stats, quiet):
    """Старий шлях (IDENTITY_V2=false / mock): медіана вікна, поріг ANOMALY_THRESHOLD."""
    window = store.points_in_window(key, now, config.HISTORY_WINDOW_DAYS)
    have = len(window)
    if have < config.MIN_HISTORY_POINTS:
        for it, tp in valid:
            store.record_point(key, it["itemId"], tp, item_currency(it), now,
                               seller=_seller_name(it))
        return
    hist_median = statistics.median(p["price_total"] for p in window)
    seen_set = store.discovery_seen if config.ALERT_MODE == "shadow" else store.alerted
    for item, tp in valid:
        item_id = item["itemId"]
        is_new = not store.has_point(key, item_id)
        store.record_point(key, item_id, tp, item_currency(item), now,
                           seller=_seller_name(item))
        if not is_new or item_id in seen_set:
            continue
        ratio = tp / hist_median if hist_median else 1.0
        if ratio >= config.ANOMALY_THRESHOLD:
            continue
        stats["verdicts"]["HOLD"] += 1
        _bump(stats["hold_reasons"], "legacy-mode")


# --------------------------------------------------------------------------- #
# Прогони
# --------------------------------------------------------------------------- #
def run_pass(client, cats, store, liq, now, *, day=0, total_days=1,
             force_mock=False, quiet=False) -> dict:
    stats = new_stats()
    store._fam = None
    api0 = (API_USAGE["browse"], API_USAGE["429"], API_USAGE["5xx"])
    for cat in cats:
        try:
            summaries, n_a, n_b, only_b, complete = fetch_summaries(
                client, cat, now, day, total_days, force_mock)
            stats["only_b"] += only_b
        except Exception as exc:  # noqa: BLE001
            log(f"[{cat['query']}] запит не вдався: {exc}")
            continue
        try:
            process_category(cat, summaries, store, liq, now, stats, quiet,
                             strict=not force_mock)
            stats["complete"][cat["query"]] = bool(complete)
            if only_b:
                vlog(f"[{cat['query']}] запит B (price-asc) додав {only_b} лот(ів)", quiet)
        except Exception as exc:  # noqa: BLE001
            log(f"[{cat['query']}] обробка не вдалась: {exc}")
            continue

    stats["gone"] = store.sweep_gone(now, None if force_mock else stats["complete"])
    stats["relisted_now"] = store.match_relists(quiet)
    store.last_run_api = tuple(API_USAGE[k] - a for k, a in zip(("browse", "429", "5xx"), api0))
    if not force_mock:
        store.record_api_usage(now, store.last_run_api[0])
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
    n_epid = sum(1 for k in store.history if k.startswith("epid:"))
    n_title = len(store.history) - n_epid
    W = config.HISTORY_WINDOW_DAYS
    ready = 0                                   # ключі, для яких можлива оцінка
    for key in store.history:
        pts = store.points_in_window(key, now, W)
        if (len(pts) >= config.QUALITY_MIN_COMPS + 1 and
                len({p.get("seller") or "?" for p in pts}) >= config.QUALITY_MIN_SELLERS + 1):
            ready += 1

    log()
    log("═════════════════════════ ПІДСУМОК ═════════════════════════")
    log(f"Маркетплейс: {config.EBAY_MARKETPLACE_ID}   ринок покупця: "
        f"{'+'.join(config.MARKET_COUNTRIES)}   ALERT_MODE={config.ALERT_MODE}")
    log(f"Унікальних ключів товару: {len(store.history)}  "
        f"(epid: {n_epid}, за назвою: {n_title})   "
        f"готові: {ready}   накопичують: {len(store.history) - ready}")
    log(f"Запит B (price-asc) додав лотів: {agg['only_b']}")
    if store.migrated:
        log("Стан переведено на схему v2: стару історію збережено в legacy_v1, "
            "історія й ліквідність рахуються заново (повний знімок ринку).")
    if agg["seeded_now"]:
        log(f"Початковий знімок (без оцінки) зроблено для: {len(agg['seeded_now'])} категорій")

    tiers_now = {}
    for key in store.listings:
        try:
            t = liq.assess(key, store.query_for(key), now).tier
        except Exception:  # noqa: BLE001
            t = "ERR"
        tiers_now[t] = tiers_now.get(t, 0) + 1
    log(f"Ліквідність по відстежених ключах: {dict(sorted(tiers_now.items()))}")

    log()
    log(f"Відсів лотів ДО аналізу — поля eBay: {agg['skipped_flags']}")
    log(f"Відсів лотів ДО аналізу — назва/ідентичність: {agg['skipped_ident']}")
    log(f"Репутація продавця: {agg['skipped_seller']}   стоп-лист стану: "
        f"{agg['skipped_blocklist']}   без ключа: {agg['no_key']}")
    n_c = sum(1 for v in agg["complete"].values() if v)
    log(f"Повний знімок ринку: {n_c}/{len(agg['complete'])} категорій "
        f"(зникнення рахуються продажем лише для повних)")
    log(f"Життєвий цикл: relist-подій (виключено з velocity): "
        f"{sum(1 for b in store.listings.values() for e in b.values() if e['status'] == 'relisted')}")

    calls, n429, n5xx = store.last_run_api
    today = now.astimezone(timezone.utc).date().isoformat()
    used = store.api_usage.get(today, 0)
    pct = used / 5000 * 100
    warn = "  ⚠ близько до ліміту 5000/добу!" if pct >= 70 else ""
    log(f"Виклики Browse API: цей прогін {calls} (429: {n429}, 5xx: {n5xx}); "
        f"бот сьогодні (UTC) ≈ {used} із 5000 ({pct:.0f}%){warn}")
    if store.api_usage:
        log("  за днями (UTC): " + ", ".join(f"{d[5:]}={n}" for d, n in sorted(store.api_usage.items())[-7:])
            + "   [лише виклики бота; ліміт eBay скидається ~07:00 UTC, дослідження не входять]")

    log()
    vd = agg["verdicts"]
    log(f"Вердикти для НОВИХ лотів: ALERT {vd['ALERT']}   HOLD {vd['HOLD']}   "
        f"(рядків у shadow-лозі: {agg['shadow_rows']})")
    if agg["hold_reasons"]:
        log(f"Причини HOLD: {dict(sorted(agg['hold_reasons'].items(), key=lambda x: -x[1]))}")
    if config.ALERT_MODE == "shadow":
        log("РЕЖИМ SHADOW — Telegram-сповіщення вимкнені; знахідки → discovery_log.jsonl (kind=shadow).")
        for a in agg["discovery"]:
            v = a["v"]
            log(f"  🔎 {a['query']} [{a['key']}]  {a['price_total']:.2f} {a['currency']} "
                f"vs медіана {v.median:.2f} (-{(1 - v.ratio) * 100:.0f}%, еталон "
                f"{v.n_comps}/{v.n_sellers} прод.)  {a['liq'].tier}")
            log(f"       {a['item'].get('title', '')}")
    else:
        log(f"СПОВІЩЕНЬ НАДІСЛАНО: {len(agg['alerts'])}")
        for a in agg["alerts"]:
            v = a["v"]
            log(f"  🔔 {a['query']} [{a['key']}]  {a['price_total']:.2f} {a['currency']}  "
                f"vs медіана {v.median:.2f}  (-{(1 - v.ratio) * 100:.0f}%)")
            log(f"       {a['item'].get('title', '')}")
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
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "shadow":
                continue                         # shadow-вердикти — окремий аналіз (analyze.py)
            rows.append(r)
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

    mode = ("SHADOW → discovery_log.jsonl (без Telegram)" if config.ALERT_MODE == "shadow"
            else "LIVE → Telegram")
    log(f"Маркетплейс: {config.EBAY_MARKETPLACE_ID} ({config.CURRENCY}) | "
        f"{describe_mode()} | режим: {mode}")
    store = HistoryStore(config.PRICE_HISTORY_FILE)
    client = build_client()
    liq = build_liquidity_provider(store, client)
    cats = [dict(c) for c in config.CATEGORIES]
    log(f"Категорій: {len(cats)} | ключів товару в базі: {len(store.history)} | "
        f"відстежується лістингів: {sum(len(b) for b in store.listings.values())} | "
        f"надісланих сповіщень: {len(store.alerted)} | "
        f"оцінених у shadow: {len(store.discovery_seen)}")

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
