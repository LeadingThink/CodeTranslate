from utils import formatTotal


def startServer():
    return formatTotal({"quantity": 3, "unitPrice": 4.5})


if __name__ == "__main__":
    print(startServer())
