"""Run a baseline validation eval over ORD example files."""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEFAULT_EXCLUDES = ["reg_*.ord", "inverter_constraints.ord"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--examples-dir",
        default="ord2_examples",
        help="Directory containing .ord files",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help=(
            "Glob pattern to exclude (can be repeated). "
            f"Defaults: {', '.join(DEFAULT_EXCLUDES)}"
        ),
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional path to write detailed JSON report",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero exit code when any example fails validation",
    )
    return parser.parse_args()


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item["success"])
    failed = total - passed

    stage_counts = Counter()
    code_counts = Counter()
    for item in results:
        if item["success"]:
            continue
        stage_counts[item["error_stage"] or "unknown"] += 1
        code_counts[item["error_code"] or "unknown"] += 1

    pass_rate = passed / total if total else 0.0

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": pass_rate,
        "failure_by_stage": dict(stage_counts),
        "failure_by_code": dict(code_counts),
    }


def _print_summary(summary: dict[str, Any]) -> None:
    print("=== Validator Eval Summary ===")
    print(f"Total files: {summary['total']}")
    print(f"Passed:      {summary['passed']}")
    print(f"Failed:      {summary['failed']}")
    print(f"Pass rate:   {summary['pass_rate']:.2%}")

    print("\nFailures by stage:")
    if summary["failure_by_stage"]:
        for stage, count in sorted(summary["failure_by_stage"].items()):
            print(f"- {stage}: {count}")
    else:
        print("- none")

    print("\nFailures by code:")
    if summary["failure_by_code"]:
        for code, count in sorted(summary["failure_by_code"].items()):
            print(f"- {code}: {count}")
    else:
        print("- none")


def main() -> int:
    args = _parse_args()

    try:
        import validator
    except Exception as exc:
        print(f"Could not import validator stack: {exc}")
        return 2

    examples_dir = Path(args.examples_dir)
    files = sorted(examples_dir.glob("*.ord"))
    if not files:
        print(f"No .ord files found in {examples_dir}")
        return 2

    exclude_patterns = [*DEFAULT_EXCLUDES, *args.exclude]
    files = [
        path
        for path in files
        if not any(fnmatch.fnmatch(path.name, pattern) for pattern in exclude_patterns)
    ]
    if not files:
        print("No .ord files left after applying excludes")
        return 2

    results = []
    for path in files:
        source = path.read_text()
        outcome = validator.validate_ord_code_full(source)

        record = {
            "file": path.name,
            "success": outcome.success,
            "error_stage": outcome.error_stage,
            "error_code": outcome.error_code,
            "error_message": outcome.error_message,
            "cell_names": outcome.cell_names,
            "spacing_violations": outcome.spacing_violations,
            "svg_bytes_size": len(outcome.svg_bytes) if outcome.svg_bytes else 0,
        }
        results.append(record)

    summary = _summarize(results)
    _print_summary(summary)

    if args.json_out:
        output = {"summary": summary, "results": results}
        Path(args.json_out).write_text(json.dumps(output, indent=2))
        print(f"\nWrote JSON report to {args.json_out}")

    if args.strict and summary["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
