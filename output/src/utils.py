def totalPrice(order):
    quantity = order["quantity"]
    unit_price = order["unitPrice"]
    if quantity < 0:
        raise ValueError("quantity must be non-negative")
    return quantity * unit_price


def formatTotal(order):
    return f"{totalPrice(order):.2f}"
