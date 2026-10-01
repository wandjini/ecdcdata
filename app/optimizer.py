"""Order optimization engine.

Given supplier catalogs (offers), the pharmacy's needs (selling price and
quantity bounds per product) and a budget, find the order that maximises the
expected profit (bénéfice):

    profit = sum((selling_price - unit_price) * quantity) - shipping fees

The problem is modelled as a Mixed-Integer Linear Program and solved with
HiGHS (through ``scipy.optimize.milp``), so the result is the exact optimum,
not a greedy approximation.

Variables
---------
For every usable offer ``i`` (one product at one supplier):
    n_i  integer  number of packs ordered (quantity q_i = pack_size_i * n_i)
    y_i  binary   1 if the offer is used
For every supplier ``s``:
    z_s  binary   1 if at least one line is ordered from the supplier

Constraints
-----------
    n_i >= min_packs_i * y_i            minimum order quantity (MOQ)
    n_i <= max_packs_i * y_i            demand / stock cap
    y_i <= z_s                          line implies supplier is used
    min_qty_p <= sum_i q_i <= max_qty_p per product, across all suppliers
    sum_i price_i q_i >= mov_s * z_s    supplier minimum order value
    sum_i price_i q_i + sum_s fee_s z_s <= budget

A second pass keeps the optimal profit and minimises the money spent, so that
between two equally profitable orders the cheaper one is returned.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_array


@dataclass(frozen=True)
class Offer:
    """A product offered by a supplier (one catalog line)."""

    supplier_id: int
    product_code: str
    unit_price: float
    moq: int = 1
    pack_size: int = 1
    stock: int | None = None


@dataclass(frozen=True)
class Need:
    """What the pharmacy wants to buy for one product."""

    product_code: str
    selling_price: float
    max_quantity: int
    min_quantity: int = 0


@dataclass(frozen=True)
class SupplierTerms:
    supplier_id: int
    min_order_value: float = 0.0
    shipping_fee: float = 0.0


@dataclass
class OrderLine:
    supplier_id: int
    product_code: str
    quantity: int
    unit_price: float
    selling_price: float

    @property
    def cost(self) -> float:
        return self.unit_price * self.quantity

    @property
    def revenue(self) -> float:
        return self.selling_price * self.quantity

    @property
    def profit(self) -> float:
        return self.revenue - self.cost


@dataclass
class SupplierSummary:
    supplier_id: int
    subtotal: float
    shipping_fee: float
    line_count: int


@dataclass
class OptimizationResult:
    status: str  # optimal | feasible | infeasible | empty | error
    message: str
    budget: float
    lines: list[OrderLine] = field(default_factory=list)
    suppliers: list[SupplierSummary] = field(default_factory=list)
    unmet: dict[str, int] = field(default_factory=dict)
    no_offer: list[str] = field(default_factory=list)
    minimum_budget: float | None = None

    @property
    def goods_cost(self) -> float:
        return sum(line.cost for line in self.lines)

    @property
    def shipping_fees(self) -> float:
        return sum(s.shipping_fee for s in self.suppliers)

    @property
    def total_cost(self) -> float:
        return self.goods_cost + self.shipping_fees

    @property
    def total_revenue(self) -> float:
        return sum(line.revenue for line in self.lines)

    @property
    def total_profit(self) -> float:
        return self.total_revenue - self.total_cost


@dataclass
class _Option:
    offer: Offer
    need: Need
    pack: int
    min_packs: int
    max_packs: int

    @property
    def pack_cost(self) -> float:
        return self.offer.unit_price * self.pack

    @property
    def pack_margin(self) -> float:
        return (self.need.selling_price - self.offer.unit_price) * self.pack


def _build_options(offers: list[Offer], needs: dict[str, Need]) -> list[_Option]:
    options = []
    for offer in offers:
        need = needs.get(offer.product_code)
        if need is None or need.max_quantity <= 0 or offer.unit_price < 0:
            continue
        pack = max(1, int(offer.pack_size))
        cap = need.max_quantity if offer.stock is None else min(need.max_quantity, offer.stock)
        max_packs = max(0, cap) // pack
        # The MOQ must be reached with whole packs.
        min_packs = max(1, math.ceil(max(1, offer.moq) / pack))
        if max_packs < min_packs:
            continue
        options.append(_Option(offer, need, pack, min_packs, max_packs))
    return options


class _Model:
    """Sparse MILP model shared by the different solve passes."""

    def __init__(self, options: list[_Option], needs: dict[str, Need], terms: dict[int, SupplierTerms]):
        self.options = options
        self.supplier_ids = sorted({o.offer.supplier_id for o in options})
        self.terms = {sid: terms.get(sid, SupplierTerms(sid)) for sid in self.supplier_ids}
        n_opt = len(options)
        n_sup = len(self.supplier_ids)
        self.n_opt, self.n_sup = n_opt, n_sup
        self.n_vars = 2 * n_opt + n_sup
        sup_index = {sid: k for k, sid in enumerate(self.supplier_ids)}

        def n_var(i: int) -> int:
            return i

        def y_var(i: int) -> int:
            return n_opt + i

        def z_var(s: int) -> int:
            return 2 * n_opt + s

        self.cost_vec = np.zeros(self.n_vars)
        self.profit_vec = np.zeros(self.n_vars)
        for i, o in enumerate(options):
            self.cost_vec[n_var(i)] = o.pack_cost
            self.profit_vec[n_var(i)] = o.pack_margin
        for k, sid in enumerate(self.supplier_ids):
            fee = self.terms[sid].shipping_fee
            self.cost_vec[z_var(k)] = fee
            self.profit_vec[z_var(k)] = -fee

        products = sorted({o.offer.product_code for o in options})
        prod_index = {code: k for k, code in enumerate(products)}
        mov_suppliers = [sid for sid in self.supplier_ids if self.terms[sid].min_order_value > 0]
        mov_index = {sid: k for k, sid in enumerate(mov_suppliers)}

        n_rows = 3 * n_opt + len(products) + len(mov_suppliers)
        A = lil_array((n_rows, self.n_vars))
        lb = np.full(n_rows, -np.inf)
        ub = np.full(n_rows, np.inf)

        row = 0
        for i, o in enumerate(options):
            k = sup_index[o.offer.supplier_id]
            # n_i - min_packs * y_i >= 0
            A[row, n_var(i)] = 1
            A[row, y_var(i)] = -o.min_packs
            lb[row] = 0
            row += 1
            # n_i - max_packs * y_i <= 0
            A[row, n_var(i)] = 1
            A[row, y_var(i)] = -o.max_packs
            ub[row] = 0
            row += 1
            # y_i - z_s <= 0
            A[row, y_var(i)] = 1
            A[row, z_var(k)] = -1
            ub[row] = 0
            row += 1

        base = row
        for i, o in enumerate(options):
            A[base + prod_index[o.offer.product_code], n_var(i)] = o.pack
        for code, k in prod_index.items():
            need = needs[code]
            lb[base + k] = max(0, need.min_quantity)
            ub[base + k] = need.max_quantity
        row = base + len(products)

        base = row
        for i, o in enumerate(options):
            sid = o.offer.supplier_id
            if sid in mov_index:
                A[base + mov_index[sid], n_var(i)] = o.pack_cost
        for sid, k in mov_index.items():
            A[base + k, z_var(sup_index[sid])] = -self.terms[sid].min_order_value
            lb[base + k] = 0

        self.A = A.tocsr()
        self.lb, self.ub = lb, ub

        var_lb = np.zeros(self.n_vars)
        var_ub = np.ones(self.n_vars)
        for i, o in enumerate(options):
            var_ub[n_var(i)] = o.max_packs
        self.bounds = Bounds(var_lb, var_ub)
        self.integrality = np.ones(self.n_vars)

    def solve(self, objective: np.ndarray, extra: list[LinearConstraint], time_limit: float):
        constraints = [LinearConstraint(self.A, self.lb, self.ub), *extra]
        return milp(
            objective,
            integrality=self.integrality,
            bounds=self.bounds,
            constraints=constraints,
            options={"time_limit": time_limit, "mip_rel_gap": 1e-7},
        )

    def budget_constraint(self, budget: float) -> LinearConstraint:
        return LinearConstraint(self.cost_vec.reshape(1, -1), -np.inf, budget)

    def quantities(self, x: np.ndarray) -> list[int]:
        return [int(round(x[i])) * o.pack for i, o in enumerate(self.options)]


def optimize_order(
    offers: list[Offer],
    needs: list[Need],
    budget: float,
    supplier_terms: list[SupplierTerms] | None = None,
    time_limit: float = 30.0,
) -> OptimizationResult:
    """Compute the most profitable order that fits in ``budget``."""
    need_map = {n.product_code: n for n in needs}
    terms = {t.supplier_id: t for t in (supplier_terms or [])}
    result = OptimizationResult(status="empty", message="", budget=budget)

    offered = {o.product_code for o in offers}
    result.no_offer = sorted(code for code in need_map if code not in offered)

    if budget < 0:
        result.status, result.message = "infeasible", "The budget must be positive."
        return result

    options = _build_options(offers, need_map)
    # Products that must be bought but cannot be sourced at all.
    sourceable: dict[str, int] = {}
    for o in options:
        sourceable[o.offer.product_code] = sourceable.get(o.offer.product_code, 0) + o.max_packs * o.pack
    impossible = [
        n.product_code for n in needs if n.min_quantity > 0 and sourceable.get(n.product_code, 0) < n.min_quantity
    ]
    if impossible:
        result.status = "infeasible"
        result.message = (
            "Mandatory minimum quantities cannot be sourced from the available catalogs "
            f"(stock / MOQ / pack size): {', '.join(sorted(impossible))}."
        )
        return result

    if not options:
        result.message = "No supplier offer matches the products to order."
        result.unmet = {n.product_code: n.max_quantity for n in needs if n.max_quantity > 0}
        return result

    model = _Model(options, need_map, terms)

    # Pass 1: maximise profit within budget.
    res = model.solve(-model.profit_vec, [model.budget_constraint(budget)], time_limit)
    if res.x is None:
        if res.status == 2:  # infeasible
            result.status = "infeasible"
            cheapest = model.solve(model.cost_vec, [], time_limit)
            if cheapest.x is not None:
                result.minimum_budget = round(float(model.cost_vec @ np.round(cheapest.x)), 2)
                result.message = (
                    "The budget is too small to cover the mandatory minimum quantities and supplier "
                    f"minimums. Minimum budget required: {result.minimum_budget:.2f}."
                )
            else:
                result.message = "The mandatory minimum quantities cannot be satisfied."
        else:
            result.status = "error"
            result.message = f"Solver stopped without a solution: {res.message}"
        return result

    proven_optimal = res.status == 0
    best_profit = float(model.profit_vec @ np.round(res.x))
    x = np.round(res.x)

    # Pass 2: same profit, lowest spend.
    tolerance = max(1e-6, 1e-9 * abs(best_profit))
    keep_profit = LinearConstraint(model.profit_vec.reshape(1, -1), best_profit - tolerance, np.inf)
    res2 = model.solve(model.cost_vec, [model.budget_constraint(budget), keep_profit], time_limit)
    if res2.x is not None:
        x = np.round(res2.x)

    quantities = model.quantities(x)
    supplier_lines: dict[int, list[OrderLine]] = {}
    ordered: dict[str, int] = {}
    for o, qty in zip(options, quantities):
        if qty <= 0:
            continue
        line = OrderLine(
            supplier_id=o.offer.supplier_id,
            product_code=o.offer.product_code,
            quantity=qty,
            unit_price=o.offer.unit_price,
            selling_price=o.need.selling_price,
        )
        result.lines.append(line)
        supplier_lines.setdefault(line.supplier_id, []).append(line)
        ordered[line.product_code] = ordered.get(line.product_code, 0) + qty

    result.lines.sort(key=lambda line: (line.supplier_id, line.product_code))
    for sid in sorted(supplier_lines):
        lines = supplier_lines[sid]
        result.suppliers.append(
            SupplierSummary(
                supplier_id=sid,
                subtotal=sum(line.cost for line in lines),
                shipping_fee=model.terms[sid].shipping_fee,
                line_count=len(lines),
            )
        )
    for n in needs:
        missing = n.max_quantity - ordered.get(n.product_code, 0)
        if missing > 0:
            result.unmet[n.product_code] = missing

    if not result.lines:
        result.status = "empty"
        result.message = "No profitable order fits in the budget."
    else:
        result.status = "optimal" if proven_optimal else "feasible"
        result.message = (
            "Optimal order found." if proven_optimal else "Time limit reached: best order found so far (may not be optimal)."
        )
    return result
