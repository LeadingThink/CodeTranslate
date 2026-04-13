from math_utils import Order, format_total


def main() -> str:
    order = Order(quantity=3, unit_price=4.5)
    return format_total(order)


if __name__ == "__main__":
    print(main())
