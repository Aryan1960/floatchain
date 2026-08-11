from __future__ import annotations

from fastapi import Request

from app.data.csfloat_client import CsFloatClient
from app.data.csgo_catalog import CsgoCatalog


def get_catalog(request: Request) -> CsgoCatalog:
    return request.app.state.catalog


def get_csfloat(request: Request) -> CsFloatClient:
    return request.app.state.csfloat
