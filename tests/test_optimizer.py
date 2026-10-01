import itertools
import random

import pytest

from app.optimizer import Need, Offer, SupplierTerms, optimize_order


def brute_force(offers, needs, budget, terms=()):
    """Exhaustive search over every feasible quantity combination (tiny instances only)."""
    need_map = {n.product_code: n for n in needs}
    term_map = {t.supplier_id: t for t in terms}
    choices = []
    for o in offers:
        n = need_map[o.product_code]
        cap = n.max_quantity if o.stock is None else min(n.max_quantity, o.stock)
        qs = [0] + [q for q in range(o.pack_size, cap + 1, o.pack_size) if q >= o.moq]
        choices.append(qs)
    best = None
    for combo in itertools.product(*choices):
        per_product, per_supplier = {}, {}
        for o, q in zip(offers, combo):
            per_product[o.product_code] = per_product.get(o.product_code, 0) + q
            if q:
                per_supplier[o.supplier_id] = per_supplier.get(o.supplier_id, 0) + q * o.unit_price
        if any(not (n.min_quantity <= per_product.get(c, 0) <= n.max_quantity) for c, n in need_map.items()):
            continue
        fees = 0.0
        ok = True
        for sid, value in per_supplier.items():
            t = term_map.get(sid, SupplierTerms(sid))
            if value < t.min_order_value - 1e-9:
                ok = False
            fees += t.shipping_fee
        if not ok:
            continue
        cost = sum(per_supplier.values()) + fees
        if cost > budget + 1e-9:
            continue
        profit = sum((need_map[o.product_code].selling_price - o.unit_price) * q for o, q in zip(offers, combo)) - fees
        if best is None or profit > best + 1e-9:
            best = profit
    return best


def test_picks_cheapest_supplier_for_shared_code():
    offers = [Offer(1, "A", 6.0), Offer(2, "A", 5.0)]
    res = optimize_order(offers, [Need("A", 10.0, 10)], budget=1000)
    assert res.status == "optimal"
    assert [(l.supplier_id, l.quantity) for l in res.lines] == [(2, 10)]
    assert res.total_profit == pytest.approx(50)


def test_budget_limits_order_and_prioritises_margin():
    offers = [Offer(1, "LOW", 9.0), Offer(1, "HIGH", 5.0)]
    needs = [Need("LOW", 10.0, 10), Need("HIGH", 10.0, 10)]
    res = optimize_order(offers, needs, budget=50)
    assert {l.product_code: l.quantity for l in res.lines} == {"HIGH": 10}
    assert res.total_cost <= 50
    assert res.unmet == {"LOW": 10}


def test_respects_moq_and_falls_back_to_other_supplier():
    # Supplier 1 is cheaper but its MOQ (50) exceeds demand; supplier 2 is used instead.
    offers = [Offer(1, "A", 4.0, moq=50), Offer(2, "A", 5.0)]
    res = optimize_order(offers, [Need("A", 8.0, 20)], budget=1000)
    assert [(l.supplier_id, l.quantity) for l in res.lines] == [(2, 20)]


def test_moq_with_budget_switches_supplier():
    # MOQ of 10 at 4.0 costs 40 > budget 30, so buy 6 units at 5.0 elsewhere.
    offers = [Offer(1, "A", 4.0, moq=10), Offer(2, "A", 5.0)]
    res = optimize_order(offers, [Need("A", 8.0, 20)], budget=30)
    assert [(l.supplier_id, l.quantity) for l in res.lines] == [(2, 6)]


def test_pack_size_multiples_and_stock():
    offers = [Offer(1, "A", 1.0, pack_size=6, stock=15)]
    res = optimize_order(offers, [Need("A", 2.0, 100)], budget=1000)
    assert res.lines[0].quantity == 12  # two full packs, stock allows 15


def test_negative_margin_products_are_not_bought_unless_mandatory():
    offers = [Offer(1, "LOSS", 12.0)]
    res = optimize_order(offers, [Need("LOSS", 10.0, 5)], budget=100)
    assert res.status == "empty" and not res.lines
    res = optimize_order(offers, [Need("LOSS", 10.0, 5, min_quantity=2)], budget=100)
    assert [(l.product_code, l.quantity) for l in res.lines] == [("LOSS", 2)]


def test_supplier_minimum_order_value_and_shipping_fee():
    offers = [Offer(1, "A", 4.0), Offer(2, "A", 4.5)]
    terms = [SupplierTerms(1, min_order_value=100), SupplierTerms(2, shipping_fee=0)]
    # Demand 10 → 40 at supplier 1 is below its 100 minimum, so supplier 2 wins.
    res = optimize_order(offers, [Need("A", 6.0, 10)], budget=1000, supplier_terms=terms)
    assert [(l.supplier_id, l.quantity) for l in res.lines] == [(2, 10)]
    # Shipping fee larger than the gain makes ordering pointless.
    res = optimize_order([Offer(1, "A", 4.0)], [Need("A", 5.0, 3)], 100, [SupplierTerms(1, shipping_fee=10)])
    assert res.status == "empty"


def test_infeasible_budget_reports_minimum_budget():
    offers = [Offer(1, "A", 5.0, moq=10)]
    res = optimize_order(offers, [Need("A", 8.0, 20, min_quantity=1)], budget=20)
    assert res.status == "infeasible"
    assert res.minimum_budget == pytest.approx(50)


def test_mandatory_product_without_offer_is_infeasible():
    res = optimize_order([], [Need("A", 8.0, 20, min_quantity=1)], budget=20)
    assert res.status == "infeasible"
    assert "A" in res.message


def test_ties_prefer_lower_spend():
    # A and B both earn 1, but only one fits in the budget: the cheaper one must be chosen.
    offers = [Offer(1, "A", 1.0), Offer(2, "B", 5.0)]
    needs = [Need("A", 2.0, 1), Need("B", 6.0, 1)]
    res = optimize_order(offers, needs, budget=5)
    assert res.total_profit == pytest.approx(1)
    assert [(l.product_code, l.quantity) for l in res.lines] == [("A", 1)]


@pytest.mark.parametrize("seed", range(25))
def test_matches_brute_force_on_random_instances(seed):
    rng = random.Random(seed)
    codes = ["A", "B", "C"]
    needs = [
        Need(c, round(rng.uniform(3, 12), 2), rng.randint(1, 8), min_quantity=rng.choice([0, 0, 0, 1]))
        for c in codes
    ]
    offers = []
    for c in codes:
        for sid in rng.sample([1, 2], rng.randint(1, 2)):
            offers.append(
                Offer(sid, c, round(rng.uniform(2, 11), 2), moq=rng.randint(1, 4),
                      pack_size=rng.choice([1, 1, 2, 3]), stock=rng.choice([None, rng.randint(2, 10)]))
            )
    terms = [SupplierTerms(1, min_order_value=rng.choice([0, 15])), SupplierTerms(2, shipping_fee=rng.choice([0, 2]))]
    budget = rng.uniform(10, 60)
    expected = brute_force(offers, needs, budget, terms)
    res = optimize_order(offers, needs, budget, terms)
    if expected is None:
        assert res.status == "infeasible"
    else:
        assert res.status in ("optimal", "empty")
        assert res.total_profit == pytest.approx(expected, abs=1e-6)
        assert res.total_cost <= budget + 1e-6
