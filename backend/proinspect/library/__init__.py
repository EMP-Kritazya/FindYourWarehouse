"""Pre-saved libraries: equipment (vehicles) and business profiles. Users can clone and edit entries."""
from functools import lru_cache
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


@lru_cache
def equipment():
    return yaml.safe_load((HERE / "equipment.yaml").read_text())["equipment"]


@lru_cache
def profiles():
    return yaml.safe_load((HERE / "profiles.yaml").read_text())["profiles"]
