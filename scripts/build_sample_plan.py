"""Write apps/demo/sample_plan.json: a hand-picked, validated plan for the demo's offline mode.

    uv run python scripts/build_sample_plan.py

Only the structure is hand-written (which seed places, in which order, and the words).
Drive times, distances and totals are filled from the tools, and the plan must pass
validate_plan, or the script fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agent.plan.measure import complete_draft, fill_numbers
from agent.plan.validate import validate_plan
from evals.catalog import seed_catalog

OUT = Path(__file__).resolve().parents[1] / "apps" / "demo" / "sample_plan.json"


def stop(pid: str, kind: str, reason: str) -> dict[str, str]:
    return {"id": pid, "type": kind, "reason": reason}


PLAN: dict[str, Any] = {
    "trip_id": "sample-rv-dc-asheville",
    "summary": "Four relaxed days from DC to Asheville in an RV, mixing farm stays with one "
    "hotel night in Roanoke.",
    "origin": "Washington, DC",
    "destination": "Asheville, NC",
    "vehicle": "rv",
    "days": [
        {
            "day": 1,
            "stops": [
                stop("stop_linden_vineyards", "winery", "A hillside vineyard right off the route."),
                stop(
                    "stop_car_carriage_caravan_museum",
                    "museum",
                    "An easy leg-stretch at Luray Caverns.",
                ),
            ],
            "overnight": {
                "kind": "host",
                "id": "stop_dices_spring_farm",
                "reason_code": "drive_time",
                "reason": "The farm sits just past Staunton, a comfortable first day.",
            },
            "narrative": "Leave DC early and climb into the Blue Ridge foothills. Taste wine at "
            "Linden and wander the car museum at Luray. Park the RV at Dices Spring Farm.",
        },
        {
            "day": 2,
            "stops": [
                stop(
                    "stop_barren_ridge_vineyards",
                    "winery",
                    "A valley winery a few minutes from last night's farm.",
                ),
                stop(
                    "stop_frontier_culture_museum",
                    "museum",
                    "Outdoor farm exhibits just down the road.",
                ),
            ],
            "overnight": {
                "kind": "hotel",
                "id": "hotel_quality_inn_roanoke_airport",
                "reason_code": "budget",
                "reason": "A well-priced hotel night in Roanoke before the long Parkway day.",
            },
            "narrative": "Start with a tasting at Barren Ridge, then the Frontier Culture Museum "
            "near Staunton. Roll south to Roanoke and sleep in a real bed tonight.",
        },
        {
            "day": 3,
            "stops": [],
            "overnight": {
                "kind": "host",
                "id": "stop_shipley_farms",
                "reason_code": "drive_time",
                "reason": "Shipley Farms near Boone breaks the longest leg of the trip.",
            },
            "narrative": "Today is the longest drive, into the North Carolina high country. Take "
            "it slow and reach Shipley Farms by late afternoon.",
        },
        {
            "day": 4,
            "stops": [
                stop(
                    "stop_hickory_ridge_museum", "museum", "Living history in Boone on the way out."
                ),
                stop("stop_asheville_art_museum", "museum", "A downtown finish in Asheville."),
            ],
            "overnight": None,
            "narrative": "Visit the Hickory Ridge History Museum before heading south. Drive the "
            "last stretch and end at the Asheville Art Museum downtown.",
        },
    ],
    "warnings": [],
    "alternatives": [],
}


def main() -> int:
    catalog = seed_catalog()
    plan = fill_numbers(complete_draft(json.loads(json.dumps(PLAN))), catalog)
    problems = validate_plan(plan, catalog, max_drive_minutes=300)
    if problems:
        for v in problems:
            print(f"day {v.day}: {v.code}: {v.message}", file=sys.stderr)
        return 1
    OUT.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    for d in plan["days"]:
        print(f"day {d['day']}: {d['drive_minutes']} min, {d['distance_km']} km")
    print(plan["totals"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
