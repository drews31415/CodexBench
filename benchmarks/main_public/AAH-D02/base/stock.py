def release(stock, reservation_id):
    lines = stock["reserved"].pop(reservation_id, ())
    for sku, quantity in lines:
        stock["available"][sku] = stock["available"].get(sku, 0) + quantity
