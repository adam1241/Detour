"""Import federal campgrounds with RV sites from Recreation.gov's RIDB (ADR-008).

    RIDB_API_KEY=... uv run python -m db.seed.import_ridb [--states CO UT] [--out ...]

Pages through /facilities per state (activity CAMPING, 50 per page; the API allows
50 requests/second, we send about 5). Keeps facilities whose type is "Campground" and whose
description or keywords mention RVs. Writes stops_ridb.csv and counts_ridb.md. Fees in RIDB
are free text, so rows carry no price. Data: Recreation.gov RIDB, US public domain.
"""

from __future__ import annotations

import argparse
import os
import re
import string
import sys
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from db.seed.import_common import OUT_DIR, STATES, http_json, in_us, report, write_stops
from db.seed.load_seed import StopRow, validate

RIDB_URL = "https://ridb.recreation.gov/api/v1/facilities"
PAGE = 50
PAUSE_S = 0.2
RV = re.compile(r"\bRVs?\b")


def _name(raw: str) -> str:
    raw = raw.strip()
    return string.capwords(raw.lower()) if raw.isupper() else raw


def campground_rows(records: Iterable[dict[str, Any]]) -> list[StopRow]:
    rows: dict[str, StopRow] = {}
    for rec in records:
        fid = str(rec.get("FacilityID", ""))
        if not fid.isdigit() or rec.get("Enabled") is False:
            continue
        if rec.get("FacilityTypeDescription") != "Campground":
            continue
        text = f"{rec.get('FacilityDescription') or ''} {rec.get('Keywords') or ''}"
        if not RV.search(text):
            continue
        try:
            lon, lat = float(rec["FacilityLongitude"]), float(rec["FacilityLatitude"])
        except (KeyError, TypeError, ValueError):
            continue
        name = _name(rec.get("FacilityName") or "")
        if not name or not in_us(lon, lat):
            continue
        rows[fid] = StopRow(
            id=f"stop_ridb_{fid}",
            name=name,
            type="campground",
            lon=lon,
            lat=lat,
            overnight_ok=True,
            est_cost_usd=None,
            description="Federal campground; its listing mentions RV sites.",
            source="ridb",
            source_url=f"https://www.recreation.gov/camping/campgrounds/{fid}",
            source_ref=f"ridb:facility/{fid}",
            licence="US-PD",
        )
    return list(rows.values())


def fetch_state(state: str, api_key: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = f"state={state}&activity=CAMPING&limit={PAGE}&offset={offset}"
        data = http_json(f"{RIDB_URL}?{query}", headers={"apikey": api_key})
        page: list[dict[str, Any]] = data.get("RECDATA", [])
        records += page
        total = int(data.get("METADATA", {}).get("RESULTS", {}).get("TOTAL_COUNT", 0))
        offset += PAGE
        time.sleep(PAUSE_S)
        if not page or offset >= total:
            return records


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--states", nargs="*", default=list(STATES))
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    api_key = os.environ.get("RIDB_API_KEY")
    if not api_key:
        print("set RIDB_API_KEY (free key from ridb.recreation.gov)", file=sys.stderr)
        return 1

    stops: dict[str, StopRow] = {}
    counts: Counter[tuple[str, str]] = Counter()
    for state in args.states:
        rows = campground_rows(fetch_state(state, api_key))
        new = [r for r in rows if r.id not in stops]
        stops.update({r.id: r for r in new})
        counts[(state, "campground")] += len(new)
        print(f"{state}: {len(new)} campgrounds with RV sites", flush=True)

    stop_list = list(stops.values())
    errors = validate([], stop_list)
    if errors:
        print("\n".join(errors[:20]), file=sys.stderr)
        return 1
    write_stops(args.out / "stops_ridb.csv", stop_list)
    table = report(counts)
    (args.out / "counts_ridb.md").write_text(table + "\n", encoding="utf-8")
    print(table)
    print(f"wrote {len(stop_list)} campgrounds to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
