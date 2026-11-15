"""Paths and keys. Keys load from ProInspect/.env; never print them."""
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]          # ProInspect/
DATA = ROOT / "data"
SCHEMAS = ROOT / "schemas"
BUILD = ROOT / "build"
CACHE_DIR = ROOT / "cache"
load_dotenv(ROOT / ".env")
