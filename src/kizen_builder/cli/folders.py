"""`kizen automations folders` — org/navigation for automations."""

from __future__ import annotations

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._mutations import _run_mutation
from kizen_builder.cli._shared import (
    FULL_OPTION,
    MUTATION_JSON_OPTION,
    cli_errors,
    console,
)
from kizen_builder.cli.automations import autos_app
from kizen_builder.tools import automations as auto_tools
from kizen_builder.tools.planners import automations as auto_planners

folders_app = typer.Typer(
    help="Automation folders, for organization.",
    no_args_is_help=True,
)
autos_app.add_typer(folders_app, name="folders")


@folders_app.command("list")
def folders_list(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """List automation folders."""
    with cli_errors():
        folders = auto_tools.list_folders()
    if json_out:
        out.emit_json(folders)
        return
    if not folders:
        console.print("[dim]no folders[/dim]")
        return
    table = Table(title="Automation folders")
    table.add_column("name")
    table.add_column("id")
    table.add_column("parent_id")
    for f in folders:
        table.add_row(
            f.get("name", ""), f.get("id", ""), str(f.get("parent_folder_id") or "")
        )
    console.print(table)


@folders_app.command("create")
def folders_create(
    name: str = typer.Option(..., "--name", help="Folder name."),
    parent: str = typer.Option("", "--parent", help="Parent folder name/UUID."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = MUTATION_JSON_OPTION,
    full: bool = FULL_OPTION,
) -> None:
    """Create one automation folder."""
    _run_mutation(
        lambda: auto_planners.plan_create_folder(name, parent=parent or None),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        full=full,
    )


@folders_app.command("update")
def folders_update(
    identifier: str = typer.Argument(..., help="Folder name or UUID."),
    name: str = typer.Option("", "--name", help="New name."),
    parent: str = typer.Option("", "--parent", help="New parent folder name/UUID."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = MUTATION_JSON_OPTION,
    full: bool = FULL_OPTION,
) -> None:
    """Rename a folder and/or move it under a new parent."""
    _run_mutation(
        lambda: auto_planners.plan_update_folder(
            identifier, name=name or None, parent=parent or None
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        full=full,
    )


@folders_app.command("delete")
def folders_delete(
    identifier: str = typer.Argument(..., help="Folder name or UUID."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = MUTATION_JSON_OPTION,
    full: bool = FULL_OPTION,
) -> None:
    """Delete one automation folder."""
    _run_mutation(
        lambda: auto_planners.plan_delete_folder(identifier),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        full=full,
    )
