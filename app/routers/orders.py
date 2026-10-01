"""Order optimization and saved order plans."""

import csv
import io

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..config import settings
from ..deps import Ctx, TenantContext
from ..models import CatalogItem, OrderPlan, Product, Supplier
from ..optimizer import Need, Offer, SupplierTerms, optimize_order
from ..schemas import (
    OptimizeIn,
    OptimizeOut,
    OrderLineOut,
    PlanSummary,
    SupplierSummaryOut,
    UnmetOut,
)

router = APIRouter(prefix="/api", tags=["orders"])


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def run_optimization(ctx: TenantContext, data: OptimizeIn) -> OptimizeOut:
    supplier_stmt = select(Supplier).where(Supplier.tenant_id == ctx.tenant_id, Supplier.active.is_(True))
    if data.supplier_ids is not None:
        supplier_stmt = supplier_stmt.where(Supplier.id.in_(data.supplier_ids))
    suppliers = {s.id: s for s in ctx.db.scalars(supplier_stmt).all()}

    products = ctx.db.scalars(
        select(Product).where(
            Product.tenant_id == ctx.tenant_id,
            Product.max_quantity > 0,
            Product.selling_price.is_not(None),
        )
    ).all()
    by_id = {p.id: p for p in products}
    by_code = {p.code: p for p in products}

    items = ctx.db.scalars(
        select(CatalogItem).where(
            CatalogItem.tenant_id == ctx.tenant_id,
            CatalogItem.supplier_id.in_(list(suppliers)),
            CatalogItem.product_id.in_(list(by_id)),
        )
    ).all()

    offers = [
        Offer(
            supplier_id=i.supplier_id,
            product_code=by_id[i.product_id].code,
            unit_price=i.unit_price,
            moq=i.moq,
            pack_size=i.pack_size,
            stock=i.stock,
        )
        for i in items
    ]
    needs = [
        Need(
            product_code=p.code,
            selling_price=p.selling_price,
            max_quantity=p.max_quantity,
            min_quantity=min(p.min_quantity, p.max_quantity),
        )
        for p in products
    ]
    terms = [
        SupplierTerms(supplier_id=s.id, min_order_value=s.min_order_value, shipping_fee=s.shipping_fee)
        for s in suppliers.values()
    ]
    result = optimize_order(offers, needs, data.budget, terms, time_limit=settings.solver_time_limit)

    return OptimizeOut(
        status=result.status,
        message=result.message,
        budget=_money(data.budget),
        goods_cost=_money(result.goods_cost),
        shipping_fees=_money(result.shipping_fees),
        total_cost=_money(result.total_cost),
        total_revenue=_money(result.total_revenue),
        total_profit=_money(result.total_profit),
        budget_left=_money(data.budget - result.total_cost),
        minimum_budget=result.minimum_budget,
        lines=[
            OrderLineOut(
                supplier_id=line.supplier_id,
                supplier_name=suppliers[line.supplier_id].name,
                product_code=line.product_code,
                product_name=by_code[line.product_code].name,
                quantity=line.quantity,
                unit_price=line.unit_price,
                selling_price=line.selling_price,
                cost=_money(line.cost),
                profit=_money(line.profit),
            )
            for line in result.lines
        ],
        suppliers=[
            SupplierSummaryOut(
                supplier_id=s.supplier_id,
                supplier_name=suppliers[s.supplier_id].name,
                subtotal=_money(s.subtotal),
                shipping_fee=_money(s.shipping_fee),
                line_count=s.line_count,
            )
            for s in result.suppliers
        ],
        unmet=[
            UnmetOut(product_code=code, product_name=by_code[code].name, missing_quantity=qty)
            for code, qty in sorted(result.unmet.items())
        ],
        no_offer=result.no_offer,
    )


@router.post("/optimize", response_model=OptimizeOut)
def optimize(data: OptimizeIn, ctx: Ctx):
    """Compute the most profitable order for the given budget."""
    out = run_optimization(ctx, data)
    if data.save and out.lines:
        plan = OrderPlan(
            tenant_id=ctx.tenant_id,
            created_by=ctx.user.id,
            name=data.name or f"Order - budget {data.budget:.2f}",
            budget=data.budget,
            status=out.status,
            total_cost=out.total_cost,
            total_profit=out.total_profit,
            result=out.model_dump(mode="json"),
        )
        ctx.db.add(plan)
        ctx.db.commit()
        out.plan_id = plan.id
    return out


def _get_plan(ctx: TenantContext, plan_id: int) -> OrderPlan:
    plan = ctx.db.scalar(select(OrderPlan).where(OrderPlan.id == plan_id, OrderPlan.tenant_id == ctx.tenant_id))
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order plan not found")
    return plan


@router.get("/orders", response_model=list[PlanSummary])
def list_plans(ctx: Ctx):
    return ctx.db.scalars(
        select(OrderPlan).where(OrderPlan.tenant_id == ctx.tenant_id).order_by(OrderPlan.created_at.desc())
    ).all()


@router.get("/orders/{plan_id}", response_model=OptimizeOut)
def get_plan(plan_id: int, ctx: Ctx):
    plan = _get_plan(ctx, plan_id)
    return OptimizeOut.model_validate({**plan.result, "plan_id": plan.id})


@router.delete("/orders/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_plan(plan_id: int, ctx: Ctx):
    ctx.db.delete(_get_plan(ctx, plan_id))
    ctx.db.commit()


@router.get("/orders/{plan_id}/export.csv")
def export_plan(plan_id: int, ctx: Ctx):
    """Purchase orders as CSV (one line per product, grouped by supplier)."""
    plan = _get_plan(ctx, plan_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["supplier", "code", "name", "quantity", "unit_price", "cost"])
    for line in plan.result["lines"]:
        writer.writerow(
            [line["supplier_name"], line["product_code"], line["product_name"],
             line["quantity"], line["unit_price"], line["cost"]]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="order-{plan.id}.csv"'},
    )
