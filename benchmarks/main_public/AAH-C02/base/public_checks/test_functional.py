import unittest
from copy import deepcopy
from reserve import reserve_order

class Functional(unittest.TestCase):
    def test_partial_and_duplicate_sku_failures_preserve_state(self):
        for lines in ([("tea", 2), ("missing", 1)], [("tea", 2), ("tea", 2)]):
            with self.subTest(lines=lines):
                stock = {"available": {"tea": 3}, "reservations": {"old": [("tea", 1)]}}
                before = deepcopy(stock)
                self.assertFalse(reserve_order(stock, "new", lines))
                self.assertEqual(stock, before)
