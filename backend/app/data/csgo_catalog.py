"""Skin/collection/float-cap catalog sourced from the free, no-auth
ByMykel/CSGO-API dataset (github.com/ByMykel/CSGO-API), cached to disk.

Known limitation: gold-tier items (knives/gloves) are listed against
`crates` (case names) rather than `collections` in this dataset, so
Covert -> Gold contract matching isn't wired up yet — this catalog only
resolves collections for normal weapon-skin tiers (Consumer through
Covert). Souvenir items are parsed but excluded everywhere trade-up
eligibility is checked, since souvenirs can't be used in trade-ups.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from app.config import Settings
from app.domain.rarity import TIER_INDEX
from app.domain.models import SkinCatalogEntry

_CACHE_FILENAME = "skins.json"


class CsgoCatalog:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._cache_path = Path(settings.cache_dir) / _CACHE_FILENAME
        self._skins: list[SkinCatalogEntry] | None = None
        self._by_name: dict[str, SkinCatalogEntry] = {}

    async def load(self, *, force_refresh: bool = False) -> list[SkinCatalogEntry]:
        if self._skins is not None and not force_refresh:
            return self._skins

        raw = await self._get_raw(force_refresh=force_refresh)
        self._skins = [entry for entry in (_parse_skin(item) for item in raw) if entry]
        self._by_name = {entry.name: entry for entry in self._skins}
        return self._skins

    def get(self, name: str) -> SkinCatalogEntry | None:
        return self._by_name.get(name)

    def search(self, query: str, limit: int = 20) -> list[SkinCatalogEntry]:
        q = query.lower().strip()
        if not q:
            return []
        matches = [s for s in (self._skins or []) if q in s.name.lower()]
        return matches[:limit]

    async def _get_raw(self, *, force_refresh: bool) -> list[dict]:
        if not force_refresh and self._cache_path.exists():
            age = time.time() - self._cache_path.stat().st_mtime
            if age < self._settings.catalog_cache_ttl_seconds:
                return json.loads(self._cache_path.read_text())

        url = f"{self._settings.csgo_api_base}/skins.json"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(data))
        return data


def _parse_skin(item: dict) -> SkinCatalogEntry | None:
    rarity = item.get("rarity") or {}
    rarity_id = rarity.get("id")
    min_float = item.get("min_float")
    max_float = item.get("max_float")

    if rarity_id not in TIER_INDEX:
        return None
    if min_float is None or max_float is None:
        return None

    collections = tuple(
        c["name"] for c in (item.get("collections") or []) if c.get("name")
    )

    return SkinCatalogEntry(
        name=item["name"],
        rarity_id=rarity_id,
        min_float=float(min_float),
        max_float=float(max_float),
        stattrak=bool(item.get("stattrak", False)),
        souvenir=bool(item.get("souvenir", False)),
        collections=collections,
        paint_index=str(item.get("paint_index") or ""),
    )
