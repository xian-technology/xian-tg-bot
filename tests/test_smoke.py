from pathlib import Path

from config import ConfigManager
from utils import format_float, is_numeric


def test_config_manager_loads_existing_global_config() -> None:
    manager = ConfigManager(Path("cfg/global.json"))

    assert isinstance(manager.snapshot(), dict)


def test_utils_smoke_behaviour() -> None:
    assert is_numeric("12.5")
    assert not is_numeric("abc")
    assert format_float(42.0) == "42"
