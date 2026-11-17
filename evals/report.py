"""Summarize a results directory, and compare two of them."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

#: The columns of the per-task table: (heading, result key).
COLUMNS = [
    ("tool calls", "tool_calls"),
    ("cli calls", "cli_calls"),
    ("failed", "cli_failed"),
    ("invalid", "invalid_commands"),
    ("spec err", "spec_errors"),
    ("plan err", "plan_errors"),
    ("api err", "api_errors"),
    ("help", "help_calls"),
    ("docs", "docs_calls"),
    ("docs kB", "docs_bytes"),
    ("dry runs", "dry_runs"),
    ("applies", "applies"),
    ("cost $", "cost_usd"),
    ("secs", "wall_seconds"),
]
_SCALE = {"docs_bytes": 1 / 1000}


def load_results(out: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(out.glob("*/*/result.json"))]


def _median(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [
        r[key] * _SCALE.get(key, 1)
        for r in rows
        if isinstance(r.get(key), (int, float))
    ]
    return statistics.median(values) if values else None


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per task, and overall: pass rate and the median of each metric."""
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_task[r["task"]].append(r)

    def row(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "runs": len(rows),
            "passed": sum(1 for r in rows if r.get("passed")),
            **{key: _median(rows, key) for _, key in COLUMNS},
        }

    return {
        "tasks": {
            task: {"tier": rows[0].get("tier"), **row(rows)}
            for task, rows in sorted(by_task.items())
        },
        "overall": row(results),
        "totals": {
            key: sum(r.get(key) or 0 for r in results)
            for key in (
                "cli_calls",
                "cli_failed",
                "invalid_commands",
                "spec_errors",
                "plan_errors",
                "api_errors",
                "internal_errors",
                "tool_calls",
            )
        },  # fmt: skip
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".") if value % 1 else str(int(value))
    return str(value)


def render(summary: dict[str, Any]) -> str:
    head = ["task", "tier", "pass", *(h for h, _ in COLUMNS)]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]

    def line(name: str, tier: Any, row: dict[str, Any]) -> str:
        cells = [name, _fmt(tier), f"{row['passed']}/{row['runs']}"]
        cells += [_fmt(row[key]) for _, key in COLUMNS]
        return "| " + " | ".join(cells) + " |"

    for task, row in summary["tasks"].items():
        lines.append(line(task, row["tier"], row))
    lines.append(line("**all runs**", None, summary["overall"]))
    totals = ", ".join(
        f"{k.replace('_', ' ')} {v}" for k, v in summary["totals"].items()
    )
    return (
        "\n".join(lines)
        + "\n\nPer-task cells are medians across that task's runs.\n\n"
        + f"Totals across all runs: {totals}.\n"
    )


def failures(results: list[dict[str, Any]]) -> str:
    lines = []
    for r in results:
        if not r.get("passed"):
            lines.append(
                f"- {r['task']} #{r.get('trial')}: "
                + "; ".join(r.get("problems") or ["?"])
            )
    return "\n".join(lines)


def compare(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Per-task change in the headline numbers from summary `a` to summary `b`."""
    keys = [("pass", None), ("tool calls", "tool_calls"), ("cli calls", "cli_calls"),
            ("failed", "cli_failed"), ("invalid", "invalid_commands"), ("docs kB", "docs_bytes")]  # fmt: skip
    head = ["task", *(h for h, _ in keys)]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]

    def cell(
        x: dict[str, Any] | None, y: dict[str, Any] | None, key: str | None
    ) -> str:
        if key is None:
            return " → ".join(
                f"{r['passed']}/{r['runs']}" if r else "-" for r in (x, y)
            )
        before, after = (r.get(key) if r else None for r in (x, y))
        if before is None or after is None:
            return f"{_fmt(before)} → {_fmt(after)}"
        delta = after - before
        sign = "+" if delta > 0 else ""
        return f"{_fmt(before)} → {_fmt(after)} ({sign}{_fmt(delta)})"

    for task in sorted(set(a["tasks"]) | set(b["tasks"])):
        x, y = a["tasks"].get(task), b["tasks"].get(task)
        lines.append(
            "| " + " | ".join([task, *(cell(x, y, k) for _, k in keys)]) + " |"
        )
    lines.append(
        "| "
        + " | ".join(
            ["**all runs**", *(cell(a["overall"], b["overall"], k) for _, k in keys)]
        )
        + " |"
    )
    return "\n".join(lines) + "\n"
