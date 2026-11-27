import unittest
from orders import cancel

class Functional(unittest.TestCase):
    def test_releases_the_linked_reservation(self):
        orders = {"ord-4": {"state": "open", "reservation_id": "hold-7"}}
        stock = {"available": {"tea": 2}, "reserved": {"hold-7": [("tea", 3)]}}
        self.assertTrue(cancel(orders, stock, "ord-4"))
        self.assertEqual((orders["ord-4"]["state"], stock),
                         ("cancelled", {"available": {"tea": 5}, "reserved": {}}))
