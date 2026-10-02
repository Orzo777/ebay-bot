"""Які картки KA вже надіслані (02.10): номер оголошення → id повідомлення в Telegram, назва, час.

Потрібно, щоб відповідь продавця (research/ka_reply.py) прийшла РЕПЛАЄМ на ту картку, з якої користувач писав продавцю.
Зберігається в стані бота (ram_mail_state.json → "cards"; для «поділитися» — ka_share_state.json), 14 днів, до 400 записів.
"""
import json
import re
from datetime import datetime, timedelta, timezone

KEEP_DAYS, KEEP_MAX = 14, 400
_AD_ID = re.compile(r"/s-anzeige/(?:[^/?#\s]+/)?(\d{6,})")


def ad_id(link: str | None) -> str | None:
    m = _AD_ID.search(link or "")
    return m.group(1) if m else None


def remember(cards: dict, link: str | None, message_id: int | None, title: str, now: datetime | None = None):
    aid = ad_id(link)
    if not aid or not message_id:
        return
    now = now or datetime.now(timezone.utc)
    cards[aid] = {"m": int(message_id), "t": (title or "")[:90], "ts": now.isoformat()}
    cut = (now - timedelta(days=KEEP_DAYS)).isoformat()
    for k in [k for k, v in cards.items() if v.get("ts", "") < cut]:
        del cards[k]
    if len(cards) > KEEP_MAX:
        for k, _ in sorted(cards.items(), key=lambda kv: kv[1].get("ts", ""))[:len(cards) - KEEP_MAX]:
            del cards[k]


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("cards") or {}
    except (OSError, ValueError, AttributeError):
        return {}


_STOP = {"und", "mit", "für", "fur", "der", "die", "das", "neu", "top", "zustand", "gebraucht", "inkl", "ovp", "verkaufe",
         "von", "zu", "the", "and", "for", "ram"}


def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9äöüß]{2,}", (s or "").lower()) if w not in _STOP}


def find(cards: dict, link_ids: list[str], listing_title: str) -> int | None:
    """Картка для відповіді: за номером оголошення з листа, інакше — за назвою (≥60% слів назви, однозначно)."""
    for aid in link_ids or []:
        if aid in cards:
            return cards[aid]["m"]
    want = _words(listing_title)
    if len(want) < 2:
        return None
    scored = []
    for v in cards.values():
        have = _words(v.get("t", ""))
        if have:
            scored.append((len(want & have) / len(want), v.get("ts", ""), v["m"]))
    scored.sort(reverse=True)   # за збігом, потім за часом: з двох однакових назв — новіша картка
    return scored[0][2] if scored and scored[0][0] >= 0.6 else None
