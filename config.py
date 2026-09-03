"""Конфігурація бота моніторингу цін eBay (німецький ринок).

Секрети читаються зі змінних середовища (див. .env.example).
Якщо ключів немає — бот працює в dry-run режимі на mock-даних.
"""

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv не обов'язковий
    pass


def _get(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


# --- eBay --------------------------------------------------------------------
EBAY_APP_ID = _get("EBAY_APP_ID")
EBAY_CERT_ID = _get("EBAY_CERT_ID")

# Маркетплейс. За замовчуванням німецький eBay (валюта EUR).
# Передається в кожен виклик Browse API як заголовок X-EBAY-C-MARKETPLACE-ID.
# Легко перемкнути: EBAY_US, EBAY_GB, EBAY_AT, EBAY_FR, ...
EBAY_MARKETPLACE_ID = _get("EBAY_MARKETPLACE_ID", "EBAY_DE")
CURRENCY = _get("CURRENCY", "EUR")  # лише для форматування, коли лот не має валюти

EBAY_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
EBAY_BROWSE_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

# --- Telegram --------------------------------------------------------------
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID")
TELEGRAM_API_BASE = "https://api.telegram.org"

# --- Що шукаємо ------------------------------------------------------------
# ШИРОКИЙ список під "режим розвідки" (DISCOVERY_MODE): напрямки з підтвердженим
# високим sell-through rate, де низька ціна НЕ пояснюється станом товару.
#
# min_price — очікувана НИЖНЯ межа реальної ціни ЦЬОГО товару (EUR), підібрана
# під кожну категорію окремо на живих даних. Іде у filter=price:[min_price..] в
# ОБОХ запитах циклу. Без неї sort=price тягне у вибірку аксесуарний шум
# (поштучні деталі, захисні стекла), і товар не потрапляє в перші 50.
# Значення різні: LEGO/iPhone — сотні євро, фільтри/еспандери/йога — десятки.
#
# Список пройшов перевірку структурної придатності (див. README «Придатність
# категорії»): прибрані запити, де ОДИН product_key бачиться раз-два за весь
# період (загальні описові запити на десятки тисяч різних товарів —
# "Vinyl ... versiegelt", "Playmobil Set", "Ravensburger Spiel"/"Puzzle",
# "LEGO City/Duplo Set", "Trekkingrucksack/-stöcke") або надто тонкі ринки з
# < ~50 лотів усього ("Dior Sauvage neu OVP" 8, "Instant Pot Dichtungsring" 12,
# "Protein/Kreatin Pulver" 15/21, "Stirnlampe LED" 33, "Shimano Schaltgruppe" 34,
# "Pokemon 151 Display" 19, "Vitamix Behälter" 68). Для них MIN_HISTORY_POINTS
# практично не набирається → вічний UNKNOWN без корисного сигналу.
CATEGORIES = [
    # --- Пилососи / запчастини (витратні деталі, компактна пересилка) ---
    {"query": "Dyson Filter Ersatz",            "min_price": 8},
    {"query": "Dyson Zubehör Set neu",          "min_price": 18},
    {"query": "Roomba Bürsten Ersatzteile",     "min_price": 9},
    {"query": "Shark Staubsauger Filter",       "min_price": 8},
    # --- Дрібна кухонна техніка ---
    {"query": "Ninja Blender neu",              "min_price": 45},
    {"query": "Vorwerk Thermomix TM6 Zubehör",  "min_price": 15},
    {"query": "Kärcher Fenstersauger",          "min_price": 25},
    # --- Сад / фітнес (компактне) ---
    {"query": "Gardena Gartengeräte Set neu",   "min_price": 35},
    {"query": "Widerstandsband Set Fitness",    "min_price": 8},
    {"query": "Yogamatte Set neu",              "min_price": 14},
    # --- Похід (компактне спорядження) ---
    {"query": "Camping Kochgeschirr Set neu",   "min_price": 12},
    # --- Колекційне (запечатане, конкретний товарний ряд) ---
    {"query": "Pokemon Booster Box versiegelt", "min_price": 60},
    {"query": "Magic The Gathering Booster Box sealed", "min_price": 70},
    {"query": "Pokemon TCG Starter Deck versiegelt", "min_price": 10},
    # --- Настолки (конкретні тайтли — визначений SKU, повтор, epid) ---
    {"query": "Catan Brettspiel neu",           "min_price": 22},
    {"query": "Ark Nova Brettspiel",            "min_price": 35},
    {"query": "Dune Imperium Brettspiel",       "min_price": 28},
    {"query": "Everdell Brettspiel",            "min_price": 40},
    {"query": "Terraforming Mars Brettspiel",   "min_price": 30},
    # --- LEGO — конкретні флагмани (пік волатильності перед зняттям з випуску) ---
    {"query": "LEGO 10307",                     "min_price": 430},  # Eiffelturm
    {"query": "LEGO 10294 Titanic",             "min_price": 400},
    {"query": "LEGO 71043 Hogwarts",            "min_price": 320},  # Schloss
    {"query": "LEGO 75313 AT-AT",               "min_price": 700},
    {"query": "LEGO 42143 Ferrari",             "min_price": 250},  # Daytona SP3
    {"query": "LEGO 10297 Boutique Hotel",      "min_price": 150},
    # --- TCG sealed (великий розрив «продавець не знає ціни») ---
    {"query": "Pokemon 151 Elite Trainer Box",         "min_price": 45},
    {"query": "Disney Lorcana Booster Display",         "min_price": 80},
    {"query": "Flesh and Blood Booster Box",            "min_price": 65},
    # --- Диски: ігри, що виходять з друку (Switch — Nintendo знімає з продажу) ---
    {"query": "Xenoblade Chronicles Definitive Edition Switch", "min_price": 30},
    {"query": "Fire Emblem Three Houses Switch",        "min_price": 35},
    {"query": "Metroid Prime Remastered Switch",        "min_price": 25},
    # --- Мініатюри ---
    {"query": "Warhammer 40k Leviathan",        "min_price": 90},
    # --- Контрольні точки для порівняння ---
    {"query": "LEGO 75192",                     "min_price": 280},
    {"query": "iPhone 15",                      "min_price": 320},
]

# Параметри виклику Browse API (GET /buy/browse/v1/item_summary/search)
# limit=200 — максимум Browse API; більший розмір відповіді не коштує зайвих
# викликів, але дає ширшу вибірку для медіани та вищий шанс зловити рідкий лот.
SEARCH_LIMIT = int(_get("SEARCH_LIMIT", "200"))
# Валюта для filter=price:[..] (обовʼязкова разом із price на не-USD ринку).
PRICE_CURRENCY = _get("PRICE_CURRENCY", "EUR")

# Кожен цикл робить ДВА запити на категорію (див. README, «Подвійний запит»):
#   A: без sort (best_match) — репрезентативна вибірка для медіани;
#   B: sort=price (за зростанням) — гарантовано ловить найдешевший РЕАЛЬНИЙ лот,
#      бо price:[min_price..] уже відсік аксесуарний шум.
# Результати обʼєднуються за item_id.
SEARCH_SORT_B = "price"

# ФІЛЬТР СТАНУ ЧЕРЕЗ API (не через слова в назві):
#   1000 New | 1500 New other | 1750 New with defects
#   2000 Certified refurbished | 2500 Seller refurbished
# Явно НЕ включаємо: 3000/4000/5000/6000 (Used*) і 7000 (For parts or not working).
SEARCH_CONDITION_IDS = _get("SEARCH_CONDITION_IDS", "1000|1500|1750|2000|2500")
SEARCH_FILTER_BASE = (
    "buyingOptions:{FIXED_PRICE},conditionIds:{" + SEARCH_CONDITION_IDS + "}"
)


def search_filter(min_price) -> str:
    """filter-рядок для запиту: стан + фіксована ціна + нижній поріг ціни."""
    return (f"{SEARCH_FILTER_BASE},price:[{min_price}..],"
            f"priceCurrency:{PRICE_CURRENCY}")

# Тонкий додатковий стоп-лист (запобіжник, НЕ головний механізм).
# Німецькі + англійські маркери "битого/розібраного" стану в назві.
# УВАГА: тут лише слова, що означають ПОШКОДЖЕННЯ. "Ersatzteil" (запчастина),
# "Zubehör" (аксесуар), "Ersatz" (заміна) — НЕ пошкодження: це нормальні слова
# для сумісних фільтрів/щіток і навіть входять у назви категорій
# ("Roomba Bürsten Ersatzteile"). На живих даних стоп-слово "ersatzteil"
# відсіювало ~590 валідних лотів за прогін — прибрано.
CONDITION_BLOCKLIST = [
    "defekt", "bastler", "teile fehlen", "teile fehlt",
    "beschädigt", "beschaedigt", "for parts", "not working",
    "nicht funktionsfähig", "zum ausschlachten",
]

# --- Логіка виявлення аномалій -------------------------------------------------
# Аномалія = ціна нового лота нижча за ANOMALY_THRESHOLD * ІСТОРИЧНА медіана
# для цього epid (а не медіана поточного live-пошуку).
ANOMALY_THRESHOLD = float(_get("ANOMALY_THRESHOLD", "0.6"))

# Мінімум накопичених спостережень для epid, щоб узагалі щось порівнювати.
MIN_HISTORY_POINTS = int(_get("MIN_HISTORY_POINTS", "3"))
# Вікно історії в днях: медіана рахується лише по точках за останні N днів.
HISTORY_WINDOW_DAYS = int(_get("HISTORY_WINDOW_DAYS", "14"))

# Ігнорувати лоти дешевші за MIN_PRICE (захист від сміттєвих цін / аксесуарів).
MIN_PRICE = float(_get("MIN_PRICE", "5.0"))

# --- Фільтр репутації продавця ----------------------------------------------
MIN_SELLER_FEEDBACK_SCORE = int(_get("MIN_SELLER_FEEDBACK_SCORE", "10"))
MIN_SELLER_FEEDBACK_PCT = float(_get("MIN_SELLER_FEEDBACK_PCT", "95.0"))

# --- Сигнал ліквідності (попиту) -------------------------------------------
# Детектор ціни каже лише "дешево відносно історії ЦЬОГО товару". Він нічого не
# каже про те, чи товар узагалі швидко купують. Цей блок будує наближену оцінку
# попиту з ВЛАСНОГО полінгу бота: скільки відстежених лістингів зникло з видачі
# (проксі "продано"), як швидко, і скільки висить активних. Точність принципово
# обмежена без Marketplace Insights API — тому передбачено явний стан UNKNOWN.
LIQUIDITY_WINDOW_DAYS = int(_get("LIQUIDITY_WINDOW_DAYS", "30"))
# Скільки днів треба відстежувати epid, перш ніж узагалі виносити вердикт.
MIN_LIQUIDITY_TRACK_DAYS = int(_get("MIN_LIQUIDITY_TRACK_DAYS", "10"))
# Скільки завершень циклу (зникнень + relist) треба, щоб вийти зі стану UNKNOWN.
MIN_LIQUIDITY_EVENTS = int(_get("MIN_LIQUIDITY_EVENTS", "3"))
LIQ_HIGH_PER_WEEK = float(_get("LIQ_HIGH_PER_WEEK", "1.5"))  # ≥ цього → швидко
LIQ_LOW_PER_WEEK = float(_get("LIQ_LOW_PER_WEEK", "0.5"))    # < цього → повільно
LIQ_FAST_DAYS = float(_get("LIQ_FAST_DAYS", "12"))           # медіана часу до продажу
LIQ_SLOW_DAYS = float(_get("LIQ_SLOW_DAYS", "25"))
# "Затоварення": багато активних пропозицій і майже нема продажів.
SUPPLY_GLUT_MIN = int(_get("SUPPLY_GLUT_MIN", "5"))

# Лістинг вважається зниклим, якщо його не було у видачі стільки днів поспіль.
LISTING_GONE_AFTER_DAYS = int(_get("LISTING_GONE_AFTER_DAYS", "2"))

# --- Виявлення перевиставлення (relist) ------------------------------------
# eBay-продавці циклічно перевиставляють fixed-price лоти. Якщо після зникнення
# лота той самий продавець за кілька днів виставляє майже ідентичний (назва + ціна
# ±толеранс) — це НЕ продаж, і його не можна рахувати у швидкість.
RELIST_WINDOW_DAYS = int(_get("RELIST_WINDOW_DAYS", "5"))
RELIST_PRICE_TOLERANCE = float(_get("RELIST_PRICE_TOLERANCE", "0.06"))
RELIST_TITLE_SIMILARITY = float(_get("RELIST_TITLE_SIMILARITY", "0.7"))

# Провайдер оцінки ліквідності:
#   "" / off  -> SelfTrackedLiquidity (власний proxy, за замовчуванням)
#   on        -> MarketplaceInsightsLiquidity (потребує App Check ticket від eBay)
MARKETPLACE_INSIGHTS_ENABLED = _get("MARKETPLACE_INSIGHTS_ENABLED", "").lower() in (
    "1", "true", "yes", "on",
)

# --- Мережа / повторні спроби ------------------------------------------------
HTTP_TIMEOUT = int(_get("HTTP_TIMEOUT", "20"))
MAX_RETRIES = int(_get("MAX_RETRIES", "4"))
BACKOFF_BASE = float(_get("BACKOFF_BASE", "2.0"))

# --- Режим розвідки (discovery) -------------------------------------------
# Поки DISCOVERY_MODE=true: детектор, історія та ліквідність працюють як завжди,
# АЛЕ окремі сповіщення в Telegram НЕ надсилаються — кожен кандидат-аномалія
# дописується рядком у DISCOVERY_LOG_FILE. `python main.py --discovery-report`
# можна запускати будь-коли; змістовний він приблизно так:
#   ~день 10-14 — цінова вісь дозріла (3 точки на ключ у 14-денному вікні),
#                 кандидати й медіани вже показові; ліквідність ще UNKNOWN;
#   ~день 18-21 — ліквідність дозріла (MIN_LIQUIDITY_TRACK_DAYS + завершення
#                 циклів), score стає надійним — тоді й звужувати CATEGORIES
#                 та ставити DISCOVERY_MODE=false.
DISCOVERY_MODE = _get("DISCOVERY_MODE", "true").lower() in ("1", "true", "yes", "on")
DISCOVERY_PERIOD_DAYS = int(_get("DISCOVERY_PERIOD_DAYS", "7"))
DISCOVERY_LOG_FILE = _get("DISCOVERY_LOG_FILE", "discovery_log.jsonl")
DISCOVERY_REPORT_FILE = _get("DISCOVERY_REPORT_FILE", "discovery_report.md")

# Інтервал полінгу за замовчуванням (для `--interval` без значення).
# 1800с → 48 циклів/добу. Бюджет: 2 виклики × 34 категорії × 48 = 3264/добу
# (≈ 65% від ліміту eBay 5000/добу).
POLL_INTERVAL_SECONDS = int(_get("POLL_INTERVAL_SECONDS", "1800"))

# --- Локальний стан --------------------------------------------------------
# Історія цін + життєвий цикл лістингів (для ліквідності) + список уже
# надісланих item_id (дедублікація) + мапа epid.
PRICE_HISTORY_FILE = _get("PRICE_HISTORY_FILE", "price_history.json")

# --- Похідні прапорці режиму ------------------------------------------------
EBAY_CREDS_OK = bool(EBAY_APP_ID and EBAY_CERT_ID)
TELEGRAM_CREDS_OK = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
DRY_RUN = not (EBAY_CREDS_OK and TELEGRAM_CREDS_OK)
