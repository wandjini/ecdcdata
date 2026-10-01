"""Suppliers and their catalogs."""

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..csv_import import parse_int, parse_number, read_rows
from ..pdf_import import PdfImportError, read_pdf_rows
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


def _read_catalog_file(filename: str, content: bytes) -> tuple[list[dict[str, str]], str, int]:
    """Rows with canonical keys, the label used in error messages and the first row number."""
    if content[:5] == b"%PDF-" or filename.lower().endswith(".pdf"):
        try:
            return read_pdf_rows(content), "row", 1
        except PdfImportError as exc:
            raise HTTPException(422, str(exc))
    return read_rows(content), "line", 2


def _catalog_item_from_row(row: dict[str, str]) -> CatalogItemIn:
    price = parse_number(row.get("unit_price"))
    if price is None:
        raise ValueError("missing unit_price")
    discount = parse_number(row.get("discount")) or 0.0
    if not 0 <= discount < 100:
        raise ValueError(f"invalid discount {row.get('discount')!r}")
    return CatalogItemIn(
        code=(row.get("code") or "").strip(),
        name=(row.get("name") or "").strip(),
        unit_price=round(price * (1 - discount / 100), 4),
        moq=parse_int(row.get("moq")) or 1,
        pack_size=parse_int(row.get("pack_size")) or 1,
        stock=parse_int(row.get("stock")),
    )


@router.post("/{supplier_id}/catalog/import", response_model=ImportReport)
async def import_catalog(
    supplier_id: int,
    ctx: Ctx,
    file: UploadFile = File(...),
    replace: bool = False,
    dry_run: bool = False,
):
    """Import a catalog from a CSV or PDF file.

    Recognised columns (French or English headers): code, name, unit_price, discount (%),
    moq, pack_size, stock. A discount is applied to the unit price.

    With ``dry_run=true`` nothing is saved and the parsed lines are returned in ``preview``,
    so they can be checked (especially for PDF files) before importing.
    With ``replace=true`` the items absent from the file are removed from the catalog.
    """
    supplier = _get_supplier(ctx, supplier_id)
    rows, label, first = _read_catalog_file(file.filename or "", await file.read())
    existing = {item.product.code for item in supplier.items}
    created = updated = 0
    errors: list[str] = []
    preview: list[CatalogItemIn] = []
    seen_codes: set[str] = set()
    for number, row in enumerate(rows, start=first):
        try:
            data = _catalog_item_from_row(row)
        except ValueError as exc:  # includes pydantic ValidationError
            message = str(exc).splitlines()
            errors.append(f"{label} {number}: {message[-1].strip() if len(message) > 1 else message[0]}")
            continue
        if dry_run:
            preview.append(data)
            is_new = data.code not in existing and data.code not in seen_codes
        else:
            is_new = _upsert_item(ctx, supplier, data)[1]
        created += is_new
        updated += not is_new
        seen_codes.add(data.code)
    if dry_run:
        ctx.db.rollback()
        return ImportReport(created=created, updated=updated, errors=errors, preview=preview, dry_run=True)
    if replace and not errors:
        for item in list(supplier.items):
            if item.product.code not in seen_codes:
                ctx.db.delete(item)
    ctx.db.commit()
    return ImportReport(created=created, updated=updated, errors=errors)
