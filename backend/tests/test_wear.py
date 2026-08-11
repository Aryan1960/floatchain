import pytest

from app.domain.wear import wear_name


@pytest.mark.parametrize(
    "float_value,expected",
    [
        (0.0, "Factory New"),
        (0.069999, "Factory New"),
        (0.07, "Minimal Wear"),
        (0.14, "Minimal Wear"),
        (0.15, "Field-Tested"),
        (0.37, "Field-Tested"),
        (0.38, "Well-Worn"),
        (0.44, "Well-Worn"),
        (0.45, "Battle-Scarred"),
        (1.0, "Battle-Scarred"),
    ],
)
def test_wear_name_thresholds(float_value, expected):
    assert wear_name(float_value) == expected
