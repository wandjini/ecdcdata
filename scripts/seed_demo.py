"""Create a demo pharmacy with three suppliers using the CSV files in samples/.

Usage: python -m scripts.seed_demo   (against DATABASE_URL, default ./pharmaopt.db)
Login: demo@pharmaopt.local / demo-password
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
EMAIL, PASSWORD = "demo@pharmaopt.local", "demo-password"
SUPPLIERS = [
    ("Grossiste A", "catalog_grossiste_a.csv", 0, 0),
    ("Grossiste B", "catalog_grossiste_b.csv", 150, 0),
    ("Labo Direct", "catalog_labo_direct.csv", 200, 15),
]


def main() -> None:
    with TestClient(app) as client:
        r = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        if r.status_code == 200:
            print(f"Demo pharmacy already exists. Login: {EMAIL} / {PASSWORD}")
            return
        r = client.post(
            "/api/auth/register",
            json={"pharmacy_name": "Pharmacie Démo", "email": EMAIL, "password": PASSWORD, "full_name": "Demo"},
        )
        r.raise_for_status()
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        for name, filename, min_order_value, shipping_fee in SUPPLIERS:
            supplier = client.post(
                "/api/suppliers",
                json={"name": name, "min_order_value": min_order_value, "shipping_fee": shipping_fee},
                headers=headers,
            ).json()
            with open(SAMPLES / filename, "rb") as fh:
                client.post(f"/api/suppliers/{supplier['id']}/catalog/import", files={"file": fh}, headers=headers)
        with open(SAMPLES / "needs.csv", "rb") as fh:
            client.post("/api/products/import", files={"file": fh}, headers=headers)
        print(f"Demo pharmacy created. Login: {EMAIL} / {PASSWORD}")


if __name__ == "__main__":
    main()
