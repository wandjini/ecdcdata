"""Products and the pharmacy's needs (selling price, expected demand)."""

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from ..csv_import import parse_int, parse_number, read_rows
from ..deps import Ctx, TenantContext
from ..models import CatalogItem, Product
from ..schemas import ImportReport, OfferOut, ProductComparison, ProductIn, ProductOut

router = APIRouter(prefix="/api/products", tags=["products"])


def get_or_create_product(ctx: TenantContext, code: str, name: str = "") -> tuple[Product, bool]:
    code = code.strip()
    product = ctx.db.scalar(select(Product).where(Product.tenant_id == ctx.tenant_id, Product.code == code))
    if product is not None:
        if name and not product.name:
            product.name = name
        return product, False
    product = Product(tenant_id=ctx.tenant_id, code=code, name=name)
    ctx.db.add(product)
    ctx.db.flush()
    return product, True


def _get_product(ctx: TenantContext, code: str) -> Product:
    product = ctx.db.scalar(select(Product).where(Product.tenant_id == ctx.tenant_id, Product.code == code))
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return product


@router.get("", response_model=list[ProductOut])
def list_products(ctx: Ctx, q: str = "", needed_only: bool = False):
    stmt = select(Product).where(Product.tenant_id == ctx.tenant_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Product.code.ilike(like), Product.name.ilike(like)))
    if needed_only:
        stmt = stmt.where(Product.max_quantity > 0)
    return ctx.db.scalars(stmt.order_by(Product.code)).all()


@router.put("", response_model=ProductOut)
def upsert_product(data: ProductIn, ctx: Ctx):
    if data.min_quantity > data.max_quantity:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "min_quantity cannot exceed max_quantity")
    product, _ = get_or_create_product(ctx, data.code, data.name)
    if data.name:
        product.name = data.name
    product.selling_price = data.selling_price
    product.max_quantity = data.max_quantity
    product.min_quantity = data.min_quantity
    ctx.db.commit()
    return product


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(code: str, ctx: Ctx):
    ctx.db.delete(_get_product(ctx, code))
    ctx.db.commit()


@router.post("/import", response_model=ImportReport)
async def import_products(ctx: Ctx, file: UploadFile = File(...)):
    """CSV columns: code, name, selling_price, max_quantity, min_quantity."""
    rows = read_rows(await file.read())
    created = updated = 0
    errors: list[str] = []
    for line_no, row in enumerate(rows, start=2):
        code = (row.get("code") or "").strip()
        if not code:
            errors.append(f"line {line_no}: missing product code")
            continue
        try:
            selling_price = parse_number(row.get("selling_price"))
            max_quantity = parse_int(row.get("max_quantity")) or 0
            min_quantity = parse_int(row.get("min_quantity")) or 0
        except ValueError as exc:
            errors.append(f"line {line_no}: {exc}")
            continue
        if min_quantity > max_quantity or max_quantity < 0 or (selling_price is not None and selling_price < 0):
            errors.append(f"line {line_no}: invalid quantities or price")
            continue
        product, is_new = get_or_create_product(ctx, code, (row.get("name") or "").strip())
        if row.get("name"):
            product.name = row["name"].strip()
        product.selling_price = selling_price
        product.max_quantity = max_quantity
        product.min_quantity = min_quantity
        created += is_new
        updated += not is_new
    ctx.db.commit()
    return ImportReport(created=created, updated=updated, errors=errors)


@router.get("/comparison", response_model=list[ProductComparison])
def compare_offers(ctx: Ctx, q: str = "", needed_only: bool = True):
    """Side-by-side supplier prices for each product sharing the same code."""
    stmt = select(Product).where(Product.tenant_id == ctx.tenant_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Product.code.ilike(like), Product.name.ilike(like)))
    if needed_only:
        stmt = stmt.where(Product.max_quantity > 0)
    products = ctx.db.scalars(stmt.order_by(Product.code)).all()
    items = ctx.db.scalars(
        select(CatalogItem)
        .where(CatalogItem.tenant_id == ctx.tenant_id, CatalogItem.product_id.in_([p.id for p in products]))
        .options(selectinload(CatalogItem.supplier))
    ).all()
    by_product: dict[int, list[CatalogItem]] = {}
    for item in items:
        by_product.setdefault(item.product_id, []).append(item)

    out = []
    for p in products:
        offers = [
            OfferOut(
                supplier_id=i.supplier_id,
                supplier_name=i.supplier.name,
                unit_price=i.unit_price,
                moq=i.moq,
                pack_size=i.pack_size,
                stock=i.stock,
                margin=None if p.selling_price is None else round(p.selling_price - i.unit_price, 4),
            )
            for i in sorted(by_product.get(p.id, []), key=lambda i: i.unit_price)
            if i.supplier.active
        ]
        out.append(
            ProductComparison.model_validate(
                {**ProductOut.model_validate(p).model_dump(), "offers": offers,
                 "best_price": offers[0].unit_price if offers else None}
            )
        )
    return out
