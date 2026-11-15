"""What the user wants from a warehouse: filled from search text, chips, or the voice Concierge.

Nothing is required to search. Each feature declares only what it truly needs (`missing_for`): plain search
needs nothing, ranking needs at least one criterion or a profile, and a simulation needs a business profile and a
truck type plus the user's confirmation. Every criterion is a deal-breaker ("D": failing it removes the warehouse) or a nice-to-have ("N": it lowers
the score), with a weight 1-5 for how much the user cares. Profile needs (profiles.yaml) seed the criteria;
anything the user says overrides them.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from proinspect.library import equipment, profiles

Level = Literal["D", "N"]


class Criterion(BaseModel):
    """One requirement. Exactly one of min / max / value is set."""
    min: Optional[float] = None
    max: Optional[float] = None
    value: Optional[object] = None
    level: Level = "N"
    weight: int = Field(3, ge=1, le=5)
    said: Optional[str] = None          # the user's own words, for the confirmation read-back


class Area(BaseModel):
    lat: float
    lon: float
    radius_mi: float = 10


class Requirements(BaseModel):
    # ---- minimum slots (must be filled before searching)
    profile: Optional[str] = None                       # key in profiles.yaml
    market: Optional[Literal["Houston", "Los Angeles", "Either"]] = None
    size_min_sqft: Optional[float] = None
    size_max_sqft: Optional[float] = None
    deal: Optional[Literal["lease", "buy", "either"]] = None
    truck: Optional[Literal["semi_53", "box_truck_26", "last_mile_van"]] = None
    confirmed: bool = False
    # ---- optional context
    business_name: Optional[str] = None
    area: Optional[Area] = None
    budget_rent_psf_yr: Optional[float] = None           # max asking rent, $/SF/yr
    budget_price: Optional[float] = None                 # max asking price, $
    equipment: list[str] = []                            # keys in equipment.yaml
    volume_scale: float = 1.0                            # x the profile's typical volume
    available_only: bool = False                         # only actively listed buildings
    inspectable_only: bool = False                       # only buildings with a warehouse-floor scan
    criteria: dict[str, Criterion] = {}                  # keyed by fact name (see rank.FACTS)

    # What each feature truly needs. Plain search needs nothing; only the simulation needs an operation.
    NEEDS: dict = {"search": (), "rank": (), "simulate": ("profile", "truck")}

    def missing_for(self, feature: str) -> list[str]:
        """Slots still empty that `feature` cannot run without, in the order to ask. Search never asks."""
        if feature not in self.NEEDS:
            raise ValueError(f"unknown feature {feature}")
        out = [s for s in self.NEEDS[feature] if getattr(self, s) in (None, "", [])]
        if feature == "rank" and not (self.profile or self.derived_criteria()):
            out.append("criteria")                 # ranking needs at least one thing to rank by
        return out

    def ready_for(self, feature: str) -> bool:
        """A simulation also needs the user's confirmation of the operation; search and rank never do."""
        return not self.missing_for(feature) and (feature != "simulate" or self.confirmed)

    def apply_profile(self, key: str):
        """Adopt a business profile: its default criteria and fleet, without overwriting what the user said."""
        if key not in profiles():
            raise ValueError(f"unknown profile {key}; choose from {sorted(profiles())}")
        p = profiles()[key]
        self.profile = key
        for name, spec in (p.get("needs") or {}).items():
            if name not in self.criteria:
                self.criteria[name] = Criterion(**{k: v for k, v in spec.items() if k in ("min", "max", "value")},
                                                level=spec.get("level", "N"), weight=4 if spec.get("level") == "D" else 2)
        if not self.equipment:
            self.equipment = [e for e in (p.get("fleet") or {}) if e in equipment()]
        if not self.truck:
            road = [v for v in list(p.get("inbound", {})) + list(p.get("outbound", {}))
                    if v in ("semi_53", "box_truck_26", "last_mile_van")]
            self.truck = max(road, key=lambda v: equipment()[v]["dims_ft"]["length"]) if road else None
        return self

    def derived_criteria(self) -> dict[str, Criterion]:
        """Criteria implied by the slots and equipment (size, truck court, equipment clear height)."""
        out = dict(self.criteria)
        if self.size_min_sqft and "size_sqft" not in out:
            out["size_sqft"] = Criterion(min=self.size_min_sqft, max=self.size_max_sqft, level="D", weight=5)
        if self.truck and "truck_court_ft" not in out:
            depth = equipment()[self.truck].get("truck_court_depth_ft")
            if depth:
                out["truck_court_ft"] = Criterion(min=depth, level="N", weight=3,
                                                  said=f"room for a {equipment()[self.truck]['name']}")
        if self.budget_rent_psf_yr and "rent_psf_yr" not in out:
            out["rent_psf_yr"] = Criterion(max=self.budget_rent_psf_yr, level="N", weight=4)
        if self.budget_price and "price" not in out:
            out["price"] = Criterion(max=self.budget_price, level="N", weight=4)
        tall = max([equipment()[e].get("min_clear_height_ft", 0) for e in self.equipment if e in equipment()] or [0])
        if tall and "clear_height_ft" not in out:
            out["clear_height_ft"] = Criterion(min=tall, level="N", weight=3, said="for the equipment you run")
        return out
