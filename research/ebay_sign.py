"""Цифровий підпис запитів eBay для продавців з ЄС (31.10). Finances API (фактичні витрати по продажах, research/finances.py)
відповідає 403 «Missing x-ebay-signature…» без підпису: eBay вимагає RFC 9421 (заголовки x-ebay-signature-key, Signature-Input,
Signature) з ключем Ed25519 з Key Management API. Ключ створюється один раз (createSigningKey), приватну частину eBay не
зберігає — бот передає її в Apps Script (зашифровано, як ключ eBay) і отримує назад у кожному запиті звірки.
https://developer.ebay.com/develop/guides/digital-signatures-for-apis
"""
import base64
import time
from urllib.parse import urlparse

KEY_URL = "https://apiz.ebay.com/developer/key_management/v1/signing_key"
COMPONENTS = '("x-ebay-signature-key" "@method" "@path" "@authority")'


def create_key(token: str, post) -> dict:
    """Новий ключ Ed25519 → {"k": публічний ключ як JWE, "p": приватний (base64 DER), "id": signingKeyId}."""
    r = post(KEY_URL, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
             json={"signingKeyCipher": "ED25519"}, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Key Management HTTP {r.status_code}: {r.text[:160]}")
    j = r.json()
    return {"k": j["jwe"], "p": j["privateKey"], "id": j.get("signingKeyId") or ""}


def signature_base(jwe: str, method: str, url: str, created: int) -> str:
    u = urlparse(url)
    return "\n".join([f'"x-ebay-signature-key": {jwe}', f'"@method": {method.upper()}', f'"@path": {u.path}',
                      f'"@authority": {u.netloc}', f'"@signature-params": {COMPONENTS};created={created}'])


def headers(key: dict, method: str, url: str, created: int | None = None) -> dict:
    """Заголовки підпису для запиту без тіла (GET)."""
    from cryptography.hazmat.primitives.serialization import load_der_private_key
    created = int(created or time.time())
    priv = load_der_private_key(base64.b64decode(key["p"]), password=None)
    sig = base64.b64encode(priv.sign(signature_base(key["k"], method, url, created).encode())).decode()
    return {"x-ebay-signature-key": key["k"], "Signature-Input": f"sig1={COMPONENTS};created={created}",
            "Signature": f"sig1=:{sig}:"}
