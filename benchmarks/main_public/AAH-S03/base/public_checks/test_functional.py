import unittest
from csv_rows import load_rows

class Functional(unittest.TestCase):
    def test_empty_and_zero_remain_distinguishable(self):
        self.assertEqual(load_rows("id,score\na,\nb,0\n"),
                         [{"id": "a", "score": None}, {"id": "b", "score": 0}])
