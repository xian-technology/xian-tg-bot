from decimal import Decimal
from pathlib import Path

import pytest

from config import ConfigManager
from utils import format_float, is_numeric


def test_config_manager_loads_existing_global_config() -> None:
    manager = ConfigManager(Path("cfg/global.json"))

    assert isinstance(manager.snapshot(), dict)


def test_utils_smoke_behaviour() -> None:
    assert is_numeric("12.5")
    assert not is_numeric("abc")
    assert format_float(42.0) == "42"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.234e-5, "0.000012"),
        (Decimal("1.234E-9"), "0.0000000012"),
        (Decimal("-1.0020300"), "-1.00203"),
        (Decimal("3.000"), "3"),
        (-0.0, "0"),
    ],
)
def test_format_float_keeps_the_scale_of_small_prices(value: float | Decimal, expected: str) -> None:
    assert format_float(value) == expected
