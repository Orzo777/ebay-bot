"""Тихі години (09.10): уночі (23:00–07:00 за Берліном) картки «можна» / «торгуйся» приходять без звуку, а «бери»
(BUY-GOOD / BUY-EXCELLENT) і аукціони — як завжди: там важить кожна хвилина. Вимкнути — QUIET_HOURS=0."""
import os
from datetime import datetime, timedelta, timezone

QUIET_FROM, QUIET_TO = 23, 7
LOUD = ("BUY-GOOD", "BUY-EXCELLENT")

try:
    from zoneinfo import ZoneInfo
    _BERLIN = ZoneInfo("Europe/Berlin")
except Exception:   # Windows без tzdata
    _BERLIN = timezone(timedelta(hours=2))


def night(now: datetime | None = None) -> bool:
    h = (now or datetime.now(timezone.utc)).astimezone(_BERLIN).hour
    return h >= QUIET_FROM or h < QUIET_TO


def silent(verdict: str | None, now: datetime | None = None) -> bool:
    """True — надіслати без звуку (тиха нічна година і не «бери»)."""
    if os.getenv("QUIET_HOURS", "1") != "1":
        return False
    return night(now) and (verdict or "") not in LOUD
