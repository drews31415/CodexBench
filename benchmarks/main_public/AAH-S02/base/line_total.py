from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def invoice_total(lines):
    billed = Decimal("0.00")
    for price, quantity in lines:
        amount = Decimal(price) * quantity
        billed += amount.quantize(CENT, rounding=ROUND_HALF_UP)
    return billed
