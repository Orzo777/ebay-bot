"""Підпис запитів eBay (research/ebay_sign.py, 31.10): RFC 9421 з Ed25519 — без мережі, власний тестовий ключ."""
import base64
import sys
import unittest

sys.path.insert(0, ".")
sys.path.insert(0, "research")
import ebay_sign  # noqa: E402


class TestSign(unittest.TestCase):
    def test_headers_verify(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
        priv = Ed25519PrivateKey.generate()
        key = {"k": "eyJ0ZXN0IjoiandlIn0", "p": base64.b64encode(priv.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())).decode()}
        url = "https://apiz.ebay.com/sell/finances/v1/transaction"
        h = ebay_sign.headers(key, "GET", url, created=1700000000)
        self.assertEqual(h["x-ebay-signature-key"], key["k"])
        self.assertEqual(h["Signature-Input"], 'sig1=("x-ebay-signature-key" "@method" "@path" "@authority");created=1700000000')
        base = ebay_sign.signature_base(key["k"], "GET", url, 1700000000)
        self.assertEqual(base.splitlines()[2:4], ['"@path": /sell/finances/v1/transaction', '"@authority": apiz.ebay.com'])
        sig = base64.b64decode(h["Signature"][len("sig1=:"):-1])
        priv.public_key().verify(sig, base.encode())   # не впаде — підпис правильний


if __name__ == "__main__":
    unittest.main()
