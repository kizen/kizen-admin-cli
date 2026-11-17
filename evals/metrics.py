"""Turn a run's logs into numbers.

Two sources. The shim's log (`kizen.jsonl`) has every CLI call, whatever
agent made it: that is where the invalid-command and error counts come from.
The agent's transcript, when there is one in Claude Code's stream-json form,
adds what the CLI can't see: total tool calls, tokens, cost and turns.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

#: How a failed call is classified, most specific first. `usage` is Click
#: rejecting the command line itself: the "invalid command" count.
_USAGE = re.compile(
    r"No such (command|option)|Missing (argument|option|command|parameter)"
    r"|Got unexpected extra argument|Invalid value|requires an argument|^Usage:",
    re.M,
)
_API = re.compile(r"HTTP (\d{3})")

_HELP_FLAGS = {"--help", "-h"}
#: Verbs that change something when run without `--dry-run`.
_MUTATING = {
    "create", "update", "delete", "activate", "deactivate", "add", "edit", "remove",
    "duplicate", "move", "start", "apply", "set-ui", "import", "archive", "cancel",
    "resume", "pause", "upgrade", "init",
}  # fmt: skip


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def command_path(argv: list[str]) -> list[str]:
    """The leading command words: `automations steps add`, `docs show`."""
    path = []
    for arg in argv:
        if arg.startswith("-") or not re.fullmatch(r"[a-z][a-z0-9-]*", arg):
            break
        path.append(arg)
        if len(path) == 3:
            break
    return path


def kind_of(argv: list[str]) -> str:
    """What a call was for: help, docs, read, dry_run or apply."""
    path = command_path(argv)
    if not argv or _HELP_FLAGS & set(argv):
        return "help"
    if path[:1] == ["docs"]:
        return "docs"
    if "--dry-run" in argv:
        return "dry_run"
    if "--check" in argv:  # `upgrade --check` only reports
        return "read"
    if _MUTATING & set(path[1:] or path):
        return "apply"
    return "read"


def outcome_of(call: dict[str, Any]) -> str:
    """ok, or why the call failed: usage, spec, plan, api_4xx, api_5xx, internal, other."""
    if call["exit_code"] == 0:
        return "ok"
    text = (call.get("stderr_tail") or "") + (call.get("stdout_tail") or "")
    if "error: internal:" in text:
        return "internal"
    if "spec error:" in text:
        return "spec"
    if "plan error:" in text:
        return "plan"
    api = _API.search(text)
    if api:
        return "api_5xx" if api.group(1).startswith("5") else "api_4xx"
    if call["exit_code"] == 2 or _USAGE.search(text):
        return "usage"
    return "other"


def cli_metrics(calls: list[dict[str, Any]]) -> dict[str, Any]:
    kinds = Counter(kind_of(c["argv"]) for c in calls)
    outcomes = Counter(outcome_of(c) for c in calls)
    failed = [c for c in calls if c["exit_code"] != 0]
    docs = [c for c in calls if kind_of(c["argv"]) == "docs"]
    applies = [c for c in calls if kind_of(c["argv"]) == "apply"]
    first_ok_apply = next(
        (i for i, c in enumerate(calls) if c in applies and c["exit_code"] == 0), None
    )
    return {
        "cli_calls": len(calls),
        "cli_failed": len(failed),
        "invalid_commands": outcomes["usage"],
        "spec_errors": outcomes["spec"],
        "plan_errors": outcomes["plan"],
        "api_errors": outcomes["api_4xx"] + outcomes["api_5xx"],
        "internal_errors": outcomes["internal"],
        "other_errors": outcomes["other"],
        "help_calls": kinds["help"],
        "docs_calls": kinds["docs"],
        "docs_topics": sorted({" ".join(c["argv"][1:3]) for c in docs}),
        "docs_bytes": sum(c["stdout_bytes"] for c in docs),
        "read_calls": kinds["read"],
        "dry_runs": kinds["dry_run"],
        "applies": kinds["apply"],
        "failed_applies": sum(1 for c in applies if c["exit_code"] != 0),
        "calls_before_first_apply": first_ok_apply,
        "cli_output_bytes": sum(c["stdout_bytes"] + c["stderr_bytes"] for c in calls),
        "cli_seconds": round(sum(c["duration_ms"] for c in calls) / 1000, 1),
    }


#: Shell text that means the session went around the CLI.
_OFF_PATH = {
    "raw_api_calls": re.compile(r"\bcurl\b|\bwget\b|httpx|requests\.(get|post|put)"),
    "source_peeks": re.compile(r"site-packages|kizen_builder|/src/"),
}


def transcript_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Numbers from a Claude Code `--output-format stream-json` transcript."""
    tools: Counter[str] = Counter()
    off_path: Counter[str] = Counter()
    result: dict[str, Any] = {}
    for e in events:
        if e.get("type") == "assistant":
            for block in (e.get("message") or {}).get("content") or []:
                if block.get("type") != "tool_use":
                    continue
                tools[block.get("name", "?")] += 1
                command = str((block.get("input") or {}).get("command", ""))
                for name, pattern in _OFF_PATH.items():
                    if pattern.search(command):
                        off_path[name] += 1
        elif e.get("type") == "result":
            result = e
    if not tools and not result:
        return {}
    usage = result.get("usage") or {}
    return {
        "tool_calls": sum(tools.values()),
        "tool_calls_by_tool": dict(tools.most_common()),
        "turns": result.get("num_turns"),
        "input_tokens": (usage.get("input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0),
        "output_tokens": usage.get("output_tokens"),
        "cost_usd": result.get("total_cost_usd"),
        "agent_seconds": round((result.get("duration_ms") or 0) / 1000, 1),
        "agent_error": bool(result.get("is_error")),
        "raw_api_calls": off_path["raw_api_calls"],
        "source_peeks": off_path["source_peeks"],
    }


def run_metrics(run_dir: Path) -> dict[str, Any]:
    out = cli_metrics(read_jsonl(run_dir / "kizen.jsonl"))
    out.update(transcript_metrics(read_jsonl(run_dir / "transcript.jsonl")))
    return out
