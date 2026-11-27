from stock import release


def cancel(orders, stock, order_id):
    order = orders[order_id]
    if order["state"] == "cancelled":
        return False
    release(stock, order_id)
    order["state"] = "cancelled"
    return True
