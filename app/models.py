"""Database models.

Multitenancy uses a shared schema: every business table carries a
``tenant_id`` and every query issued by the API is filtered on the tenant of
the authenticated user (see ``app.deps``). A tenant is one pharmacy.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="member")  # admin | member

    tenant: Mapped[Tenant] = relationship()


class Product(Base):
    """Product referenced by the code shared between all supplier catalogs (CIP, EAN...)."""

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(300), default="")
    # Pharmacy-side data used by the optimizer.
    selling_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_quantity: Mapped[int] = mapped_column(Integer, default=0)  # expected demand
    min_quantity: Mapped[int] = mapped_column(Integer, default=0)  # mandatory quantity


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    min_order_value: Mapped[float] = mapped_column(Float, default=0.0)
    shipping_fee: Mapped[float] = mapped_column(Float, default=0.0)
    active: Mapped[bool] = mapped_column(default=True)

    items: Mapped[list["CatalogItem"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan", passive_deletes=True
    )


class CatalogItem(Base):
    __tablename__ = "catalog_items"
    __table_args__ = (UniqueConstraint("supplier_id", "product_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    unit_price: Mapped[float] = mapped_column(Float)
    moq: Mapped[int] = mapped_column(Integer, default=1)
    pack_size: Mapped[int] = mapped_column(Integer, default=1)
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    supplier: Mapped[Supplier] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class OrderPlan(Base):
    """A saved optimization result (snapshot, independent from later catalog changes)."""

    __tablename__ = "order_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    name: Mapped[str] = mapped_column(String(200), default="")
    budget: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20))
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    total_profit: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[dict] = mapped_column(JSON)
