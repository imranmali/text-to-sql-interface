"""A small, deterministic e-commerce database used for demos and tests."""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DDL = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    signup_date TEXT NOT NULL
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price REAL NOT NULL,
    stock INTEGER NOT NULL
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date TEXT NOT NULL,
    status TEXT NOT NULL,
    total_amount REAL NOT NULL
);

CREATE TABLE order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL
);
"""

FIRST_NAMES = [
    "Ava",
    "Liam",
    "Noah",
    "Emma",
    "Olivia",
    "Mia",
    "Lucas",
    "Amir",
    "Sara",
    "Yuki",
    "Omar",
    "Lena",
    "Ivan",
    "Zara",
    "Ethan",
    "Chloe",
    "Hana",
    "Raj",
    "Nina",
    "Leo",
]
LAST_NAMES = [
    "Smith",
    "Khan",
    "Garcia",
    "Chen",
    "Muller",
    "Rossi",
    "Tanaka",
    "Ali",
    "Novak",
    "Silva",
    "Brown",
    "Kim",
    "Haddad",
    "Larsen",
    "Dubois",
]
CITIES = [
    ("London", "UK"),
    ("Manchester", "UK"),
    ("New York", "USA"),
    ("Chicago", "USA"),
    ("Berlin", "Germany"),
    ("Paris", "France"),
    ("Tokyo", "Japan"),
    ("Karachi", "Pakistan"),
    ("Toronto", "Canada"),
    ("Sydney", "Australia"),
]
PRODUCTS = [
    ("Laptop Pro 14", "Electronics", 1299.0),
    ("Wireless Mouse", "Electronics", 25.5),
    ("Mechanical Keyboard", "Electronics", 89.99),
    ("4K Monitor", "Electronics", 349.0),
    ("USB-C Hub", "Electronics", 39.0),
    ("Noise Cancelling Headphones", "Electronics", 199.0),
    ("Office Chair", "Furniture", 229.0),
    ("Standing Desk", "Furniture", 499.0),
    ("Bookshelf", "Furniture", 120.0),
    ("Desk Lamp", "Furniture", 35.0),
    ("Coffee Beans 1kg", "Grocery", 18.5),
    ("Green Tea 100 bags", "Grocery", 7.25),
    ("Dark Chocolate", "Grocery", 3.99),
    ("Running Shoes", "Sports", 110.0),
    ("Yoga Mat", "Sports", 29.0),
    ("Dumbbell Set", "Sports", 75.0),
    ("Water Bottle", "Sports", 15.0),
    ("Python Cookbook", "Books", 45.0),
    ("SQL Fundamentals", "Books", 39.5),
    ("Sci-Fi Anthology", "Books", 22.0),
]
STATUSES = ["delivered", "delivered", "delivered", "shipped", "pending", "cancelled"]


def create_sample_database(path: str | Path, overwrite: bool = False, seed: int = 42) -> Path:
    """Create the sample database at ``path`` and return the path.

    Data is generated from a fixed seed, so every build is identical.
    """
    path = Path(path)
    if path.exists():
        if not overwrite:
            return path
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        start = date(2024, 1, 1)

        customers = []
        for i in range(1, 51):
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            city, country = rng.choice(CITIES)
            signup = start + timedelta(days=rng.randint(0, 365))
            customers.append(
                (
                    i,
                    first,
                    last,
                    f"{first}.{last}{i}@example.com".lower(),
                    city,
                    country,
                    signup.isoformat(),
                )
            )
        conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)", customers)

        products = [
            (i, name, cat, price, rng.randint(0, 200))
            for i, (name, cat, price) in enumerate(PRODUCTS, start=1)
        ]
        conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?)", products)

        item_id = 1
        for order_id in range(1, 201):
            customer_id = rng.randint(1, len(customers))
            order_date = start + timedelta(days=rng.randint(0, 600))
            items = []
            for _ in range(rng.randint(1, 4)):
                product = rng.choice(products)
                qty = rng.randint(1, 3)
                items.append((item_id, order_id, product[0], qty, product[3]))
                item_id += 1
            total = round(sum(q * p for _, _, _, q, p in items), 2)
            conn.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
                (order_id, customer_id, order_date.isoformat(), rng.choice(STATUSES), total),
            )
            conn.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)", items)
        conn.commit()
    finally:
        conn.close()
    return path
