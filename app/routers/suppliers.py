"""Suppliers and their catalogs."""

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..csv_import import parse_int, parse_number, read_rows
from ..deps import Ctx, TenantContext
from ..models import CatalogItem, Supplier
from ..schemas import CatalogItemIn, CatalogItemOut, ImportReport, SupplierIn, SupplierOut
from .products import get_or_create_product

router = APIRouter(prefix="/api/suppliers", tags=["suppliers"])


def _get_supplier(ctx: TenantContext, supplier_id: int) -> Supplier:
    supplier = ctx.db.scalar(
        select(Supplier).where(Supplier.id == supplier_id, Supplier.tenant_id == ctx.tenant_id)
    )
    if supplier is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found")
    return supplier


def _supplier_out(supplier: Supplier, count: int) -> SupplierOut:
    return SupplierOut.model_validate(supplier).model_copy(update={"item_count": count})


def _item_out(item: CatalogItem) -> CatalogItemOut:
    return CatalogItemOut(
        id=item.id,
        code=item.product.code,
        name=item.product.name,
        unit_price=item.unit_price,
        moq=item.moq,
        pack_size=item.pack_size,
        stock=item.stock,
        updated_at=item.updated_at,
    )


def _upsert_item(ctx: TenantContext, supplier: Supplier, data: CatalogItemIn) -> tuple[CatalogItem, bool]:
    product, _ = get_or_create_product(ctx, data.code, data.name)
    item = ctx.db.scalar(
        select(CatalogItem).where(CatalogItem.supplier_id == supplier.id, CatalogItem.product_id == product.id)
    )
    created = item is None
    if created:
        item = CatalogItem(tenant_id=ctx.tenant_id, supplier_id=supplier.id, product_id=product.id)
        ctx.db.add(item)
    item.unit_price = data.unit_price
    item.moq = data.moq
    item.pack_size = data.pack_size
    item.stock = data.stock
    ctx.db.flush()
    return item, created


@router.get("", response_model=list[SupplierOut])
def list_suppliers(ctx: Ctx):
    counts = dict(
        ctx.db.execute(
            select(CatalogItem.supplier_id, func.count())
            .where(CatalogItem.tenant_id == ctx.tenant_id)
            .group_by(CatalogItem.supplier_id)
        ).all()
    )
    suppliers = ctx.db.scalars(
        select(Supplier).where(Supplier.tenant_id == ctx.tenant_id).order_by(Supplier.name)
    ).all()
    return [_supplier_out(s, counts.get(s.id, 0)) for s in suppliers]


@router.post("", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
def create_supplier(data: SupplierIn, ctx: Ctx):
    supplier = Supplier(tenant_id=ctx.tenant_id, **data.model_dump())
    ctx.db.add(supplier)
    try:
        ctx.db.commit()
    except IntegrityError:
        ctx.db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A supplier with this name already exists")
    return _supplier_out(supplier, 0)


@router.put("/{supplier_id}", response_model=SupplierOut)
def update_supplier(supplier_id: int, data: SupplierIn, ctx: Ctx):
    supplier = _get_supplier(ctx, supplier_id)
    for key, value in data.model_dump().items():
        setattr(supplier, key, value)
    try:
        ctx.db.commit()
    except IntegrityError:
        ctx.db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A supplier with this name already exists")
    count = ctx.db.scalar(select(func.count()).where(CatalogItem.supplier_id == supplier.id))
    return _supplier_out(supplier, count or 0)


@router.delete("/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_supplier(supplier_id: int, ctx: Ctx):
    ctx.db.delete(_get_supplier(ctx, supplier_id))
    ctx.db.commit()


@router.get("/{supplier_id}/catalog", response_model=list[CatalogItemOut])
def list_catalog(supplier_id: int, ctx: Ctx):
    supplier = _get_supplier(ctx, supplier_id)
    items = ctx.db.scalars(
        select(CatalogItem)
        .where(CatalogItem.supplier_id == supplier.id)
        .options(selectinload(CatalogItem.product))
    ).all()
    return sorted((_item_out(i) for i in items), key=lambda i: i.code)


@router.put("/{supplier_id}/catalog", response_model=CatalogItemOut)
def upsert_catalog_item(supplier_id: int, data: CatalogItemIn, ctx: Ctx):
    supplier = _get_supplier(ctx, supplier_id)
    item, _ = _upsert_item(ctx, supplier, data)
    ctx.db.commit()
    return _item_out(item)


@router.delete("/{supplier_id}/catalog/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_catalog_item(supplier_id: int, item_id: int, ctx: Ctx):
    supplier = _get_supplier(ctx, supplier_id)
    item = ctx.db.scalar(
        select(CatalogItem).where(CatalogItem.id == item_id, CatalogItem.supplier_id == supplier.id)
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Catalog item not found")
    ctx.db.delete(item)
    ctx.db.commit()


@router.post("/{supplier_id}/catalog/import", response_model=ImportReport)
async def import_catalog(supplier_id: int, ctx: Ctx, file: UploadFile = File(...), replace: bool = False):
    """CSV columns: code, name, unit_price, moq, pack_size, stock.

    With ``replace=true`` the items absent from the file are removed from the catalog.
    """
    supplier = _get_supplier(ctx, supplier_id)
    rows = read_rows(await file.read())
    created = updated = 0
    errors: list[str] = []
    seen_codes: set[str] = set()
    for line_no, row in enumerate(rows, start=2):
        try:
            price = parse_number(row.get("unit_price"))
            if price is None:
                raise ValueError("missing unit_price")
            data = CatalogItemIn(
                code=(row.get("code") or "").strip(),
                name=(row.get("name") or "").strip(),
                unit_price=price,
                moq=parse_int(row.get("moq")) or 1,
                pack_size=parse_int(row.get("pack_size")) or 1,
                stock=parse_int(row.get("stock")),
            )
        except ValueError as exc:  # includes pydantic ValidationError
            errors.append(f"line {line_no}: {str(exc).splitlines()[0]}")
            continue
        if _upsert_item(ctx, supplier, data)[1]:
            created += 1
        else:
            updated += 1
        seen_codes.add(data.code)
    if replace and not errors:
        for item in list(supplier.items):
            if item.product.code not in seen_codes:
                ctx.db.delete(item)
    ctx.db.commit()
    return ImportReport(created=created, updated=updated, errors=errors)
