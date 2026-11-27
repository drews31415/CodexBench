import unittest
from ingest import ingest_batch

class Functional(unittest.TestCase):
    def test_collects_invalid_and_duplicate_rows_without_losing_valid_writes(self):
        rows = [{"id": "a", "label": "original"}, {"id": "z"}, {"id": "a", "label": "other"},
                {"id": "z", "label": "last", "flag": True}]
        store = {}
        self.assertEqual(ingest_batch(rows, store),
                         {"accepted": ["a", "z"], "errors": [{"row": 2, "code": "INVALID"},
                                                              {"row": 3, "code": "DUPLICATE"}]})
        self.assertEqual(store, {"a": rows[0], "z": rows[3]})
