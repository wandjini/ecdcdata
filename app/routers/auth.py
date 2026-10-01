from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..deps import DB, AdminCtx, Ctx
from ..models import Tenant, User
from ..schemas import LoginIn, MeOut, RegisterIn, TokenOut, UserIn, UserOut
from ..security import create_token, hash_password, verify_password

router = APIRouter(prefix="/api", tags=["auth"])


def _email_taken(db, email: str) -> bool:
    return db.scalar(select(User.id).where(User.email == email)) is not None


@router.post("/auth/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, db: DB):
    """Create a new pharmacy (tenant) together with its first administrator."""
    if _email_taken(db, data.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    tenant = Tenant(name=data.pharmacy_name.strip())
    db.add(tenant)
    db.flush()
    user = User(
        tenant_id=tenant.id,
        email=data.email,
        full_name=data.full_name,
        password_hash=hash_password(data.password),
        role="admin",
    )
    db.add(user)
    db.commit()
    return TokenOut(access_token=create_token(user.id, tenant.id))


@router.post("/auth/login", response_model=TokenOut)
def login(data: LoginIn, db: DB):
    user = db.scalar(select(User).where(User.email == data.email))
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return TokenOut(access_token=create_token(user.id, user.tenant_id))


@router.get("/auth/me", response_model=MeOut)
def me(ctx: Ctx):
    return MeOut(
        id=ctx.user.id,
        email=ctx.user.email,
        full_name=ctx.user.full_name,
        role=ctx.user.role,
        tenant_id=ctx.tenant_id,
        tenant_name=ctx.user.tenant.name,
    )


@router.get("/users", response_model=list[UserOut])
def list_users(ctx: Ctx):
    return ctx.db.scalars(select(User).where(User.tenant_id == ctx.tenant_id).order_by(User.email)).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(data: UserIn, ctx: AdminCtx):
    """Add a colleague to the current pharmacy."""
    if _email_taken(ctx.db, data.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(
        tenant_id=ctx.tenant_id,
        email=data.email,
        full_name=data.full_name,
        password_hash=hash_password(data.password),
        role=data.role,
    )
    ctx.db.add(user)
    ctx.db.commit()
    return user
