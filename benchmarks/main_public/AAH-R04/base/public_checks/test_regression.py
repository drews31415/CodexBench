import unittest
from retry import enqueue_retry

class Regression(unittest.TestCase):
    def test_first_retry_priority_and_cancellation(self):
        queue = [(11, -1, "existing")]
        self.assertFalse(enqueue_retry(queue, {"id": "cancel", "failures": 1,
                                              "priority": 8, "cancelled": True}, 10))
        self.assertTrue(enqueue_retry(queue, {"id": "urgent", "failures": 1,
                                             "priority": 4, "cancelled": False}, 10))
        self.assertEqual(queue, [(11, -4, "urgent"), (11, -1, "existing")])
