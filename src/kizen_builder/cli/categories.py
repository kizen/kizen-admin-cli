"""`kizen categories` — field categories."""

from __future__ import annotations

from typing import Any

import typer

from kizen_builder.cli._mutations import _run_mutation
from kizen_builder.cli._shared import (
    FULL_OPTION,
    MUTATION_JSON_OPTION,
    app,
)
from kizen_builder.tools.planners import objects as object_planners

categories_app = typer.Typer(
    help="Create and update field categories on custom objects.", no_args_is_help=True
)
app.add_typer(categories_app, name="categories")


@categories_app.command("create")
def categories_create(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    api_name: str = typer.Option(
        ..., "--api-name", help="Category api_name (spec-side identifier)."
    ),
    name: str = typer.Option(..., "--name", help="Category display name."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = MUTATION_JSON_OPTION,
    full: bool = FULL_OPTION,
) -> None:
    """Create one field category on an existing object."""
    cat_dict: dict[str, Any] = {"api_name": api_name, "name": name}

    _run_mutation(
        lambda: object_planners.plan_create_category(object_api_name, cat_dict),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        full=full,
    )


@categories_app.command("update")
def categories_update(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    category_name: str = typer.Argument(
        ..., help="Current display name of the category."
    ),
    name: str = typer.Option(..., "--name", help="New display name."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = MUTATION_JSON_OPTION,
    full: bool = FULL_OPTION,
) -> None:
    """Rename one field category."""
    _run_mutation(
        lambda: object_planners.plan_update_category(
            object_api_name, category_name, {"name": name}
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        full=full,
    )


@categories_app.command("delete")
def categories_delete(
    object_api_name: str = typer.Argument(..., help="Parent object api_name."),
    category_name: str = typer.Argument(
        ..., help="Display name of the category to delete."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without applying."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = MUTATION_JSON_OPTION,
    full: bool = FULL_OPTION,
) -> None:
    """Delete one field category."""
    _run_mutation(
        lambda: object_planners.plan_delete_category(object_api_name, category_name),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        full=full,
    )
