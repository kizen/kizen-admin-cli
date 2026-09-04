"""Render/wait helpers shared by `cli/runs.py` and `cli/automations.py`.

Lives here, not in either of those modules, so neither has to import the
other: `runs.py` needs these for `runs view`/`runs logs`, and
`automations.py` needs them for `_RunStream`/`automations start --wait`.
"""

from __future__ import annotations

import json
from typing import Any

from kizen_builder import output as out
from kizen_builder.cli._shared import console
from kizen_builder.tools import automations as auto_tools


def history_duration(e: dict[str, Any]) -> str:
    ms = e.get("duration_ms")
    if ms is None:
        return ""
    return f"{ms / 1000:.2f}s" if ms >= 1000 else f"{ms}ms"


def wait_exit_code(summary: dict[str, Any]) -> int:
    """completed -> 0; failed/cancelled -> 1; timeout or any paused* -> 3 —
    the wait ended without the run finishing, which a script has to be able
    to tell apart from an actual failure."""
    status = summary.get("status")
    if summary.get("timed_out") or status in auto_tools.PAUSED_EXECUTION_STATUSES:
        return 3
    if status in ("failed", "cancelled"):
        return 1
    return 0


def print_wait_outcome(
    execution_id: str, summary: dict[str, Any], timeout: float
) -> None:
    """The human-readable line printed after `--wait` ends without the run
    reaching `completed`/`failed`/`cancelled`. Deliberately avoids "failed",
    "stalled", "stuck" — a timeout or a plain `paused` is not evidence the
    run is broken (see wait_for_execution's docstring)."""
    status = summary.get("status")
    if summary.get("timed_out"):
        console.print(
            f"[yellow]still {status or 'unknown'}[/yellow] after {timeout:g}s — "
            "the run may still complete; this is not a failure. Re-check with "
            f"`kizen automations runs view {execution_id}`, or raise --timeout "
            f"(currently {timeout:g}s)."
        )
    elif status == "paused_by_failure":
        console.print(
            "[yellow]paused_by_failure[/yellow] — halted on a step failure and "
            f"needs a human. Resume with `kizen automations runs resume "
            f"{execution_id}` once it's addressed."
        )
    elif status in auto_tools.PAUSED_EXECUTION_STATUSES:
        console.print(
            f"[yellow]{status}[/yellow] — resume with `kizen automations runs "
            f"resume {execution_id}`."
        )


def print_step_log(index: int, entry: dict[str, Any]) -> None:
    """Render one history row's `detailed_log`. At least five shapes are seen
    live: this repo's fixture `{stdout, traceback}`, a bare `logs` list (the
    shape a code_step's `outputs.log(...)` calls produce on their own),
    `{reasons: "..."}` on an action-step failure, `{debug_action: "..."}` on
    a debug advance, and a code step's full `{logs, inputs, values,
    http_requests, duration}` audit. Only a *bare* `logs` dict gets the terse
    rendering below; a `logs` dict with sibling keys (the full audit) falls
    through to the JSON dump so `inputs`/`values`/`http_requests`/`duration`
    aren't silently dropped."""
    kind = entry.get("kind") or "step"
    step_type = entry.get("type") or ""
    desc = entry.get("description") or ""
    label = f"#{index} {kind}"
    if step_type:
        label += f" ({step_type})"
    if desc:
        label += f" — {desc}"
    console.print(f"[bold]{label}[/bold]")

    log = entry.get("detailed_log")
    if isinstance(log, dict) and ("stdout" in log or "traceback" in log):
        stdout = log.get("stdout") or ""
        if stdout:
            console.print(f"  stdout: {stdout}")
        traceback = log.get("traceback")
        if traceback:
            console.print(f"  [red]{traceback}[/red]")
        if not stdout and not traceback:
            console.print("  [dim](empty)[/dim]")
    elif isinstance(log, dict) and set(log) == {"logs"}:
        # Mirrors cli/code.py's `_render_coderunner_result` logs section —
        # same "always show, explicit (none)" idiom, not shared code.
        lines = log.get("logs") or []
        if lines:
            console.print(f"  [bold]logs[/bold] ({len(lines)})")
            for line in lines:
                console.print(f"    {line}")
        else:
            console.print(
                '  [dim]logs: (none — use outputs.log("…") to emit; plain '
                "print() is not captured)[/dim]"
            )
    else:
        dumped = json.dumps(log, indent=2, default=str)
        console.print("  " + dumped.replace("\n", "\n  "))
    console.print()


def render_execution_logs(
    entries: list[dict[str, Any]], *, json_out: bool = False
) -> None:
    """Render every history row's `detailed_log` — the rendering half of
    `runs logs`, kept separate from the command so `automations start`'s
    `--show-logs` can call it too instead of duplicating it.

    ``entries`` is `auto_tools.get_execution_history()`'s own return value,
    unchanged — this adds no new API call.
    """
    numbered = list(enumerate(entries, 1))
    logged = [(i, e) for i, e in numbered if e.get("detailed_log") is not None]

    if json_out:
        out.emit_json([{"index": i, **e} for i, e in logged])
        return

    if not logged:
        console.print(
            "[dim](no step logs)[/dim] — only a code_step's "
            'outputs.log("…") populates this (plain print() is not '
            "captured). See `kizen docs show code-steps`."
        )
        return

    for i, e in logged:
        print_step_log(i, e)
