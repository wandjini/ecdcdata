import os
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./pharmaopt.db")
    # Without SECRET_KEY a random key is generated: tokens are then invalidated on restart.
    secret_key: str = os.getenv("SECRET_KEY") or secrets.token_urlsafe(48)
    token_ttl_minutes: int = int(os.getenv("TOKEN_TTL_MINUTES", "720"))
    solver_time_limit: float = float(os.getenv("SOLVER_TIME_LIMIT", "30"))
    password_iterations: int = int(os.getenv("PASSWORD_ITERATIONS", "210000"))


settings = Settings()
