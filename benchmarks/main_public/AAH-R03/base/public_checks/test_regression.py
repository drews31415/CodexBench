import unittest
from datetime import date
from day_format import parse_day

class Regression(unittest.TestCase):
    def test_retains_dashed_and_rejects_invalid_formats(self):
        self.assertEqual(parse_day("2024-02-29"), date(2024, 2, 29))
        for value in ("2025-02-29", "2024-2-9", "2024-W01-1", " 2024-02-29 "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_day(value)
