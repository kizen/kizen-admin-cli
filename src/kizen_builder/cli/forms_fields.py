"""`kizen forms fields` / `kizen surveys fields` — the field and field-option
sub-apps, attached to a form-like app by `add_field_commands`.

Split out of `forms.py` only for size; it is part of the same factory and is
called once per kind. Nothing here registers on the root `app`.
"""

from __future__ import annotations

from typing import Any, Literal

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._mutations import _read_spec, _run_mutation
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    cli_errors,
    console,
    err_console,
    warn_renamed_flag,
)
from kizen_builder.tools import forms as form_tools
from kizen_builder.tools.planners import forms as form_planners


def add_field_commands(
    form_app: typer.Typer, *, base_path: str, kind: Literal["form", "survey"]
) -> None:
    """Attach the `fields` sub-app (and, under it, `options`) to a form app."""
    Title = kind.capitalize()

    fields_app = typer.Typer(
        help=f"Manage the fields on a {kind}.", no_args_is_help=True
    )
    form_app.add_typer(fields_app, name="fields")

    @fields_app.command("list")
    def _fields_list(
        identifier: str = typer.Argument(..., help=f"{Title} api_name or UUID."),
        output: str = OUTPUT_OPTION,
        json_out: bool = JSON_OPTION,
    ) -> None:
        """List the fields on a form/survey (same view as `get`)."""
        fmt = out.resolve_format(output, json_out)
        with cli_errors(LookupError):
            result = form_tools.get_form(identifier, base_path=base_path)
        fields = result["fields"]

        def table() -> None:
            t = Table(title=f"Fields — {result['name']}")
            t.add_column("order", justify="right")
            t.add_column("api_name")
            t.add_column("display_name")
            t.add_column("type")
            t.add_column("req", justify="center")
            t.add_column("id")
            for f in fields:
                t.add_row(
                    str(f.get("order") if f.get("order") is not None else ""),
                    f.get("api_name") or "",
                    f.get("display_name") or "",
                    f.get("field_type") or "",
                    "✓" if f.get("is_required") else "",
                    f.get("id") or "",
                )
            console.print(t)

        out.render(
            fmt,
            json_data=fields,
            table=table,
            csv_rows=fields,
            csv_columns=[
                out.Column("order", "order"),
                out.Column("api_name", "api_name"),
                out.Column("display_name", "display_name"),
                out.Column("field_type", "field_type"),
                out.Column("is_required", "is_required"),
                out.Column("id", "id"),
            ],
        )

    @fields_app.command(
        "create",
        epilog="Bulk spec shape (a FormFieldDef list): see `kizen docs show form`",
    )
    def _fields_create(
        identifier: str = typer.Argument(..., help=f"{Title} api_name or UUID."),
        api_name: str = typer.Option(
            "", "--api-name", help="Field api_name (optional; server derives one)."
        ),
        display_name: str = typer.Option(
            "", "--name", help="Field display name (single-field mode)."
        ),
        field_type: str = typer.Option(
            "", "--type", help="Field type (text, dropdown, rating, etc.)."
        ),
        description: str = typer.Option("", "--description", help="Field description."),
        required: bool = typer.Option(
            False, "--required", help="Mark the field required."
        ),
        hidden: bool = typer.Option(
            False, "--hidden", help="Hide the field by default."
        ),
        option: list[str] = typer.Option(
            [], "--option", help="Choice option (repeatable)."
        ),
        spec_file: str = typer.Option(
            "",
            "--spec-file",
            help="Bulk mode: JSON list of FormFieldDef objects (or an object with a "
            '"fields" list). Omit with stdin to read from stdin.',
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
        """Add one field, or many at once from a JSON spec, to a form/survey."""
        if spec_file and display_name:
            err_console.print(
                "[red]error:[/red] pass either single-field flags or a bulk spec, not both."
            )
            raise typer.Exit(code=2)

        spec, from_stdin = (
            (None, False) if display_name else _read_spec(spec_file, optional=True)
        )
        if spec is not None:
            spec_fields = spec.get("fields", spec) if isinstance(spec, dict) else spec
            if not isinstance(spec_fields, list):
                err_console.print(
                    '[red]error:[/red] spec must be a JSON list of fields (or {"fields": [...]}).'
                )
                raise typer.Exit(code=2)
            _run_mutation(
                lambda: form_planners.plan_create_form_fields(
                    identifier, spec_fields, base_path=base_path, kind=kind
                ),
                dry_run=dry_run,
                yes=yes,
                json_out=json_out,
                stdin_consumed=from_stdin,
            )
            return

        if not display_name or not field_type:
            err_console.print(
                "[red]error:[/red] single-field create needs --name and --type."
            )
            raise typer.Exit(code=2)
        field_dict: dict[str, Any] = {
            "name": display_name,
            "field_type": field_type,
            "required": required,
            "hidden": hidden,
        }
        if option:
            field_dict["options"] = option
        if api_name:
            field_dict["api_name"] = api_name
        if description:
            field_dict["description"] = description

        _run_mutation(
            lambda: form_planners.plan_create_form_fields(
                identifier, [field_dict], base_path=base_path, kind=kind
            ),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
        )

    @fields_app.command("update")
    def _fields_update(
        identifier: str = typer.Argument(..., help=f"{Title} api_name or UUID."),
        field_api_name: str = typer.Argument(..., help="Field api_name to update."),
        name: str = typer.Option("", "--name", help="New display name."),
        description: str = typer.Option("", "--description", help="New description."),
        required: bool | None = typer.Option(
            None, "--required/--optional", help="Set the required flag."
        ),
        read_only: bool | None = typer.Option(
            None, "--read-only/--editable", help="Set the read-only flag."
        ),
        hidden: bool | None = typer.Option(
            None, "--hidden/--visible", help="Set the hidden flag."
        ),
        order: int = typer.Option(-1, "--order", help="New order index (>=0)."),
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
        """Update one field on a form/survey. Only the flags you set are changed."""
        changes: dict[str, Any] = {}
        if name:
            changes["name"] = name
        if description:
            changes["description"] = description
        if required is not None:
            changes["required"] = required
        if read_only is not None:
            changes["read_only"] = read_only
        if hidden is not None:
            changes["hidden"] = hidden
        if order >= 0:
            changes["order"] = order

        if not changes:
            err_console.print("[red]error:[/red] no changes given.")
            raise typer.Exit(code=2)

        _run_mutation(
            lambda: form_planners.plan_update_form_field(
                identifier, field_api_name, changes, base_path=base_path, kind=kind
            ),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
        )

    @fields_app.command("delete")
    def _fields_delete(
        identifier: str = typer.Argument(..., help=f"{Title} api_name or UUID."),
        field_api_name: str = typer.Argument(..., help="Field api_name to delete."),
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
        """Delete one field from a form/survey."""
        _run_mutation(
            lambda: form_planners.plan_delete_form_field(
                identifier, field_api_name, base_path=base_path, kind=kind
            ),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
        )

    _add_field_option_commands(fields_app, base_path=base_path, kind=kind)


def _add_field_option_commands(
    fields_app: typer.Typer, *, base_path: str, kind: Literal["form", "survey"]
) -> None:
    """Attach `fields options add` / `remove`."""
    Title = kind.capitalize()

    field_options_app = typer.Typer(
        help=f"Add or remove options on a select-type {kind} field.",
        no_args_is_help=True,
    )
    fields_app.add_typer(field_options_app, name="options")

    @field_options_app.command("add")
    def _field_options_add(
        identifier: str = typer.Argument(..., help=f"{Title} api_name or UUID."),
        field_api_name: str = typer.Argument(..., help="Field api_name."),
        option: list[str] = typer.Option(
            [], "--option", help="Option label to add (repeatable)."
        ),
        option_short: list[str] = typer.Option([], "-o", hidden=True),
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
        """Add options to a select-type form/survey field. Existing names are skipped."""
        if option_short:
            warn_renamed_flag("-o", "--option")
            option = [*option, *option_short]
        if not option:
            err_console.print("[red]error:[/red] pass at least one --option.")
            raise typer.Exit(code=2)
        _run_mutation(
            lambda: form_planners.plan_add_form_field_options(
                identifier, field_api_name, option, base_path=base_path, kind=kind
            ),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
        )

    @field_options_app.command("remove")
    def _field_options_remove(
        identifier: str = typer.Argument(..., help=f"{Title} api_name or UUID."),
        field_api_name: str = typer.Argument(..., help="Field api_name."),
        option: str = typer.Argument(
            ..., help="Option to remove (name, code, or UUID)."
        ),
        remap_to: str = typer.Option(
            "",
            "--remap-to",
            help="Move records using the removed option onto this option first.",
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
        """Remove one option from a select-type form/survey field."""
        _run_mutation(
            lambda: form_planners.plan_remove_form_field_option(
                identifier,
                field_api_name,
                option,
                remap_to=remap_to or None,
                base_path=base_path,
                kind=kind,
            ),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
        )
