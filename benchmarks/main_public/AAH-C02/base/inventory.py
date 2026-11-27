def take(available, sku, quantity):
    if available.get(sku, 0) < quantity:
        return False
    available[sku] -= quantity
    return True
