"""`kizen forms` and `kizen surveys` — structurally identical, so one factory
builds both apps.
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
    _short,
    app,
    cli_errors,
    console,
    err_console,
    read_json_file,
)
from kizen_builder.cli.forms_fields import add_field_commands
from kizen_builder.tools import forms as form_tools
from kizen_builder.tools.planners import forms as form_planners


def _build_form_like_app(
    base_path: str, kind: Literal["form", "survey"]
) -> typer.Typer:
    """Build a `kizen forms`/`kizen surveys` typer app.

    Forms and surveys are the same API shape under two different base paths
    (`/api/forms` vs `/api/surveys`); this factory is called once per
    kind so the command surface (and its tests) aren't duplicated by hand.
    `kind` is `"form"` or `"survey"` — used in help text and passed
    through to the tools/planners so plan-op keys read naturally.

    The commands are attached in three passes, in the order they have to be
    registered: reads, writes, then the `fields` sub-app. Typer renders
    `--help` in registration order, so that sequence is the help output.
    """
    form_app = typer.Typer(
        help=f"Create, read, update, and delete {kind}s, their fields, and their "
        "visual page layout (form_ui).",
        no_args_is_help=True,
    )
    _add_read_commands(form_app, base_path=base_path, kind=kind)
    _add_write_commands(form_app, base_path=base_path, kind=kind)
    add_field_commands(form_app, base_path=base_path, kind=kind)
    return form_app


def _add_read_commands(
    form_app: typer.Typer, *, base_path: str, kind: Literal["form", "survey"]
) -> None:
    """`list` and `get`."""
    Title = kind.capitalize()

    @form_app.command("list")
    def _list(
        search: str = typer.Option("", "--search", help="Filter by name text."),
        output: str = OUTPUT_OPTION,
        json_out: bool = JSON_OPTION,
    ) -> None:
        """List forms/surveys in the configured env."""
        fmt = out.resolve_format(output, json_out)
        with cli_errors():
            items = form_tools.list_forms(base_path=base_path, search=search or None)

        def table() -> None:
            t = Table(title=f"{Title}s")
            t.add_column("name")
            t.add_column("api_name")
            t.add_column("submissions", justify="right")
            t.add_column("template", justify="center")
            t.add_column("id")
            for item in items:
                t.add_row(
                    item.get("name") or "",
                    item.get("api_name") or "",
                    str(
                        item.get("n_submissions")
                        if item.get("n_submissions") is not None
                        else ""
                    ),
                    item.get("template_type") or "",
                    item.get("id") or "",
                )
            console.print(t)

        out.render(
            fmt,
            json_data=items,
            table=table,
            csv_rows=items,
            csv_columns=[
                out.Column("name", "name"),
                out.Column("api_name", "api_name"),
                out.Column("n_submissions", "n_submissions"),
                out.Column("template_type", "template_type"),
                out.Column("id", "id"),
            ],
        )

    @form_app.command("get")
    def _get(
        identifier: str = typer.Argument(..., help=f"{Title} api_name or UUID."),
        output: str = OUTPUT_OPTION,
        json_out: bool = JSON_OPTION,
    ) -> None:
        """Show one form/survey: metadata and fields."""
        fmt = out.resolve_format(output, json_out)
        with cli_errors(LookupError):
            result = form_tools.get_form(identifier, base_path=base_path)

        def table() -> None:
            console.print(
                f"[bold]{result['name']}[/bold]  "
                f"[dim]({result['api_name']}, id={result['id']})[/dim]"
            )
            meta_bits = [
                f"template={result.get('template_type')}",
                f"related_object={result.get('related_object')}",
                f"submissions={result.get('n_submissions')}",
            ]
            if result.get("description"):
                meta_bits.append(f"description={result['description']}")
            console.print("[dim]" + "  ".join(meta_bits) + "[/dim]")

            fld_table = Table(title="Fields")
            fld_table.add_column("order", justify="right")
            fld_table.add_column("api_name")
            fld_table.add_column("display_name")
            fld_table.add_column("type")
            fld_table.add_column("req", justify="center")
            fld_table.add_column("hidden", justify="center")
            fld_table.add_column("options", style="dim")
            fld_table.add_column("id")
            for f in result["fields"]:
                opts = ", ".join(
                    o["name"] for o in (f.get("options") or []) if o.get("name")
                )
                fld_table.add_row(
                    str(f.get("order") if f.get("order") is not None else ""),
                    f.get("api_name") or "",
                    f.get("display_name") or "",
                    f.get("field_type") or "",
                    "✓" if f.get("is_required") else "",
                    "✓" if f.get("is_hidden") else "",
                    _short(opts, 40),
                    f.get("id") or "",
                )
            console.print(fld_table)

        out.render(
            fmt,
            json_data={k: v for k, v in result.items() if k != "raw"},
            table=table,
            csv_rows=result["fields"],
            csv_columns=[
                out.Column("order", "order"),
                out.Column("api_name", "api_name"),
                out.Column("display_name", "display_name"),
                out.Column("field_type", "field_type"),
                out.Column("is_required", "is_required"),
                out.Column("is_hidden", "is_hidden"),
                out.Column("id", "id"),
            ],
        )


def _add_write_commands(
    form_app: typer.Typer, *, base_path: str, kind: Literal["form", "survey"]
) -> None:
    """`create`, `update`, `set-ui`, `delete`, `duplicate`."""
    Title = kind.capitalize()

    @form_app.command(
        "create",
        epilog="Spec shape (a FormDef, may include fields): see `kizen docs show form`",
    )
    def _create(
        name: str = typer.Option(
            "", "--name", help="Display name (single-object mode)."
        ),
        api_name: str = typer.Option(
            "", "--api-name", help="api_name (optional; server derives one)."
        ),
        description: str = typer.Option(
            "", "--description", help=f"{Title} description."
        ),
        related_object: str = typer.Option(
            "",
            "--related-object",
            help="api_name of the custom object submissions attach records to (required).",
        ),
        template_type: str = typer.Option(
            "modern", "--template-type", help="modern | open | splash."
        ),
        spec_file: str = typer.Option(
            "",
            "--spec-file",
            help="Path to a JSON FormDef (with optional inline fields). "
            "Omit with stdin to read from stdin.",
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
        """Create one form/survey from flags, or a JSON FormDef (--spec-file/stdin).

        Flag mode covers the common metadata; use a spec for inline fields or
        the other optional FormDef keys (submission_action, redirect_url, etc).
        """
        if spec_file and name:
            err_console.print(
                "[red]error:[/red] pass either --name flags or a --spec-file/stdin spec, not both."
            )
            raise typer.Exit(code=2)

        spec, from_stdin = (
            (None, False) if name else _read_spec(spec_file, optional=True)
        )
        if spec is not None:
            _run_mutation(
                lambda: form_planners.plan_create_form(
                    spec, base_path=base_path, kind=kind
                ),
                dry_run=dry_run,
                yes=yes,
                json_out=json_out,
                stdin_consumed=from_stdin,
            )
            return

        if not name:
            err_console.print(
                "[red]error:[/red] --name is required (or pass a spec via --spec-file/stdin)."
            )
            raise typer.Exit(code=2)
        if not related_object:
            err_console.print(
                "[red]error:[/red] --related-object is required (the custom object "
                "submissions attach records to)."
            )
            raise typer.Exit(code=2)

        spec_dict: dict[str, Any] = {
            "name": name,
            "related_object": related_object,
            "template_type": template_type,
        }
        if api_name:
            spec_dict["api_name"] = api_name
        if description:
            spec_dict["description"] = description

        _run_mutation(
            lambda: form_planners.plan_create_form(
                spec_dict, base_path=base_path, kind=kind
            ),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
        )

    @form_app.command(
        "update",
        epilog="Spec shape (FormDef changes; pair with set-ui to render): see `kizen docs show form`",
    )
    def _update(
        identifier: str = typer.Argument(..., help=f"{Title} api_name or UUID."),
        name: str = typer.Option("", "--name", help="New display name."),
        description: str = typer.Option("", "--description", help="New description."),
        template_type: str = typer.Option(
            "", "--template-type", help="New template_type."
        ),
        submission_action: str = typer.Option(
            "", "--submission-action", help="go_to_page | go_to_url."
        ),
        redirect_url: str = typer.Option(
            "", "--redirect-url", help="New redirect_url."
        ),
        challenge_token_required: bool | None = typer.Option(
            None,
            "--challenge-token/--no-challenge-token",
            help="Require a reCAPTCHA-style token.",
        ),
        spec_file: str = typer.Option(
            "",
            "--spec-file",
            help="Path to a JSON dict of changes (advanced; keys map to FormDef).",
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
        """Update one form/survey. Only the flags you set are changed."""
        changes: dict[str, Any] = {}
        if spec_file:
            changes = read_json_file(spec_file, "--spec-file")
        if name:
            changes["name"] = name
        if description:
            changes["description"] = description
        if template_type:
            changes["template_type"] = template_type
        if submission_action:
            changes["submission_action"] = submission_action
        if redirect_url:
            changes["redirect_url"] = redirect_url
        if challenge_token_required is not None:
            changes["challenge_token_required"] = challenge_token_required

        if not changes:
            err_console.print("[red]error:[/red] no changes given.")
            raise typer.Exit(code=2)

        _run_mutation(
            lambda: form_planners.plan_update_form(
                identifier, changes, base_path=base_path, kind=kind
            ),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
        )

    @form_app.command(
        "set-ui",
        epilog="Spec shape (the form_ui page layout): see `kizen docs show form` and reference.md",
    )
    def _set_ui(
        identifier: str = typer.Argument(..., help=f"{Title} api_name or UUID."),
        spec_file: str = typer.Option(
            "",
            "--spec-file",
            help="Path to a JSON page spec. Omit with stdin to read from stdin.",
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
        """Set the visual page layout (form_ui) for one form/survey from a JSON page spec.

        References fields by api_name (see `fields list`) instead of the raw
        craft.js blob — see `tools/forms.py::build_form_ui_from_spec` for the
        spec shape. A trailing "Thank You" page is added automatically unless
        the spec already includes a non-form page.
        """
        spec, from_stdin = _read_spec(spec_file, what=f"{kind} UI")

        with cli_errors(LookupError, ValueError):
            built = form_tools.build_form_ui_from_spec(
                identifier, spec, base_path=base_path
            )

        _run_mutation(
            lambda: form_planners.plan_update_form(
                identifier, {"form_ui": built}, base_path=base_path, kind=kind
            ),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
            stdin_consumed=from_stdin,
        )

    @form_app.command("delete")
    def _delete(
        identifier: str = typer.Argument(..., help=f"{Title} api_name or UUID."),
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
        """Delete one form/survey."""
        _run_mutation(
            lambda: form_planners.plan_delete_form(
                identifier, base_path=base_path, kind=kind
            ),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
        )

    @form_app.command("duplicate")
    def _duplicate(
        identifier: str = typer.Argument(..., help=f"{Title} api_name or UUID."),
        name: str = typer.Option(
            "", "--name", help="Name for the duplicate. Defaults to 'Copy of <name>'."
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
        """Duplicate one form/survey."""
        _run_mutation(
            lambda: form_planners.plan_duplicate_form(
                identifier, name=name or None, base_path=base_path, kind=kind
            ),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
        )


forms_app = _build_form_like_app("/api/forms", "form")
app.add_typer(forms_app, name="forms")

surveys_app = _build_form_like_app("/api/surveys", "survey")
app.add_typer(surveys_app, name="surveys")
