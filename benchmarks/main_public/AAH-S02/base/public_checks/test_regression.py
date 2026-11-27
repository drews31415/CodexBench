import unittest
from decimal import Decimal
from line_total import invoice_total

class Regression(unittest.TestCase):
    def test_exact_amount_and_empty_order(self):
        self.assertEqual(invoice_total([("1.25", 3)]), Decimal("3.75"))
        self.assertEqual(invoice_total([]).as_tuple().exponent, -2)
