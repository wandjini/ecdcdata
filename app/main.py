from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import Base, engine
from .routers import auth, orders, products, suppliers

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(
    title="PharmaOpt",
    description="Multitenant order optimizer for pharmacies: maximise profit within a budget "
    "across several supplier catalogs, honouring minimum order quantities.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(auth.router)
app.include_router(suppliers.router)
app.include_router(products.router)
app.include_router(orders.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok"}
