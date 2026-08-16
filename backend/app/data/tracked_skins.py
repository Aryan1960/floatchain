"""Curated set of liquid, well-known skins the Phase 2 snapshot collector
polls. Deliberately small (per-sweep cost = ~1-2 CSFloat calls per skin,
using paint_index + the skin's own float range rather than one call per wear
tier -- see CsFloatClient.get_listings), so a full sweep stays comfortably
within rate limits and each skin accumulates distinct listings fast enough
to be useful within days rather than weeks.

Non-StatTrak only for now -- doubling to StatTrak variants would double call
volume for a first pass; revisit once the normal-variant pipeline is proven
out. Names must match SkinCatalogEntry.name exactly (verified against the
live catalog in scripts/snapshot.py at startup, not assumed here).
"""

from __future__ import annotations

TRACKED_SKINS: tuple[str, ...] = (
    "AK-47 | Redline",
    "AWP | Asiimov",
    "M4A4 | Asiimov",
    "AK-47 | Vulcan",
    "AK-47 | Bloodsport",
    "M4A1-S | Hyper Beast",
    "AWP | Neo-Noir",
    "Desert Eagle | Blaze",
    "Glock-18 | Fade",
    "USP-S | Kill Confirmed",
    "AK-47 | Fire Serpent",
    "AK-47 | Case Hardened",
    "AWP | Lightning Strike",
    "Five-SeveN | Monkey Business",
    "MAC-10 | Neon Rider",
    "Nova | Antique",
    "P90 | Asiimov",
    "MP7 | Fade",
    "AK-47 | Neon Rider",
    "M4A4 | Neo-Noir",
    "AWP | Electric Hive",
    "Desert Eagle | Printstream",
    "AK-47 | Wasteland Rebel",
    "M4A1-S | Printstream",
)
