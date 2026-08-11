from app.domain.naming import market_hash_name


def test_market_hash_name_normal():
    assert market_hash_name("AK-47 | Redline", "Field-Tested", False) == (
        "AK-47 | Redline (Field-Tested)"
    )


def test_market_hash_name_stattrak_weapon():
    assert market_hash_name("AK-47 | Redline", "Field-Tested", True) == (
        "StatTrak™ AK-47 | Redline (Field-Tested)"
    )


def test_market_hash_name_stattrak_knife():
    assert market_hash_name("★ Karambit | Doppler", "Factory New", True) == (
        "★ StatTrak™ Karambit | Doppler (Factory New)"
    )
