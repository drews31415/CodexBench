import unittest
from retry import enqueue_retry

class Functional(unittest.TestCase):
    def test_grows_delay_up_to_eight_ticks(self):
        queue = []
        for task_id, failures in (("near", 3), ("capped", 8)):
            enqueue_retry(queue, {"id": task_id, "failures": failures,
                                  "priority": 1, "cancelled": False}, 10)
        self.assertEqual(queue, [(14, -1, "near"), (18, -1, "capped")])
