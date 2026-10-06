"""`kizen automations steps` — patch one step via GET → translate → mutate → PUT."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

import typer
from rich.markup import escape
from rich.prompt import Confirm

from kizen_builder.cli._mutations import _read_spec
from kizen_builder.cli._shared import cli_errors, console, err_console
from kizen_builder.cli.automations import autos_app
from kizen_builder.tools import automations as auto_tools
from kizen_builder.tools import steps as step_tools
from kizen_builder.tools.plans import PlanError

steps_app = typer.Typer(
    help=(
        "Step-level surgery on one automation. Each verb fetches live state, "
        "mutates one node of the translated payload, validates the whole step "
        "graph, and PUTs atomically. Step keys come from "
        "`kizen automations show`."
    ),
    no_args_is_help=True,
)
autos_app.add_typer(steps_app, name="steps")


def _detect_message_auto_versions(diff: list[dict[str, Any]]) -> list[str]:
    """Surface auto-versioned automation-message resources as their own
    callout, instead of letting them hide inside a large per-field diff dump.

    Kizen silently clones a message (`notify_member_via_email`/
    `send_related_contact_email`'s `email` association) when it's referenced
    by more than one live step — e.g. adding a second step that points at an
    already-used message causes the FIRST step's message id/name to change
    underneath it, even though that step wasn't directly touched by this
    patch. Detected by pairing up `<prefix>.email.id` / `<prefix>.email.name`
    changes that land on the same step path.
    """
    by_prefix: dict[str, dict[str, Any]] = {}
    for d in diff:
        for suffix in (".email.id", ".email.name"):
            if d["path"].endswith(suffix):
                prefix = d["path"][: -len(suffix)]
                by_prefix.setdefault(prefix, {})[suffix] = d
    callouts = []
    for prefix, changes in sorted(by_prefix.items()):
        name_change = changes.get(".email.name")
        id_change = changes.get(".email.id")
        if name_change and id_change:
            callouts.append(
                f"automation_message auto-versioned at [yellow]{prefix}[/yellow]: "
                f"{name_change['before']!r} → {name_change['after']!r} "
                f"(id {id_change['before']!r} → {id_change['after']!r})"
            )
    return callouts


def _run_step_patch(
    api_name: str,
    mutate: Callable[[dict[str, Any], dict[str, Any]], Any],
    dry_run: bool,
    yes: bool,
    json_out: bool,
    stdin_consumed: bool = False,
) -> None:
    """Preview → confirm → apply loop for step-level patches.

    The apply re-runs the whole GET→mutate→validate pipeline against fresh
    live state (plans are ephemeral); last_revision makes a concurrent edit
    between preview and apply fail loudly instead of clobbering.
    """
    with cli_errors(LookupError, PlanError):
        preview = auto_tools.patch_steps(api_name, mutate, execute=False)

    if not json_out:
        console.print(
            f"[bold]{api_name}[/bold]  [dim](rev {preview['revision_before']}, "
            f"{len(preview['payload']['steps'])} steps after patch)[/dim]"
        )
        console.print(
            json.dumps(preview["report"], indent=2, default=list),
            markup=False,
            emoji=False,
            soft_wrap=True,
        )
        if preview["validation_problems"]:
            console.print("[red]step graph INVALID — refusing to apply:[/red]")
            for p in preview["validation_problems"]:
                console.print(f"  [red]-[/red] {p}")
    if preview["validation_problems"]:
        if json_out:
            typer.echo(json.dumps(preview, indent=2, default=list))
        raise typer.Exit(code=1)

    if dry_run:
        if json_out:
            typer.echo(json.dumps(preview, indent=2, default=list))
        else:
            console.print("[green]validated[/green] — dry run, nothing applied")
        return

    if not yes:
        if stdin_consumed or not sys.stdin.isatty():
            err_console.print(
                "[red]error:[/red] can't prompt (stdin consumed). "
                "Preview with --dry-run, then re-run with --yes."
            )
            raise typer.Exit(code=2)
        if not Confirm.ask("Apply this step patch?", default=False):
            console.print("[yellow]aborted[/yellow]")
            raise typer.Exit(code=1)

    with cli_errors(LookupError, PlanError):
        result = auto_tools.patch_steps(api_name, mutate, execute=True)

    if json_out:
        typer.echo(json.dumps(result, indent=2, default=list))
        return
    if not result["executed"]:
        console.print("[red]graph invalid at apply time:[/red]")
        for p in result["validation_problems"]:
            console.print(f"  [red]-[/red] {p}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]PUT ok[/green] — revision {result['revision_before']} → "
        f"{result['revision_after']}"
    )
    diff = result.get("diff") or []
    for callout in _detect_message_auto_versions(diff):
        console.print(f"[bold yellow]![/bold yellow] {callout}")
    console.print(f"[bold]what changed[/bold] ({len(diff)} semantic diff(s)):")
    for d in diff[:15]:
        console.print(
            f"  [yellow]{escape(str(d['path']))}[/yellow]: "
            f"{escape(repr(d['before']))} → {escape(repr(d['after']))}",
            emoji=False,
        )
    if len(diff) > 15:
        console.print(
            f"  [dim]… and {len(diff) - 15} more — the diff is positional, so "
            "an insert/remove mid-graph shifts every later step; verify with "
            "`kizen automations show` (or re-run with --json for the full "
            "diff)[/dim]"
        )
    if not diff:
        console.print(
            "  [yellow]none — the patch was a semantic no-op. "
            "Check that this was intended.[/yellow]"
        )


@steps_app.command("get")
def steps_get(
    api_name: str = typer.Argument(..., help="Automation api_name."),
    key: str = typer.Argument(..., help="Step key from `kizen automations show`."),
) -> None:
    """Print one step's wire JSON — the starting point for `steps edit`."""
    with cli_errors(LookupError, PlanError):
        result = auto_tools.show_automation(api_name)
        step = step_tools.find_step(result["payload"], key)
    typer.echo(json.dumps(step, indent=2))


@steps_app.command(
    "edit",
    epilog="Patch shape (one step; start from 'steps get'): see `kizen docs show automation-step`",
)
def steps_edit(
    api_name: str = typer.Argument(..., help="Automation api_name."),
    key: str = typer.Argument(..., help="Step key from `kizen automations show`."),
    spec_file: str = typer.Option(
        "", "--spec-file", help="JSON patch file. Default: read from stdin."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without applying."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the y/N confirmation."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Patch one step: top-level keys in the JSON replace the step's.

    A config block (`action_*` / `step_*`) replaces wholesale — start
    from `steps get` output and modify. Blocks accept the same authoring
    shapes as create specs (field_refs resolve against the live env).
    `key` and `type` are immutable; re-parenting via `parent_key` /
    `parent_yes_no` is allowed and graph-validated.
    """
    patch, from_stdin = _read_spec(spec_file)

    def mutate(payload: dict[str, Any], raw: dict[str, Any]) -> Any:
        step = step_tools.find_step(payload, key)
        target_object = (raw.get("custom_object") or {}).get("name")
        normalized = auto_tools.normalize_step_patch(
            dict(patch), key, step["type"], payload, target_object
        )
        return step_tools.edit_step(payload, key, normalized)

    _run_step_patch(api_name, mutate, dry_run, yes, json_out, from_stdin)


@steps_app.command(
    "add",
    epilog="Step spec shape (one AutomationDef step): see `kizen docs show automation-step`",
)
def steps_add(
    api_name: str = typer.Argument(..., help="Automation api_name."),
    parent: str = typer.Option(
        "", "--parent", help="Step key to attach under. Omit with --root."
    ),
    root: bool = typer.Option(
        False,
        "--root",
        help="Insert as the new first step (old root becomes its child).",
    ),
    branch: str = typer.Option(
        "",
        "--branch",
        help="'yes' or 'no' — required branch when parent is a condition/goal.",
    ),
    leaf: bool = typer.Option(
        False,
        "--leaf",
        help="Append as a leaf instead of inserting into the chain "
        "(parent's existing children stay put).",
    ),
    spec_file: str = typer.Option(
        "", "--spec-file", help="JSON step spec file. Default: read from stdin."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without applying."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the y/N confirmation."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Insert one step. The spec is a single AutomationDef step (step_type +
    config block; field_refs welcome); placement comes from the flags, and
    by default the parent's existing children move under the new step."""
    if bool(parent) == root:
        err_console.print("[red]error:[/red] pass exactly one of --parent or --root")
        raise typer.Exit(code=2)
    spec, from_stdin = _read_spec(spec_file)

    def mutate(payload: dict[str, Any], raw: dict[str, Any]) -> Any:
        target_object = (raw.get("custom_object") or {}).get("name")
        wire = auto_tools.build_wire_step(spec, payload, target_object)
        return step_tools.insert_step(
            payload,
            wire,
            parent_key=parent or None,
            branch=branch,
            adopt_children=not leaf,
        )

    _run_step_patch(api_name, mutate, dry_run, yes, json_out, from_stdin)


@steps_app.command("remove")
def steps_remove(
    api_name: str = typer.Argument(..., help="Automation api_name."),
    key: str = typer.Argument(..., help="Step key from `kizen automations show`."),
    cascade: bool = typer.Option(
        False, "--cascade", help="Also remove every descendant of the step."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without applying."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the y/N confirmation."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Splice one step out of the graph (children adopt its parent, go_to
    references retarget). A condition/goal with children needs --cascade."""

    def mutate(payload: dict[str, Any], raw: dict[str, Any]) -> Any:
        return step_tools.remove_step(payload, key, cascade=cascade)

    _run_step_patch(api_name, mutate, dry_run, yes, json_out)
