# PharmaOpt – pharmacy order optimizer

Multitenant web application that helps pharmacies **maximise their profit (bénéfice) when placing orders**.
Each pharmacy loads the catalogs of its suppliers (wholesalers, laboratories…), which share a common
product code (CIP / EAN). Given a **budget**, the optimizer computes the most profitable order across all
suppliers, respecting the **minimum order quantity (MOQ)** of every catalog line.

## Features

- **Multitenant**: each pharmacy is a tenant with its own users, suppliers, catalogs, products and saved orders.
  Shared database schema, every row carries a `tenant_id` and every query is scoped to the caller's tenant
  (JWT carries user + tenant). Admins can invite colleagues.
- **Supplier catalogs**: CSV import (`,` `;` or tab, decimal comma accepted, FR/EN headers) or manual entry:
  `code; name; unit_price; moq; pack_size; stock`. Optional "replace whole catalog" mode.
  Supplier terms: minimum order value and shipping fee.
- **Products & needs**: per product code, the selling price, the expected demand (`max_quantity`) and an
  optional mandatory quantity (`min_quantity`).
- **Price comparison** of all suppliers for each shared product code.
- **Optimization** with an exact MILP solver (HiGHS via SciPy), see below. Results grouped by supplier,
  with KPIs, uncovered demand, and saved order plans exportable as CSV purchase orders.

## Optimization model

For each offer *i* (a product at a supplier) the solver chooses an integer number of packs.
It **maximises**

    Σ (selling_price − unit_price) × quantity  −  Σ shipping fees of the suppliers used

subject to:

| Constraint | Meaning |
|---|---|
| `quantity = 0` **or** `quantity ≥ MOQ` | minimum order quantity per catalog line (semi-continuous variable) |
| `quantity` multiple of `pack_size`, `≤ stock` | packaging and availability |
| `min_quantity ≤ Σ suppliers quantity ≤ max_quantity` | the same product (shared code) can be split across suppliers, without exceeding demand |
| supplier subtotal `≥ min_order_value` if the supplier is used | supplier minimum order |
| `Σ cost + shipping ≤ budget` | budget |

A second pass keeps the optimal profit and minimises spending (ties → cheaper order).
If the budget cannot cover the mandatory quantities, the API returns the **minimum budget required**.
Products with a negative margin are never bought unless they are mandatory.
The optimizer is cross-checked against an exhaustive search in the tests and solves
~3 000 products × 9 000 offers to proven optimality in a few seconds.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m scripts.seed_demo          # optional demo pharmacy (demo@pharmaopt.local / demo-password)
SECRET_KEY=$(openssl rand -hex 32) uvicorn app.main:app --reload
```

Open http://localhost:8000 (API docs at http://localhost:8000/docs).

With Docker:

```bash
docker build -t pharmaopt .
docker run -p 8000:8000 -e SECRET_KEY=change-me -v pharmaopt-data:/data pharmaopt
```

Configuration (environment variables):

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./pharmaopt.db` | any SQLAlchemy URL, e.g. `postgresql+psycopg://…` (install the driver) |
| `SECRET_KEY` | random per process | **set it in production**, otherwise tokens are invalidated on restart |
| `TOKEN_TTL_MINUTES` | `720` | |
| `SOLVER_TIME_LIMIT` | `30` | seconds; beyond it the best order found so far is returned (`status: feasible`) |

## Tests

```bash
pytest
```

## API overview

| Method & path | |
|---|---|
| `POST /api/auth/register` | create a pharmacy (tenant) and its admin |
| `POST /api/auth/login`, `GET /api/auth/me` | authentication |
| `GET/POST /api/users` | team members (admin only for creation) |
| `GET/POST /api/suppliers`, `PUT/DELETE /api/suppliers/{id}` | suppliers and their terms |
| `GET/PUT /api/suppliers/{id}/catalog`, `POST …/catalog/import` | catalog lines, CSV import |
| `GET/PUT /api/products`, `POST /api/products/import` | products and needs |
| `GET /api/products/comparison` | supplier price comparison per product code |
| `POST /api/optimize` | `{budget, supplier_ids?, save?, name?}` → optimal order |
| `GET /api/orders`, `GET/DELETE /api/orders/{id}`, `GET /api/orders/{id}/export.csv` | saved orders |

## Project layout

```
app/
  optimizer.py     MILP model (pure, framework-independent)
  models.py        SQLAlchemy models (tenant-scoped)
  routers/         auth, suppliers & catalogs, products, optimization & orders
  static/          single-page web UI (no build step)
samples/           example catalogs and needs (CSV)
scripts/seed_demo.py
tests/
```

## Next steps (not implemented yet)

- Database migrations (Alembic) and PostgreSQL row-level security for defence in depth.
- Volume-based price tiers (e.g. lower unit price above 100 units), expiry dates, promotions.
- Demand forecasting from sales history to fill `max_quantity` automatically.
- Sending purchase orders to suppliers (EDI / e-mail).
