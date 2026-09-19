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
# ФОКУСНИЙ список: компактне, важко підробити, медіана ~€90-410, розкид цін
# ("продавець не знає ціни"). Бюджет ~€1к → 5-8 позицій одночасно. Кожен напрям
# пройшов пробник структурної придатності (обсяг лотів + повторюваність
# product_key) і перевірку стану ринку (вересень 2026).
#
# min_price — очікувана НИЖНЯ межа реальної ціни ЦЬОГО товару (EUR), ~45-55%
# від спостереженої медіани. Іде у filter=price:[min_price..] в ОБОХ запитах
# циклу. Без неї sort=price тягне у вибірку аксесуарний шум (поштучні деталі,
# протектори карт, кабелі), і товар не потрапляє в перші 50.
#
# Свідомо НЕ включено: дешеві витратники (фільтри/еспандери — реальний виграш
# €5-15), габаритне (електроінструмент, меблі — дорога пересилка), структурно
# тонкі ринки (LEGO за SKU, нішеві настолки, вінілові платівки — фрагментація
# по пресингу), легко-підроблюване (AirPods, Leatherman), Pokemon 151 та Marvel
# Crisis Protocol (спадний ринок), Playmobil (банкрутство geobra, лютий 2026).
CATEGORIES = [
    # --- Запечатане TCG (шринк + вага = важко підробити; «продавець не знає ціни») ---
    {"query": "Magic The Gathering Booster Box sealed", "min_price": 70},
    {"query": "Pokemon Booster Box versiegelt",         "min_price": 60},
    {"query": "Disney Lorcana Booster Display",         "min_price": 80},
    {"query": "One Piece Card Game Display OP",         "min_price": 60},
    {"query": "Star Wars Unlimited Booster Box",        "min_price": 55},
    {"query": "Flesh and Blood Booster Box",            "min_price": 65},
    # --- Warhammer / мініатюри (GW-пластик ~ непідробний; GW щороку +ціни) ---
    {"query": "Warhammer 40k Leviathan",               "min_price": 90},
    {"query": "Warhammer 40k Combat Patrol",           "min_price": 90},
    {"query": "Warhammer Necromunda",                  "min_price": 50},
    {"query": "Warhammer 40k Start Collecting",        "min_price": 50},
    {"query": "Warhammer Underworlds",                 "min_price": 25},
    # Legion: запит широкий (core set + десятки юніт-паків) — поріг €70 цілить
    # лише в core/великі експансії, відсікає поштучні паки/кубики/кодекси.
    {"query": "Star Wars Legion",                      "min_price": 70},
    # --- Дитяче / аудіо (ліцензійний контент — підробка безсенсова) ---
    {"query": "Ravensburger tiptoi Starterset",        "min_price": 30},
    # --- Консольні видання (запечатане, колекційне) ---
    {"query": "Zelda Tears of the Kingdom Collector's Edition", "min_price": 80},
    # --- Аудіо / муз. інструменти (визначений SKU, churn від апгрейдів) ---
    {"query": "Rode NT1",                              "min_price": 110},
    {"query": "Electro-Harmonix Big Muff",             "min_price": 55},
    # --- Хоум-офіс (корпоративний churn, важко підробити) ---
    {"query": "Jabra Evolve2 65",                      "min_price": 90},
    {"query": "Keychron Tastatur",                     "min_price": 70},
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
# Точка = унікальний лістинг ключа (дата першого бачення). При 14 днях перший
# знімок ринку «випадає» разом і готовність ключів обвалюється (18.09: 932→512);
# 30 днів перекриває цю сходинку. Повернути до 14-21, коли накопичаться
# органічні точки (≈ жовтень 2026).
HISTORY_WINDOW_DAYS = int(_get("HISTORY_WINDOW_DAYS", "30"))

# --- Шар якості знахідки (quality.py) --------------------------------------
# Еталон = ІНШІ порівнянні лоти того ж ключа (identity.py), по продавцю. Знахідка
# лише якщо: достатньо еталона, він однорідний, ціна помітно нижча, не «ринковий
# кластер», економія не смішна. Поріг ANOMALY_THRESHOLD (вище) лишається.
# Еталон «сильний»: >= STRONG_COMPS лотів від >= STRONG_SELLERS продавців → допустимий розкид
# до QUALITY_MAX_DISPERSION. «Слабкий» (але не менше MIN_*): лише якщо ринок майже однорідний
# (розкид <= QUALITY_TIGHT_DISPERSION) — 4 лоти по €200±5 переконливіші за 5 лотів по €120-280.
QUALITY_MIN_COMPS = int(_get("QUALITY_MIN_COMPS", "4"))          # інших лотів у ключі (мінімум)
QUALITY_MIN_SELLERS = int(_get("QUALITY_MIN_SELLERS", "3"))      # різних продавців (мінімум)
QUALITY_STRONG_COMPS = int(_get("QUALITY_STRONG_COMPS", "5"))
QUALITY_STRONG_SELLERS = int(_get("QUALITY_STRONG_SELLERS", "4"))
QUALITY_TIGHT_DISPERSION = float(_get("QUALITY_TIGHT_DISPERSION", "0.12"))
QUALITY_MAX_DISPERSION = float(_get("QUALITY_MAX_DISPERSION", "0.35"))  # (Q3-Q1)/медіана
QUALITY_MIN_RATIO = float(_get("QUALITY_MIN_RATIO", "0.30"))     # глибше = підозра (не той товар/скам)
QUALITY_MIN_SAVING = float(_get("QUALITY_MIN_SAVING", "25"))     # EUR, абсолютна економія
QUALITY_CLUSTER_PCT = float(_get("QUALITY_CLUSTER_PCT", "0.12")) # ±12% ціни кандидата
QUALITY_CLUSTER_MAX = int(_get("QUALITY_CLUSTER_MAX", "2"))      # >=2 інших поруч = ринкова ціна

# Ринок покупця: приймаємо лише лоти з цих країн (митниця/ПДВ/повернення поза ЄС).
MARKET_COUNTRIES = [c for c in _get("MARKET_COUNTRIES", "DE,AT").split(",") if c]

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
MIN_LIQUIDITY_TRACK_DAYS = int(_get("MIN_LIQUIDITY_TRACK_DAYS", "7"))
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

# ALERT_MODE (нова система фільтрів: identity.py + quality.py):
#   shadow — усе працює як у бою, але вердикти пишуться в discovery_log.jsonl
#            (kind="shadow"), Telegram НЕ шле;
#   live   — Telegram лише для знахідок, що пройшли ВСІ шари.
# Якщо ALERT_MODE не задано: DISCOVERY_MODE=true → shadow, інакше → live.
_am = _get("ALERT_MODE", "").lower()
ALERT_MODE = _am if _am in ("shadow", "live") else ("shadow" if DISCOVERY_MODE else "live")
# Аварійний вимикач нової ідентифікації (повернення до старого ключа product_key).
IDENTITY_V2 = _get("IDENTITY_V2", "true").lower() in ("1", "true", "yes", "on")
# У Telegram лише ці рівні ліквідності (UNKNOWN/LOW → у shadow-лог із причиною).
ALERT_TIERS = [t for t in _get("ALERT_TIERS", "OK,MEDIUM").split(",") if t]
# Суворіший продавець для СПОВІЩЕННЯ (еталон рахується за базовим MIN_SELLER_*).
# Вік лота (itemCreationDate). Справжня знижка не висить тижнями: її купують за години.
# Лот, що місяцями/роками висить на пів-ціни, — «привид» (продавець скасовує замовлення)
# або застарілий GTC. Старіші за це число днів → HOLD "stale-listing".
ALERT_MAX_AGE_DAYS = float(_get("ALERT_MAX_AGE_DAYS", "7"))
ALERT_MIN_SELLER_SCORE = int(_get("ALERT_MIN_SELLER_SCORE", "25"))
ALERT_MIN_SELLER_PCT = float(_get("ALERT_MIN_SELLER_PCT", "97.0"))
STATE_SCHEMA = 2
DISCOVERY_PERIOD_DAYS = int(_get("DISCOVERY_PERIOD_DAYS", "7"))
DISCOVERY_LOG_FILE = _get("DISCOVERY_LOG_FILE", "discovery_log.jsonl")
DISCOVERY_REPORT_FILE = _get("DISCOVERY_REPORT_FILE", "discovery_report.md")

# Інтервал полінгу за замовчуванням (для `--interval` без значення).
# 1800с → 48 циклів/добу. Бюджет: 2 виклики × 18 категорій × 48 = 1728/добу
# (≈ 35% від ліміту eBay 5000/добу; запас під частіші тригери з ноута).
POLL_INTERVAL_SECONDS = int(_get("POLL_INTERVAL_SECONDS", "1800"))

# --- Локальний стан --------------------------------------------------------
# Історія цін + життєвий цикл лістингів (для ліквідності) + список уже
# надісланих item_id (дедублікація) + мапа epid.
PRICE_HISTORY_FILE = _get("PRICE_HISTORY_FILE", "price_history.json")

# --- Похідні прапорці режиму ------------------------------------------------
EBAY_CREDS_OK = bool(EBAY_APP_ID and EBAY_CERT_ID)
TELEGRAM_CREDS_OK = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
DRY_RUN = not (EBAY_CREDS_OK and TELEGRAM_CREDS_OK)
