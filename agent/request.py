"""A trip request as the planner receives it: the user's words plus known constraints."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass
class TripRequest:
    text: str
    vehicle: str | None = None  # "rv" or "car"; None lets the model read it from the text
    max_drive_minutes: float = 300
    budget_usd: Decimal | float | None = None  # lodging only (spec §5.0)
    previous_plan: dict[str, Any] | None = None  # set for a replan
    replan_from_day: int | None = None

    def budget(self) -> Decimal | None:
        return None if self.budget_usd is None else Decimal(str(self.budget_usd))
