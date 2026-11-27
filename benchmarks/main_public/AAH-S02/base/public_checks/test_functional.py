import unittest
from decimal import Decimal
from line_total import invoice_total

class Functional(unittest.TestCase):
    def test_rounds_once_after_summing(self):
        lines = [("0.335", 1), ("0.335", 1)]
        self.assertEqual(invoice_total(lines), Decimal("0.67"))
