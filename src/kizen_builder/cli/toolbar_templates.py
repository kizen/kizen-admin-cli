"""`kizen toolbar-templates` — named toolbars (the bar across the top of the page)."""

from __future__ import annotations

from typing import Any

import typer
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from kizen_builder import output as out
from kizen_builder.cli._mutations import _read_spec, _run_mutation
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    app,
    cli_errors,
    console,
)
from kizen_builder.tools import toolbar_templates as tt_tools
from kizen_builder.tools.planners import toolbar_templates as tt_planners

EPILOG = "Spec shape and examples: see `kizen docs show toolbar-templates`"
TEMPLATE_ARG_HELP = "Toolbar template UUID or exact name."

toolbar_templates_app = typer.Typer(
    help="Toolbar templates: named top-of-page toolbars. Nothing here applies one "
    "to anyone.",
    no_args_is_help=True,
)
app.add_typer(toolbar_templates_app, name="toolbar-templates")


@toolbar_templates_app.command("list", epilog=EPILOG)
def toolbar_templates_list(
    search: str = typer.Option(
        "", "--search", help="Filter by name (server-side search)."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List toolbar templates: name, owner, top-level entries, updated, id."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        rows = [
            tt_tools.template_summary(t)
            for t in tt_tools.list_toolbar_templates(search or None)
        ]

    def table() -> None:
        t = Table(title="Toolbar templates")
        t.add_column("name")
        t.add_column("owner")
        t.add_column("entries", justify="right")
        t.add_column("updated")
        t.add_column("id")
        for r in rows:
            t.add_row(
                Text(r["name"] or ""),
                Text(r["owner"]),
                str(r["entries"]),
                r["updated"] or "",
                r["id"] or "",
            )
        console.print(t)

    out.render(
        fmt,
        json_data=rows,
        table=table,
        csv_rows=rows,
        csv_columns=[
            out.Column("name", "name"),
            out.Column("owner", "owner"),
            out.Column("entries", "entries"),
            out.Column("updated", "updated"),
            out.Column("id", "id"),
        ],
    )


def _add_branch(parent: Tree, entry: dict[str, Any]) -> None:
    kind, ref = tt_tools.entry_kind(entry)
    node = parent.add(
        Text.assemble(
            (tt_tools.entry_label(entry), "bold"), f"  {kind}  ", (ref, "dim")
        )
    )
    for child in entry.get("children") or []:
        _add_branch(node, child)


@toolbar_templates_app.command("get", epilog=EPILOG)
def toolbar_templates_get(
    template: str = typer.Argument(..., help=TEMPLATE_ARG_HELP),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit {id, name, owner, sharing_settings, entries}, with entries "
        "in the spec shape `update` takes.",
    ),
    raw: bool = typer.Option(False, "--raw", help="Emit the raw API response."),
) -> None:
    """Show one toolbar template's entries as a tree (label, kind, ref)."""
    with cli_errors(LookupError):
        item = tt_tools.find_toolbar_template(template)
        if raw:
            out.emit_json(item)
            return
        if json_out:
            out.emit_json(tt_tools.to_spec(item, tt_tools.object_api_names()))
            return

    tree = Tree(
        Text.assemble(
            (item.get("name") or "", "bold"),
            f"  owner {tt_tools.owner_name(item) or '-'}  ",
            (item.get("id") or "", "dim"),
        )
    )
    for entry in tt_tools.template_entries(item):
        _add_branch(tree, entry)
    console.print(tree)


SPEC_FILE_HELP = "Path to a JSON toolbar template spec. Default: read from stdin."
DRY_RUN_OPTION = typer.Option(
    False, "--dry-run", help="Show the plan without applying."
)
YES_OPTION = typer.Option(
    False, "--yes", "-y", help="Skip the y/N confirmation prompt."
)
JSON_PLAN_OPTION = typer.Option(
    False, "--json", help="Emit JSON (plan with --dry-run, results otherwise)."
)


@toolbar_templates_app.command("create", epilog=EPILOG)
def toolbar_templates_create(
    spec_file: str = typer.Option("", "--spec-file", help=SPEC_FILE_HELP),
    dry_run: bool = DRY_RUN_OPTION,
    yes: bool = YES_OPTION,
    json_out: bool = JSON_PLAN_OPTION,
) -> None:
    """Create a toolbar template from a spec.

    Plugin pages are built from the installed plugin. A new template is
    private to its owner unless the spec's `sharing_settings` says otherwise.
    """
    spec, from_stdin = _read_spec(spec_file, what="toolbar template")
    _run_mutation(
        lambda: tt_planners.plan_create_toolbar_template(spec),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


@toolbar_templates_app.command("update", epilog=EPILOG)
def toolbar_templates_update(
    template: str = typer.Argument(..., help=TEMPLATE_ARG_HELP),
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Path to a JSON toolbar template spec. Default: read from stdin; "
        "none re-saves the template with current plugin pages.",
    ),
    dry_run: bool = DRY_RUN_OPTION,
    yes: bool = YES_OPTION,
    json_out: bool = JSON_PLAN_OPTION,
) -> None:
    """Update a toolbar template. Keys the spec leaves out keep their value.

    `entries` replaces the whole list. Every update rebuilds plugin pages
    from the installed plugin, so with no spec this re-saves the template
    after a plugin release. Plugin toolbar-item references are kept as
    references.
    """
    spec, from_stdin = _read_spec(spec_file, what="toolbar template", optional=True)
    _run_mutation(
        lambda: tt_planners.plan_update_toolbar_template(template, spec),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


@toolbar_templates_app.command("delete", epilog=EPILOG)
def toolbar_templates_delete(
    template: str = typer.Argument(..., help=TEMPLATE_ARG_HELP),
    dry_run: bool = DRY_RUN_OPTION,
    yes: bool = YES_OPTION,
    json_out: bool = JSON_PLAN_OPTION,
) -> None:
    """Delete a toolbar template."""
    _run_mutation(
        lambda: tt_planners.plan_delete_toolbar_template(template),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )
