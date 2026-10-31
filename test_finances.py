"""Фактичні витрати eBay по продажах (research/finances.py, 31.10) — без мережі. Суми — як у «Zahlungen» за №5:
етикетка Hermes 5,79 €, реклама Basis 9,52 € (5% + 19% ПДВ від 160 €)."""
import sys
import unittest
from unittest import mock

sys.path.insert(0, ".")
sys.path.insert(0, "research")
import finances  # noqa: E402

TXN = [
    {"transactionType": "SALE", "orderId": "12-15361-67725", "bookingEntry": "CREDIT", "amount": {"value": "160.00"},
     "orderLineItems": [{"marketplaceFees": [{"feeType": "FINAL_VALUE_FEE", "amount": {"value": "0.00"}}]}]},
    {"transactionType": "NON_SALE_CHARGE", "bookingEntry": "DEBIT", "amount": {"value": "9.52"}, "feeType": "AD_FEE",
     "references": [{"referenceId": "12-15361-67725", "referenceType": "ORDER_ID"}]},
    {"transactionType": "SHIPPING_LABEL", "orderId": "12-15361-67725", "bookingEntry": "DEBIT", "amount": {"value": "5.79"}},
    {"transactionType": "SHIPPING_LABEL", "orderId": "27-15296-80094", "bookingEntry": "DEBIT", "amount": {"value": "6.19"}},
    {"transactionType": "PAYOUT", "bookingEntry": "DEBIT", "amount": {"value": "149.30"}},   # без замовлення — не наше
]


class TestFinances(unittest.TestCase):
    def test_costs_per_order(self):
        self.assertEqual(finances.order_costs(TXN), {"12-15361-67725": {"label": 5.79, "fees": 9.52},
                                                     "27-15296-80094": {"label": 6.19, "fees": 0.0}})

    def test_refunded_label_is_negative(self):
        t = TXN[2:3] + [dict(TXN[2], bookingEntry="CREDIT")]
        self.assertEqual(finances.order_costs(t), {})   # купив і повернув етикетку — витрат немає

    def test_fetch_pages(self):
        pages = [{"transactions": [{}] * 200}, {"transactions": [{}] * 3}]
        get = mock.Mock(side_effect=[mock.Mock(status_code=200, json=lambda p=p: p) for p in pages])
        self.assertEqual(len(finances.fetch("tok", get)), 203)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["offset"], 200)
        bad = mock.Mock(return_value=mock.Mock(status_code=403, text="insufficient scope"))
        with self.assertRaises(RuntimeError):
            finances.fetch("tok", bad)


if __name__ == "__main__":
    unittest.main()
