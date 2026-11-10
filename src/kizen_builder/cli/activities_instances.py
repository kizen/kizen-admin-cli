"""`kizen activities logged` / `scheduled` — activity instances.

Logged instances are read-only: logging an activity is done in the Kizen UI
by users. Scheduled instances can be read, created, changed and deleted.
"""

from __future__ import annotations

import json

import typer
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._mutations import _run_mutation
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    _short,
    cli_errors,
    console,
)
from kizen_builder.cli.activities import _resolve_object_id, activities_app
from kizen_builder.tools import activities as act_tools
from kizen_builder.tools.planners import activities as act_planners

act_logged_app = typer.Typer(
    help="Read logged activity instances (read-only).", no_args_is_help=True
)
activities_app.add_typer(act_logged_app, name="logged")


@act_logged_app.command("get")
def act_logged_get(
    logged_id: str = typer.Argument(..., help="Logged-activity UUID."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one logged activity instance with its field values."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        result = act_tools.get_logged_activity(logged_id)

    def table() -> None:
        console.print(
            f"[bold]{result.get('activity_name')}[/bold]  [dim](logged {result.get('id')})[/dim]"
        )
        meta = [
            f"logged_at={result.get('logged_at')}",
            f"logged_by={result.get('logged_by')}",
            f"completed_at={result.get('completed_at')}",
        ]
        console.print("[dim]" + "  ".join(str(m) for m in meta) + "[/dim]")
        ents = result.get("associated_entities") or []
        if ents:
            console.print(
                "[dim]associated: "
                + ", ".join(
                    f"{e.get('display_name')} ({e.get('object_api_name')})"
                    for e in ents
                )
                + "[/dim]"
            )
        if result.get("notes"):
            console.print(f"notes: {result['notes']}")
        t = Table(title="Field values")
        t.add_column("field", style="dim")
        t.add_column("type", style="dim")
        t.add_column("value")
        for f in result.get("fields") or []:
            val = f.get("value")
            t.add_row(
                f.get("display_name") or f.get("api_name") or "",
                f.get("field_type") or "",
                json.dumps(val) if isinstance(val, (dict, list)) else str(val),
            )
        console.print(t)

    out.render(
        fmt,
        json_data={k: v for k, v in result.items() if k != "raw"},
        table=table,
        csv_rows=result.get("fields") or [],
        csv_columns=[
            out.Column("api_name", "api_name"),
            out.Column("display_name", "display_name"),
            out.Column("field_type", "field_type"),
            out.Column("value", lambda f: json.dumps(f.get("value"))),
        ],
    )


@act_logged_app.command("list")
def act_logged_list(
    identifier: str = typer.Argument(..., help="Activity api_name or UUID."),
    obj: str = typer.Option(
        "", "--object", help="Filter by custom object api_name (or UUID)."
    ),
    search: str = typer.Option("", "--search", help="Filter by text."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List logged instances of one activity type."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        object_id = _resolve_object_id(obj) if obj else None
        items = act_tools.list_logged(
            identifier, custom_object_id=object_id, search=search or None
        )

    def table() -> None:
        t = Table(title=f"Logged instances — {identifier}")
        t.add_column("id")
        t.add_column("logged_at")
        t.add_column("logged_by")
        t.add_column("associated")
        for r in items:
            t.add_row(
                r.get("id") or "",
                str(r.get("logged_at") or ""),
                str(r.get("logged_by") or ""),
                _short(r.get("associated") or "", 50),
            )
        console.print(t)

    out.render(
        fmt,
        json_data=items,
        table=table,
        csv_rows=items,
        csv_columns=[
            out.Column("id", "id"),
            out.Column("logged_at", "logged_at"),
            out.Column("logged_by", "logged_by"),
            out.Column("associated", "associated"),
        ],
    )


def _parse_record_flags(records: list[str]) -> list[tuple[str, str]]:
    """Turn repeatable `--record object:record_uuid` into pairs."""
    pairs = []
    for item in records:
        obj, sep, record_id = item.partition(":")
        if not sep or not obj or not record_id:
            raise typer.BadParameter(
                f"--record must be object:record_uuid (got {item!r})."
            )
        pairs.append((obj, record_id))
    return pairs


act_scheduled_app = typer.Typer(
    help="Read and schedule activity instances.", no_args_is_help=True
)
activities_app.add_typer(act_scheduled_app, name="scheduled")


@act_scheduled_app.command("list")
def act_scheduled_list(
    activity: str = typer.Option(
        "", "--activity", help="Filter by activity api_name or UUID."
    ),
    assigned_to_me: bool = typer.Option(
        False, "--mine", help="Only those assigned to me."
    ),
    completed: bool | None = typer.Option(
        None, "--completed/--pending", help="Filter by completion state."
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List scheduled activity instances."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError):
        items = act_tools.list_scheduled(
            activity=activity or None,
            assigned_to_me=True if assigned_to_me else None,
            completed=completed,
        )

    def table() -> None:
        t = Table(title="Scheduled activities")
        t.add_column("id")
        t.add_column("activity")
        t.add_column("due")
        t.add_column("completed_at")
        t.add_column("associated")
        for s in items:
            t.add_row(
                s.get("id") or "",
                str(s.get("activity_object") or ""),
                str(s.get("due_datetime") or ""),
                str(s.get("completed_at") or ""),
                _short(s.get("associated") or "", 40),
            )
        console.print(t)

    out.render(
        fmt,
        json_data=items,
        table=table,
        csv_rows=items,
        csv_columns=[
            out.Column("id", "id"),
            out.Column("activity_object", "activity_object"),
            out.Column("due_datetime", "due_datetime"),
            out.Column("completed_at", "completed_at"),
            out.Column("associated", "associated"),
        ],
    )


@act_scheduled_app.command("get")
def act_scheduled_get(
    scheduled_id: str = typer.Argument(..., help="Scheduled-activity UUID."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one scheduled activity instance."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        result = act_tools.get_scheduled(scheduled_id)

    def table() -> None:
        console.print(f"[bold]scheduled activity[/bold] [dim]{result.get('id')}[/dim]")
        console.print_json(
            json.dumps({k: v for k, v in result.items() if k != "env"}, default=str)
        )

    out.render(
        fmt,
        json_data=result,
        table=table,
        csv_rows=[result],
        csv_columns=[
            out.Column("id", "id"),
            out.Column("due_datetime", "due_datetime"),
            out.Column("completed_at", "completed_at"),
        ],
    )


@act_scheduled_app.command(
    "create",
    epilog="Wire body: see `kizen docs show activity`",
)
def act_scheduled_create(
    identifier: str = typer.Argument(..., help="Activity api_name or UUID."),
    due: str = typer.Option(
        ...,
        "--due",
        help="Due date and time, ISO with a UTC offset (2026-11-16T15:30:00-05:00).",
    ),
    assignee: str = typer.Option(
        "", "--assignee", help="Team member's email, name or UUID. Or use --role."
    ),
    role: str = typer.Option(
        "", "--role", help="Role name or UUID. Or use --assignee."
    ),
    record: list[str] = typer.Option(
        [],
        "--record",
        help="Associate a record: --record object_api_name:record_uuid (repeatable).",
    ),
    note: str = typer.Option("", "--note", help="Note text."),
    notification: list[str] = typer.Option(
        [],
        "--notification",
        help="Remind the assignee: type:amount:unit, e.g. email:1:day (repeatable).",
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
    """Schedule one activity for a team member or a role, on one or more records."""
    records = _parse_record_flags(record)
    _run_mutation(
        lambda: act_planners.plan_schedule_activity(
            identifier,
            due,
            records,
            assignee=assignee or None,
            role=role or None,
            note=note or None,
            notifications=notification,
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@act_scheduled_app.command(
    "update",
    epilog="Wire body and replace rules: see `kizen docs show activity`",
)
def act_scheduled_update(
    scheduled_id: str = typer.Argument(..., help="Scheduled-activity UUID."),
    due: str = typer.Option(
        "", "--due", help="New due date and time, ISO with a UTC offset."
    ),
    assignee: str = typer.Option(
        "", "--assignee", help="Reassign to a team member (email, name or UUID)."
    ),
    role: str = typer.Option("", "--role", help="Reassign to a role (name or UUID)."),
    note: str | None = typer.Option(None, "--note", help="Replace the note."),
    record: list[str] = typer.Option(
        [],
        "--record",
        help="Replace the records: --record object_api_name:record_uuid (repeatable).",
    ),
    notification: list[str] = typer.Option(
        [],
        "--notification",
        help="Replace the reminders: type:amount:unit, e.g. email:1:day (repeatable).",
    ),
    clear_notifications: bool = typer.Option(
        False, "--clear-notifications", help="Remove every reminder."
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
    """Change one scheduled activity. Only the options you pass are sent."""
    records = _parse_record_flags(record) if record else None
    _run_mutation(
        lambda: act_planners.plan_update_scheduled_activity(
            scheduled_id,
            due=due or None,
            assignee=assignee or None,
            role=role or None,
            note=note,
            records=records,
            notifications=notification or None,
            clear_notifications=clear_notifications,
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@act_scheduled_app.command("delete")
def act_scheduled_delete(
    scheduled_id: str = typer.Argument(..., help="Scheduled-activity UUID."),
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
    """Permanently delete one scheduled activity. It can't be restored."""
    _run_mutation(
        lambda: act_planners.plan_delete_scheduled_activity(scheduled_id),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )
