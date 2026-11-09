from inventory import append_item


def main():
    first = append_item("first")
    second = append_item("second")
    print(first, second)


if __name__ == "__main__":
    main()
