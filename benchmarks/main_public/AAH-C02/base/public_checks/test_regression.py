import unittest
from reserve import reserve_order

class Regression(unittest.TestCase):
    def test_success_and_existing_order(self):
        stock = {"available": {"tea": 3, "cup": 2}, "reservations": {}}
        lines = [("tea", 2), ("cup", 1), ("tea", 1)]
        self.assertTrue(reserve_order(stock, "ok", lines))
        self.assertEqual(stock, {"available": {"tea": 0, "cup": 1}, "reservations": {"ok": lines}})
        self.assertFalse(reserve_order(stock, "ok", [("cup", 1)]))
        self.assertEqual(stock["available"]["cup"], 1)
