from tests.conftest import register

CATALOG_A = "code;name;unit_price;moq;pack_size;stock\n3400930000011;Paracetamol 500mg;1,20;10;1;\n3400930000028;Ibuprofen 400mg;2,10;1;1;\n"
CATALOG_B = "code,name,unit_price,moq\n3400930000011,Paracetamol 500mg,1.00,50\n3400930000028,Ibuprofen 400mg,1.90,1\n"
NEEDS = "code;name;selling_price;max_quantity;min_quantity\n3400930000011;Paracetamol 500mg;2,18;30;0\n3400930000028;Ibuprofen 400mg;3,50;20;5\n"


def setup_pharmacy(client, headers):
    sup_a = client.post("/api/suppliers", json={"name": "Wholesaler A"}, headers=headers).json()
    sup_b = client.post("/api/suppliers", json={"name": "Wholesaler B"}, headers=headers).json()
    r = client.post(f"/api/suppliers/{sup_a['id']}/catalog/import", files={"file": ("a.csv", CATALOG_A)}, headers=headers)
    assert r.json()["created"] == 2 and r.json()["errors"] == []
    r = client.post(f"/api/suppliers/{sup_b['id']}/catalog/import", files={"file": ("b.csv", CATALOG_B)}, headers=headers)
    assert r.json()["created"] == 2
    r = client.post("/api/products/import", files={"file": ("needs.csv", NEEDS)}, headers=headers)
    assert r.json()["updated"] == 2 and r.json()["errors"] == []
    return sup_a, sup_b


def test_requires_authentication(client):
    assert client.get("/api/suppliers").status_code == 401
    assert client.get("/api/suppliers", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_register_login_me(client):
    register(client, "Pharmacie du Centre", "Owner@Example.com")
    r = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "secret-pass"})
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["tenant_name"] == "Pharmacie du Centre" and me["role"] == "admin"
    assert client.post("/api/auth/login", json={"email": "owner@example.com", "password": "wrong-pass"}).status_code == 401
    assert client.post(
        "/api/auth/register", json={"pharmacy_name": "X", "email": "owner@example.com", "password": "secret-pass"}
    ).status_code == 409


def test_full_optimization_flow(client):
    headers = register(client, "Pharmacie A", "a@example.com")
    sup_a, sup_b = setup_pharmacy(client, headers)

    comparison = client.get("/api/products/comparison", headers=headers).json()
    para = next(p for p in comparison if p["code"] == "3400930000011")
    assert [o["supplier_name"] for o in para["offers"]] == ["Wholesaler B", "Wholesaler A"]
    assert para["best_price"] == 1.0

    r = client.post("/api/optimize", json={"budget": 1000, "save": True, "name": "Weekly"}, headers=headers)
    assert r.status_code == 200
    result = r.json()
    assert result["status"] == "optimal"
    lines = {(l["supplier_name"], l["product_code"]): l["quantity"] for l in result["lines"]}
    # Paracetamol: B is cheaper but its MOQ (50) exceeds demand (30) → A. Ibuprofen: B is cheaper.
    assert lines == {("Wholesaler A", "3400930000011"): 30, ("Wholesaler B", "3400930000028"): 20}
    assert result["total_profit"] == round(30 * (2.18 - 1.20) + 20 * (3.50 - 1.90), 2)
    assert result["plan_id"]

    plans = client.get("/api/orders", headers=headers).json()
    assert [p["name"] for p in plans] == ["Weekly"]
    detail = client.get(f"/api/orders/{plans[0]['id']}", headers=headers).json()
    assert detail["lines"] == result["lines"]
    csv_text = client.get(f"/api/orders/{plans[0]['id']}/export.csv", headers=headers).text
    assert "Wholesaler A;3400930000011;Paracetamol 500mg;30" in csv_text

    # Small budget: mandatory 5 Ibuprofen still covered.
    small = client.post("/api/optimize", json={"budget": 12}, headers=headers).json()
    assert small["total_cost"] <= 12
    assert sum(l["quantity"] for l in small["lines"] if l["product_code"] == "3400930000028") >= 5

    # Too small: minimum budget reported.
    tiny = client.post("/api/optimize", json={"budget": 1}, headers=headers).json()
    assert tiny["status"] == "infeasible" and tiny["minimum_budget"] == 9.5

    # Restricting to supplier A only.
    only_a = client.post("/api/optimize", json={"budget": 1000, "supplier_ids": [sup_a["id"]]}, headers=headers).json()
    assert {l["supplier_name"] for l in only_a["lines"]} == {"Wholesaler A"}


def test_tenant_isolation(client):
    h1 = register(client, "Pharmacie 1", "one@example.com")
    h2 = register(client, "Pharmacie 2", "two@example.com")
    sup_a, _ = setup_pharmacy(client, h1)
    plan_id = client.post("/api/optimize", json={"budget": 500, "save": True}, headers=h1).json()["plan_id"]

    assert client.get("/api/suppliers", headers=h2).json() == []
    assert client.get("/api/products", headers=h2).json() == []
    assert client.get(f"/api/suppliers/{sup_a['id']}/catalog", headers=h2).status_code == 404
    assert client.delete(f"/api/suppliers/{sup_a['id']}", headers=h2).status_code == 404
    assert client.get(f"/api/orders/{plan_id}", headers=h2).status_code == 404
    assert client.post("/api/optimize", json={"budget": 500}, headers=h2).json()["lines"] == []
    # Same product code can be used independently by another pharmacy.
    r = client.put("/api/products", json={"code": "3400930000011", "selling_price": 9, "max_quantity": 1}, headers=h2)
    assert r.status_code == 200
    p1 = client.get("/api/products?q=3400930000011", headers=h1).json()[0]
    assert p1["selling_price"] == 2.18


def test_team_management(client):
    admin = register(client, "Pharmacie T", "admin@example.com")
    r = client.post("/api/users", json={"email": "member@example.com", "password": "member-pass"}, headers=admin)
    assert r.status_code == 201 and r.json()["role"] == "member"
    token = client.post("/api/auth/login", json={"email": "member@example.com", "password": "member-pass"}).json()
    member = {"Authorization": f"Bearer {token['access_token']}"}
    assert client.get("/api/auth/me", headers=member).json()["tenant_name"] == "Pharmacie T"
    assert client.post("/api/users", json={"email": "x@example.com", "password": "member-pass"}, headers=member).status_code == 403
    assert len(client.get("/api/users", headers=member).json()) == 2


def test_catalog_import_errors_and_replace(client):
    headers = register(client, "Pharmacie C", "c@example.com")
    sup = client.post("/api/suppliers", json={"name": "S"}, headers=headers).json()
    bad = "code;unit_price;moq\nA;1.5;1\n;2;1\nB;abc;1\nC;-1;1\n"
    r = client.post(f"/api/suppliers/{sup['id']}/catalog/import", files={"file": ("x.csv", bad)}, headers=headers).json()
    assert r["created"] == 1 and len(r["errors"]) == 3

    replace = "code;unit_price\nB;3\n"
    r = client.post(
        f"/api/suppliers/{sup['id']}/catalog/import?replace=true", files={"file": ("x.csv", replace)}, headers=headers
    ).json()
    assert r["created"] == 1
    codes = [i["code"] for i in client.get(f"/api/suppliers/{sup['id']}/catalog", headers=headers).json()]
    assert codes == ["B"]

    # Deleting a product removes its catalog entries.
    assert client.delete("/api/products/B", headers=headers).status_code == 204
    assert client.get(f"/api/suppliers/{sup['id']}/catalog", headers=headers).json() == []
