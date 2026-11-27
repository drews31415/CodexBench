import unittest
from codec import dump_queue
from restore import restore_queue

class Functional(unittest.TestCase):
    def test_restores_future_and_overdue_ticks(self):
        stored = dump_queue([(40, "a"), (18, "b"), (40, "c")])
        self.assertEqual(restore_queue(stored, 25), [(18, "b"), (40, "a"), (40, "c")])
