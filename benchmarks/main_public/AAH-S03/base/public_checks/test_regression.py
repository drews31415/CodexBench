import unittest
from csv_rows import load_rows

class Regression(unittest.TestCase):
    def test_nonzero_order_and_invalid_numeric_input(self):
        self.assertEqual(load_rows("id,score\n009,-4\nc,3\n"),
                         [{"id": "009", "score": -4}, {"id": "c", "score": 3}])
        with self.assertRaises(ValueError):
            load_rows("id,score\na,missing\n")
