from __future__ import annotations

from fastapi import Request

from app.data.csfloat_client import CsFloatClient
from app.data.csgo_catalog import CsgoCatalog
from app.data.pricing_store import PricingStore


def get_catalog(request: Request) -> CsgoCatalog:
    return request.app.state.catalog


def get_csfloat(request: Request) -> CsFloatClient:
    return request.app.state.csfloat


def get_pricing_store(request: Request) -> PricingStore:
    return request.app.state.pricing_store
