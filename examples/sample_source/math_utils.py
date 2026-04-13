from dataclasses import dataclass


@dataclass
class Order:
    quantity: int
    unit_price: float


def total_price(order: Order) -> float:
    if order.quantity < 0:
        raise ValueError("quantity must be non-negative")
    return order.quantity * order.unit_price


def format_total(order: Order) -> str:
    return f"{total_price(order):.2f}"
