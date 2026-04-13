const { formatTotal } = require("./utils");

function startServer() {
  return formatTotal({ quantity: 3, unitPrice: 4.5 });
}

if (require.main === module) {
  console.log(startServer());
}

module.exports = { startServer };
