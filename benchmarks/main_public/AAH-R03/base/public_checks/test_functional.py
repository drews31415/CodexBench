import unittest
from datetime import date
from day_format import parse_day

class Functional(unittest.TestCase):
    def test_accepts_compact_calendar_date(self):
        try:
            actual = parse_day("20240229")
        except ValueError:
            self.fail("the compact date was rejected")
        self.assertEqual(actual, date(2024, 2, 29))
