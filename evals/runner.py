"""Eval runner (spec §8.1, ADR-004).

    uv run python -m evals.runner --planner agent.planner:plan_for_eval [--seed-catalog]
        [--baseline evals/results/main.json] [--out evals/results]

Runs a planner over every case (replans get the plan of the case they replan), checks each
plan, scores the suite against the thresholds and main's baseline, and writes
evals/results/<sha>.json plus a Markdown summary for the PR comment. Exit code 1 if the gate
fails.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.plan.catalog import Catalog
from evals.cases import EvalCase, load_cases
from evals.checks import GATED, CaseResult, check_case, score

RESULTS_DIR = Path(__file__).resolve().parent / "results"


@dataclass
class PlannerOutput:
    plan: Any
    allowed_ids: set[str] | None = None  # place IDs the tools returned in this run
    latency_s: float | None = None  # full plan
    first_progress_s: float | None = None  # first progress event (spec §8.2: < 3 s)
    cost_usd: float | None = None
    error: str | None = None


Planner = Callable[[EvalCase, dict[str, Any] | None], PlannerOutput]


def run(
    planner: Planner, catalog: Catalog, cases: Sequence[EvalCase] | None = None
) -> list[CaseResult]:
    cases = list(cases) if cases is not None else load_cases()
    plans: dict[str, Any] = {}
    results: list[CaseResult] = []
    for case in cases:
        previous = plans.get(case.replan.of) if case.replan else None
        started = time.perf_counter()
        try:
            # A copy: a planner that edits the plan it replans must not change the reference
            # the replan check compares against.
            output = planner(case, copy.deepcopy(previous))
        except Exception as err:
            output = PlannerOutput(plan=None, error=f"{type(err).__name__}: {err}")
        if output.latency_s is None:
            output.latency_s = time.perf_counter() - started
        result = check_case(case, output, catalog, previous)
        plans[case.id] = output.plan
        results.append(result)
    return results


def _markdown(summary: dict[str, Any], sha: str) -> str:
    verdict = "✅ gate passed" if summary["gate_ok"] else "❌ gate failed"
    lines = [
        f"### Eval results for `{sha}`: {verdict}",
        "",
        "| check | passed | rate | threshold | ok |",
        "|---|---|---|---|---|",
    ]
    for name in GATED:
        c = summary["checks"][name]
        lines.append(
            f"| {name} | {c['passed']}/{c['applicable']} | {c['rate']:.0%} | "
            f"{c['threshold']:.0%} | {'✅' if c['ok'] else '❌'} |"
        )
    if summary["regressions"]:
        lines += ["", "**Regressions vs main:** " + "; ".join(summary["regressions"])]
    t = summary.get("tracked") or {}
    if t:

        def fmt(v: Any, spec: str) -> str:
            return "n/a" if v is None else format(v, spec)

        mean_cost = t.get("mean_cost_usd")
        cost = "n/a" if mean_cost is None else f"${mean_cost:.3f}"
        lines += [
            "",
            f"Tracked (not gated): hotel conversion {fmt(t.get('hotel_conversion'), '.0%')}, "
            f"first progress p50 {fmt(t.get('first_progress_p50_s'), '.1f')} s / p95 "
            f"{fmt(t.get('first_progress_p95_s'), '.1f')} s, "
            f"latency p50 {fmt(t.get('latency_p50_s'), '.1f')} s / p95 "
            f"{fmt(t.get('latency_p95_s'), '.1f')} s, "
            f"mean cost {cost}",
        ]
    return "\n".join(lines) + "\n"


def write_results(
    results: list[CaseResult], summary: dict[str, Any], out_dir: Path, sha: str
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "sha": sha,
        "summary": summary,
        "cases": [
            {
                "id": r.id,
                "checks": r.checks,
                "violations": r.violations,
                "error": r.error,
                "latency_s": r.latency_s,
                "first_progress_s": r.first_progress_s,
                "cost_usd": r.cost_usd,
            }
            for r in results
        ],
    }
    json_path = out_dir / f"{sha}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    md_path = out_dir / f"{sha}.md"
    md_path.write_text(_markdown(summary, sha), encoding="utf-8")
    return json_path, md_path


def _load_planner(spec: str) -> Planner:
    module, _, name = spec.partition(":")
    planner: Planner = getattr(importlib.import_module(module), name)
    return planner


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--planner", required=True, help="module:function")
    parser.add_argument("--baseline", type=Path, default=RESULTS_DIR / "main.json")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    parser.add_argument("--seed-catalog", action="store_true", help="use seed CSVs, not the DB")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252

    catalog: Catalog
    if args.seed_catalog:
        from evals.catalog import seed_catalog

        catalog = seed_catalog()
    else:
        import psycopg

        from agent.plan.catalog import DbCatalog

        catalog = DbCatalog(psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10))

    results = run(_load_planner(args.planner), catalog)
    baseline = None
    if args.baseline.exists():
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["summary"]
    summary = score([r.checks for r in results], baseline, tracked=results)
    sha = (
        subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        or "local"
    )
    json_path, md_path = write_results(results, summary, args.out, sha)
    print(md_path.read_text(encoding="utf-8"))
    print(f"wrote {json_path}")
    return 0 if summary["gate_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
