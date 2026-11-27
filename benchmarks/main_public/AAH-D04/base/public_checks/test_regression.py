import unittest
from restore import restore_queue

class Regression(unittest.TestCase):
    def test_empty_and_tied_jobs(self):
        self.assertEqual(restore_queue([], 3), [])
        records = [{"task_id": "b", "due_tick": 3}, {"task_id": "a", "due_tick": 3}]
        self.assertEqual(restore_queue(records, 3), [(3, "a"), (3, "b")])
