"""Shared pieces of the OpenStreetMap and RIDB importers (ADR-008)."""

from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from db.seed.load_seed import (
    HOTEL_COLUMNS,
    NATIONWIDE_COLUMNS,
    STOP_COLUMNS,
    US_BBOX,
    HotelRow,
    StopRow,
)

# Imported data stays out of git: publishing it would make the repo an ODbL derived database.
OUT_DIR = Path(__file__).resolve().parent / "imported"
USER_AGENT = "Detour-poc-importer/1.0 (one-time build of a proof-of-concept dataset)"

# Contiguous US states plus DC (ADR-008 scope).
STATES = [
    "AL",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "DC",
    "FL",
    "GA",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
]


def in_us(lon: float, lat: float) -> bool:
    box = US_BBOX
    return box["min_lon"] <= lon <= box["max_lon"] and box["min_lat"] <= lat <= box["max_lat"]


def http_json(
    url: str,
    *,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = 4,
) -> Any:
    """GET (or form POST when `data` is given) JSON, retrying 429/5xx with backoff."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, headers=request_headers)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310 (fixed https hosts)
                return json.loads(resp.read())
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(15 * (attempt + 1))
                continue
            raise RuntimeError(f"{url}: HTTP {err.code}") from None
    raise RuntimeError(f"{url}: retries exhausted")


def write_csv(path: Path, columns: Sequence[str], rows: Sequence[HotelRow | StopRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[*columns, *NATIONWIDE_COLUMNS])
        writer.writeheader()
        for row in rows:
            record = asdict(row)
            if "amenities" in record:
                record["amenities"] = ";".join(record["amenities"])
            writer.writerow(
                {k: ("" if record[k] is None else record[k]) for k in writer.fieldnames}
            )
    tmp.replace(path)


def write_hotels(path: Path, rows: Sequence[HotelRow]) -> None:
    write_csv(path, HOTEL_COLUMNS, rows)


def write_stops(path: Path, rows: Sequence[StopRow]) -> None:
    write_csv(path, STOP_COLUMNS, rows)


def report(counts: Counter[tuple[str, str]]) -> str:
    """Markdown table of counts per state and type, for the PR description."""
    types = sorted({t for _, t in counts})
    states = sorted({s for s, _ in counts})
    lines = ["| state | " + " | ".join(types) + " |", "|---|" + "---|" * len(types)]
    for state in states:
        lines.append(f"| {state} | " + " | ".join(str(counts[(state, t)]) for t in types) + " |")
    totals = [sum(counts[(s, t)] for s in states) for t in types]
    lines.append("| **total** | " + " | ".join(f"**{n}**" for n in totals) + " |")
    return "\n".join(lines)
