"""Eval cases (spec §8.1): one YAML file per trip request in evals/cases/."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

CASES_DIR = Path(__file__).resolve().parent / "cases"
PERSONAS = {"rv_couple", "family", "weekend_explorer"}
REQUIRED = [
    "id",
    "persona",
    "request",
    "origin",
    "destination",
    "days",
    "vehicle",
    "max_drive_minutes",
    "budget_usd",
    "outdoor_leaning",
    "infeasible",
]


@dataclass(frozen=True)
class Replan:
    of: str
    from_day: int


@dataclass(frozen=True)
class EvalCase:
    id: str
    persona: str
    request: str
    origin: str
    destination: str | None
    days: int | None
    vehicle: str
    max_drive_minutes: int
    budget_usd: Decimal | None
    outdoor_leaning: bool
    infeasible: bool
    replan: Replan | None = None


def _parse(path: Path, raw: dict[str, Any]) -> EvalCase:
    missing = [k for k in REQUIRED if k not in raw]
    if missing:
        raise ValueError(f"{path.name}: missing {missing}")
    if raw["persona"] not in PERSONAS:
        raise ValueError(f"{path.name}: persona must be one of {sorted(PERSONAS)}")
    if raw["vehicle"] not in ("rv", "car"):
        raise ValueError(f"{path.name}: vehicle must be rv or car")
    if not isinstance(raw["max_drive_minutes"], int) or raw["max_drive_minutes"] <= 0:
        raise ValueError(f"{path.name}: max_drive_minutes must be a positive integer")
    replan = raw.get("replan")
    return EvalCase(
        id=str(raw["id"]),
        persona=raw["persona"],
        request=str(raw["request"]),
        origin=str(raw["origin"]),
        destination=raw["destination"],
        days=raw["days"],
        vehicle=raw["vehicle"],
        max_drive_minutes=raw["max_drive_minutes"],
        budget_usd=None if raw["budget_usd"] is None else Decimal(str(raw["budget_usd"])),
        outdoor_leaning=bool(raw["outdoor_leaning"]),
        infeasible=bool(raw["infeasible"]),
        replan=Replan(of=replan["of"], from_day=int(replan["from_day"])) if replan else None,
    )


def load_cases(directory: Path = CASES_DIR) -> list[EvalCase]:
    """All cases in file-name order. Replan cases must come after the case they replan."""
    cases: list[EvalCase] = []
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: expected a mapping")
        cases.append(_parse(path, raw))
    seen: set[str] = set()
    for c in cases:
        if c.id in seen:
            raise ValueError(f"duplicate case id {c.id}")
        if c.replan and c.replan.of not in seen:
            raise ValueError(f"{c.id}: replans {c.replan.of}, which must come earlier")
        seen.add(c.id)
    return cases
