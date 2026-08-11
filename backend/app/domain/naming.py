"""Steam market_hash_name construction, needed to query CSFloat by name."""

from __future__ import annotations


def market_hash_name(base_name: str, wear: str, stattrak: bool) -> str:
    if not stattrak:
        return f"{base_name} ({wear})"
    if base_name.startswith("★ "):
        return f"★ StatTrak™ {base_name[2:]} ({wear})"
    return f"StatTrak™ {base_name} ({wear})"
