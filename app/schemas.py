from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

Email = Annotated[
    str,
    Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    AfterValidator(lambda value: value.strip().lower()),
]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Auth -----------------------------------------------------------------
class RegisterIn(BaseModel):
    pharmacy_name: str = Field(min_length=1, max_length=200)
    email: Email
    password: str = Field(min_length=8)
    full_name: str = ""


class LoginIn(BaseModel):
    email: Email
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserIn(BaseModel):
    email: Email
    password: str = Field(min_length=8)
    full_name: str = ""
    role: str = Field("member", pattern="^(admin|member)$")


class UserOut(ORM):
    id: int
    email: str
    full_name: str
    role: str
    tenant_id: int


class MeOut(UserOut):
    tenant_name: str


# --- Products (pharmacy needs) -------------------------------------------
class ProductIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = ""
    selling_price: float | None = Field(None, ge=0)
    max_quantity: int = Field(0, ge=0)
    min_quantity: int = Field(0, ge=0)


class ProductOut(ORM):
    id: int
    code: str
    name: str
    selling_price: float | None
    max_quantity: int
    min_quantity: int


class OfferOut(BaseModel):
    supplier_id: int
    supplier_name: str
    unit_price: float
    moq: int
    pack_size: int
    stock: int | None
    margin: float | None


class ProductComparison(ProductOut):
    offers: list[OfferOut]
    best_price: float | None


# --- Suppliers & catalogs -------------------------------------------------
class SupplierIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    min_order_value: float = Field(0, ge=0)
    shipping_fee: float = Field(0, ge=0)
    active: bool = True


class SupplierOut(ORM):
    id: int
    name: str
    min_order_value: float
    shipping_fee: float
    active: bool
    item_count: int = 0


class CatalogItemIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = ""
    unit_price: float = Field(ge=0)
    moq: int = Field(1, ge=1)
    pack_size: int = Field(1, ge=1)
    stock: int | None = Field(None, ge=0)


class CatalogItemOut(BaseModel):
    id: int
    code: str
    name: str
    unit_price: float
    moq: int
    pack_size: int
    stock: int | None
    updated_at: datetime


class ImportReport(BaseModel):
    created: int
    updated: int
    errors: list[str]


# --- Optimization -----------------------------------------------------------
class OptimizeIn(BaseModel):
    budget: float = Field(ge=0)
    supplier_ids: list[int] | None = None  # restrict to these suppliers
    save: bool = False
    name: str = ""


class OrderLineOut(BaseModel):
    supplier_id: int
    supplier_name: str
    product_code: str
    product_name: str
    quantity: int
    unit_price: float
    selling_price: float
    cost: float
    profit: float


class SupplierSummaryOut(BaseModel):
    supplier_id: int
    supplier_name: str
    subtotal: float
    shipping_fee: float
    line_count: int


class UnmetOut(BaseModel):
    product_code: str
    product_name: str
    missing_quantity: int


class OptimizeOut(BaseModel):
    plan_id: int | None = None
    status: str
    message: str
    budget: float
    goods_cost: float
    shipping_fees: float
    total_cost: float
    total_revenue: float
    total_profit: float
    budget_left: float
    minimum_budget: float | None
    lines: list[OrderLineOut]
    suppliers: list[SupplierSummaryOut]
    unmet: list[UnmetOut]
    no_offer: list[str]


class PlanSummary(ORM):
    id: int
    name: str
    created_at: datetime
    budget: float
    status: str
    total_cost: float
    total_profit: float
