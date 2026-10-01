from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)

DB = Annotated[Session, Depends(get_db)]


@dataclass
class TenantContext:
    """The authenticated user and the tenant every query must be scoped to."""

    user: User
    db: Session

    @property
    def tenant_id(self) -> int:
        return self.user.tenant_id


def get_context(
    db: DB, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> TenantContext:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"}
    )
    if credentials is None:
        raise unauthorized
    try:
        payload = decode_token(credentials.credentials)
        user_id = int(payload["sub"])
        tenant_id = int(payload["tid"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    user = db.get(User, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise unauthorized
    return TenantContext(user=user, db=db)


Ctx = Annotated[TenantContext, Depends(get_context)]


def require_admin(ctx: Ctx) -> TenantContext:
    if ctx.user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator role required")
    return ctx


AdminCtx = Annotated[TenantContext, Depends(require_admin)]
