from inventory import take


def reserve_order(stock, order_id, lines):
    if order_id in stock["reservations"]:
        return False
    for sku, quantity in lines:
        if not take(stock["available"], sku, quantity):
            return False
    stock["reservations"][order_id] = list(lines)
    return True
