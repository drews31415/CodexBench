import unittest
from ingest import ingest_batch

class Regression(unittest.TestCase):
    def test_empty_batch_and_existing_store_duplicate(self):
        store = {"known": {"id": "known", "label": "first"}}
        self.assertEqual(ingest_batch([], store), {"accepted": [], "errors": []})
        self.assertEqual(ingest_batch([{"id": "known", "label": "second"}], store),
                         {"accepted": [], "errors": [{"row": 1, "code": "DUPLICATE"}]})
        self.assertEqual(store["known"]["label"], "first")
