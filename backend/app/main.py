from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.data.csfloat_client import CsFloatClient
from app.data.csgo_catalog import CsgoCatalog
from app.routers import contracts, skins


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.catalog = CsgoCatalog(settings)
    app.state.csfloat = CsFloatClient(settings)
    await app.state.catalog.load()
    yield
    await app.state.csfloat.aclose()


app = FastAPI(title="FloatChain API", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(skins.router)
app.include_router(contracts.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
