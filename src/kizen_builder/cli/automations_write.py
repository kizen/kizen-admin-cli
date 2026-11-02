"""`kizen automations` mutations: create/update, the lightweight lifecycle
flips (activate/deactivate/move), and duplicate/delete.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import typer

from kizen_builder.cli._mutations import (
    _confirm_or_abort,
    _read_spec,
    _run_mutation,
)
from kizen_builder.cli._shared import cli_errors, console
from kizen_builder.cli.automations import autos_app
from kizen_builder.tools import automations as auto_tools
from kizen_builder.tools.planners import automations as auto_planners
from kizen_builder.tools.plans import PlanError


@autos_app.command(
    "create",
    epilog="Spec shape (an AutomationDef: triggers + step graph): see `kizen docs show automation`",
)
def autos_create(
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Path to a JSON file containing an AutomationDef. Default: read from stdin.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Create one automation from a JSON AutomationDef (--spec-file or stdin)."""
    spec_dict, from_stdin = _read_spec(spec_file)
    _run_mutation(
        lambda: auto_planners.plan_create_automation(spec_dict),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


@autos_app.command(
    "update",
    epilog="Spec shape (an AutomationDef: triggers + step graph): see `kizen docs show automation`",
)
def autos_update(
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Path to JSON AutomationDef. Default: read from stdin.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Update an existing automation from a JSON AutomationDef (--spec-file or stdin).

    The plan carries the full PUT body including last_revision — a concurrent
    edit between plan and apply surfaces as an API error. Keys the spec leaves
    out keep their live values on steps that carry their `id`; see `kizen docs
    show automation`.
    """
    spec_dict, from_stdin = _read_spec(spec_file)
    _run_mutation(
        lambda: auto_planners.plan_update_automation(spec_dict),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


# ---------------------------------------------------------------------------
# automations lifecycle — activate/deactivate/move (lightweight field flips,
# no full AutomationDef required) and duplicate/delete (Plan-based)
# ---------------------------------------------------------------------------


def _before_after(result: dict[str, Any]) -> tuple[str, str]:
    if result["field"] == "folder":
        return auto_planners.folder_labels(result["before"] or {}, result["after"])
    return repr(result["before"]), repr(result["after"])


def _run_field_patch(
    api_name: str,
    call: Callable[[bool], dict[str, Any]],
    dry_run: bool,
    yes: bool,
    json_out: bool,
) -> None:
    """Preview -> confirm -> apply loop for one-field automation patches
    (`activate`/`deactivate`/`move`). Lighter than `_run_mutation`: no Plan
    object, just a before/after value pair for the one field being flipped."""
    with cli_errors(LookupError, PlanError):
        preview = call(False)

    if not json_out:
        console.print(
            f"[bold]{api_name}[/bold]  [dim](rev {preview['revision_before']})[/dim]"
        )
        before, after = _before_after(preview)
        console.print(f"  {preview['field']}: {before} → {after}")
        if preview["no_op"]:
            console.print("[yellow]no-op — already at the target value[/yellow]")

    if dry_run:
        if json_out:
            typer.echo(json.dumps(preview, indent=2, default=list))
        else:
            console.print("[green]validated[/green] — dry run, nothing applied")
        return

    if preview["no_op"]:
        if json_out:
            typer.echo(json.dumps(preview, indent=2, default=list))
        return

    _confirm_or_abort(
        f"Set {preview['field']} to {_before_after(preview)[1]}?",
        yes=yes,
        hint="Preview with --dry-run, then re-run with --yes.",
    )

    with cli_errors(LookupError, PlanError):
        result = call(True)

    if json_out:
        typer.echo(json.dumps(result, indent=2, default=list))
        return
    before, after = _before_after(result)
    console.print(f"[green]done[/green] — {result['field']}: {before} → {after}")


@autos_app.command("activate")
def autos_activate(
    api_name: str = typer.Argument(..., help="Automation api_name."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without applying."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the y/N confirmation."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Flip one automation's `active` flag to true, without re-authoring the full spec."""
    _run_field_patch(
        api_name,
        lambda execute: auto_tools.set_active(api_name, True, execute=execute),
        dry_run,
        yes,
        json_out,
    )


@autos_app.command("deactivate")
def autos_deactivate(
    api_name: str = typer.Argument(..., help="Automation api_name."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without applying."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the y/N confirmation."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Flip one automation's `active` flag to false, without re-authoring the full spec."""
    _run_field_patch(
        api_name,
        lambda execute: auto_tools.set_active(api_name, False, execute=execute),
        dry_run,
        yes,
        json_out,
    )


@autos_app.command("duplicate")
def autos_duplicate(
    api_name: str = typer.Argument(..., help="Automation api_name to duplicate."),
    name: str = typer.Option(
        "",
        "--name",
        help=(
            "Requested name for the duplicate. NOTE: confirmed live that the "
            "server ignores this and always auto-names the copy "
            "'<original> (copy #N)' itself — kept for forward compatibility."
        ),
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Duplicate one automation. The copy is named/numbered by the server."""
    _run_mutation(
        lambda: auto_planners.plan_duplicate_automation(api_name, name=name or None),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@autos_app.command("delete")
def autos_delete(
    api_name: str = typer.Argument(..., help="Automation api_name to delete."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
    ),
) -> None:
    """Delete one automation. Irreversible."""
    _run_mutation(
        lambda: auto_planners.plan_delete_automation(api_name),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@autos_app.command("move")
def autos_move(
    api_name: str = typer.Argument(..., help="Automation api_name to move."),
    folder: str = typer.Option(
        "", "--folder", help="Folder name/UUID to move into. Omit to move to the root."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without applying."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the y/N confirmation."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Move one automation into a folder, or to the root when --folder is left out.

    The automation itself isn't rewritten, so this works whatever steps it has.
    """
    with cli_errors(PlanError):
        match = auto_planners._find_folder(
            folder or "<business_root>", auto_tools.list_folders()
        )
    folder_id, folder_name = match["id"], match.get("name")
    _run_field_patch(
        api_name,
        lambda execute: auto_tools.move_to_folder(
            api_name, folder_id, folder_name, execute=execute
        ),
        dry_run,
        yes,
        json_out,
    )
