"""Pydantic request models for the HTTP API. Responses are returned as plain
dataclasses from app.domain.models — FastAPI's jsonable_encoder serializes
those directly, so we don't duplicate every field into a second schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContractInputSchema(BaseModel):
    skin_name: str
    raw_float: float = Field(ge=0.0, le=1.0)
    price: float = Field(ge=0.0)
    stattrak: bool = False


class ContractRequest(BaseModel):
    inputs: list[ContractInputSchema] = Field(min_length=5, max_length=10)
    monte_carlo_runs: int = Field(default=1000, ge=100, le=20000)


class ChainRequest(BaseModel):
    inputs: list[ContractInputSchema] = Field(min_length=5, max_length=10)
    beam_width: int = Field(default=3, ge=1, le=5)
    max_depth: int = Field(default=2, ge=1, le=3)
    materiality_threshold_pct: float = Field(default=5.0, ge=0.0, le=100.0)
