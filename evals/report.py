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
#: Metrics whose run-to-run range the report shows next to the median.
SPREAD_KEYS = ("tool_calls", "cli_calls", "cli_failed", "cost_usd")


def load_results(out: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(out.glob("*/*/result.json"))]


def _values(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [
        r[key] * _SCALE.get(key, 1)
        for r in rows
        if isinstance(r.get(key), (int, float))
    ]


def _median(rows: list[dict[str, Any]], key: str) -> float | None:
    values = _values(rows, key)
    return statistics.median(values) if values else None


def _range(rows: list[dict[str, Any]], key: str) -> list[float] | None:
    values = _values(rows, key)
    return [min(values), max(values)] if len(values) > 1 else None


def is_correct(result: dict[str, Any]) -> bool:
    """The reliability verdict for one run. A run the judge failed on isn't correct."""
    return bool(result.get("correct", result.get("passed")))


def friction_summary(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Per cause the judge named: how often, and how many CLI calls it cost."""
    out: dict[str, dict[str, int]] = {}
    for r in results:
        for f in r.get("friction") or []:
            row = out.setdefault(f["cause"], {"times": 0, "cli_calls_wasted": 0})
            row["times"] += 1
            row["cli_calls_wasted"] += f.get("cli_calls_wasted") or 0
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["cli_calls_wasted"]))


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per task, and overall: how many runs were correct, and each metric's median."""
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_task[r["task"]].append(r)

    def row(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "runs": len(rows),
            "correct": sum(1 for r in rows if is_correct(r)),
            "passed": sum(1 for r in rows if r.get("passed")),
            "judge_errors": sum(1 for r in rows if r.get("judge_errors")),
            **{key: _median(rows, key) for _, key in COLUMNS},
            "spread": {key: _range(rows, key) for key in SPREAD_KEYS},
        }

    return {
        "tasks": {
            task: {"tier": rows[0].get("tier"), **row(rows)}
            for task, rows in sorted(by_task.items())
        },
        "overall": row(results),
        "judged": any("judged_correct" in r for r in results),
        "friction": friction_summary(results),
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
                "cost_usd",
                "judge_tokens",
            )
        },
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".") if value % 1 else str(int(value))
    return str(value)


def render(summary: dict[str, Any]) -> str:
    head = ["task", "tier", "correct", "checks", *(h for h, _ in COLUMNS)]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]

    def cell(row: dict[str, Any], key: str) -> str:
        spread = (row.get("spread") or {}).get(key)
        if spread and spread[0] != spread[1]:
            return f"{_fmt(row[key])} ({_fmt(spread[0])}–{_fmt(spread[1])})"
        return _fmt(row[key])

    def line(name: str, tier: Any, row: dict[str, Any]) -> str:
        runs = row["runs"]
        cells = [
            name,
            _fmt(tier),
            f"{row['correct']}/{runs}",
            f"{row['passed']}/{runs}",
        ]
        cells += [cell(row, key) for _, key in COLUMNS]
        return "| " + " | ".join(cells) + " |"

    for task, row in summary["tasks"].items():
        lines.append(line(task, row["tier"], row))
    lines.append(line("**all runs**", None, summary["overall"]))
    totals = ", ".join(
        f"{k.replace('_', ' ')} {_fmt(v)}" for k, v in summary["totals"].items()
    )
    judged = (
        " and the judge passed every outcome criterion" if summary.get("judged") else ""
    )
    text = (
        "\n".join(lines)
        + f"\n\n`correct` is the reliability number: the structural checks passed{judged}. "
        "`checks` is the structural checks alone. The other cells are medians across "
        "that task's runs, with the range in brackets where runs differed.\n\n"
        f"Totals across all runs: {totals}.\n"
    )
    if summary["overall"].get("judge_errors"):
        text += (
            f"\nThe judge failed on {summary['overall']['judge_errors']} run(s), which "
            "count as not correct. Run `python -m evals judge` on this directory to retry.\n"
        )
    if summary.get("friction"):
        text += (
            "\nFriction the judge found, by cause:\n\n"
            "| cause | times | cli calls wasted |\n|---|---|---|\n"
        )
        for cause, row in summary["friction"].items():
            text += f"| {cause} | {row['times']} | {row['cli_calls_wasted']} |\n"
    return text


def failures(results: list[dict[str, Any]]) -> str:
    """One line per run that wasn't correct, saying why."""
    lines = []
    for r in results:
        if is_correct(r):
            continue
        why = list(r.get("problems") or [])
        why += [f"judge: failed {c}" for c in r.get("outcome_failed") or []]
        why += [f"judge: could not tell {c}" for c in r.get("outcome_unclear") or []]
        why += [f"judge error: {e}" for e in r.get("judge_errors") or []]
        lines.append(f"- {r['task']} #{r.get('trial')}: " + "; ".join(why or ["?"]))
    return "\n".join(lines)


def friction_notes(results: list[dict[str, Any]]) -> str:
    """Each tool-side friction point the judge wrote up, costliest first."""
    notes = [
        (f.get("cli_calls_wasted") or 0, r["task"], f)
        for r in results
        for f in r.get("friction") or []
        if f.get("cause") != "agent_mistake"
    ]
    notes.sort(key=lambda n: -n[0])
    return "\n".join(
        f"- {task} ({f['cause']}, {wasted} calls): {f['what_happened']}"
        for wasted, task, f in notes
    )


def compare(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Per-task change in the headline numbers from summary `a` to summary `b`."""
    keys: list[tuple[str, str | None]] = [
        ("correct", None),
        ("cost $", "cost_usd"),
        ("tool calls", "tool_calls"),
        ("cli calls", "cli_calls"),
        ("failed", "cli_failed"),
        ("invalid", "invalid_commands"),
        ("docs kB", "docs_bytes"),
    ]
    head = ["task", *(h for h, _ in keys)]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]

    def cell(
        x: dict[str, Any] | None, y: dict[str, Any] | None, key: str | None
    ) -> str:
        if key is None:
            return " → ".join(
                f"{r['correct']}/{r['runs']}" if r else "-" for r in (x, y)
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
    overall = [cell(a["overall"], b["overall"], k) for _, k in keys]
    lines.append("| " + " | ".join(["**all runs**", *overall]) + " |")
    return "\n".join(lines) + "\n"
