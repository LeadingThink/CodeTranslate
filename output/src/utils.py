def totalPrice(order):
    quantity = order["quantity"] if isinstance(order, dict) else order.quantity
    unit_price = order["unitPrice"] if isinstance(order, dict) else order.unitPrice
    if quantity < 0:
        raise ValueError("quantity must be non-negative")
    return quantity * unit_price


def formatTotal(order):
    return format(round(totalPrice(order) + 1e-12, 2), ".2f")
