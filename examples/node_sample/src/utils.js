function totalPrice(order) {
  if (order.quantity < 0) {
    throw new Error("quantity must be non-negative");
  }
  return order.quantity * order.unitPrice;
}

function formatTotal(order) {
  return totalPrice(order).toFixed(2);
}

module.exports = { totalPrice, formatTotal };
