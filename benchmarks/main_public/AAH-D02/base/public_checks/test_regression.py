import unittest
from orders import cancel

class Regression(unittest.TestCase):
    def test_repeated_cancel_and_missing_order(self):
        orders = {"ord-1": {"state": "cancelled", "reservation_id": "hold-1"}}
        stock = {"available": {"tea": 5}, "reserved": {}}
        self.assertFalse(cancel(orders, stock, "ord-1"))
        self.assertEqual(stock["available"]["tea"], 5)
        with self.assertRaises(KeyError):
            cancel(orders, stock, "unknown")
