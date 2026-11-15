"""The warehouse subset of the kit: properties, their Matterport scans and key specs."""
import csv
import json
from functools import lru_cache

from proinspect.config import DATA, ROOT, SCHEMAS

WAREHOUSE_SUBTYPES = {"Industrial_Warehouse", "Industrial_Distribution", "Flex_LightDistribution"}


@lru_cache
def enums():
    return json.loads((SCHEMAS / "enums.json").read_text())


def dec(enum, v):
    return enums().get(enum, {}).get(str(v), v)


@lru_cache
def scans_by_property():
    out = {}
    with open(ROOT / "commercial-properties.csv") as f:
        for r in csv.DictReader(f):
            if r["MatterportModelId"]:
                out.setdefault(int(r["PropertyId"]), []).append(r["MatterportModelId"])
    return out


@lru_cache
def warehouses():
    """{propertyId: raw commercial-property record} for warehouse-type properties."""
    out = {}
    with open(DATA / "commercial-property.jsonl") as f:
        for line in f:
            r = json.loads(line)
            if dec("PropertySubtype", r.get("propertySubtype")) in WAREHOUSE_SUBTYPES:
                out[r["id"]] = r
    return out
