"""`kizen smart-connectors` — data-ingestion / ETL SQL connectors: the app
definition, the helpers the other `smart_connectors_*` modules share, and
the build/configure commands.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any, NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.prompt import Confirm
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.api.client import KizenAPIError
from kizen_builder.cli._mutations import _read_spec
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    app,
    cli_errors,
    console,
    err_console,
)
from kizen_builder.config import ConfigError
from kizen_builder.tools import smart_connectors as sc_tools
from kizen_builder.tools.plans import PlanError

smart_connectors_app = typer.Typer(
    help=(
        "Build, inspect, and iterate on smart connectors. "
        "Read verbs (list/get/executions/scripts/events/metadata) are safe. "
        "The local SQL loop is pull → run → push: `pull` builds a working "
        "directory, `run` executes connector.sql against embedded ClickHouse "
        "(needs the 'connectors' extra), `push` writes the SQL back to the "
        "draft and can publish it live. Building one from scratch runs "
        "create → set-input → (SQL loop) → generate-sample → push --publish → "
        "configure-flow → activate → start-flow."
    ),
    no_args_is_help=True,
)
app.add_typer(smart_connectors_app, name="smart-connectors")


def _preview_and_confirm(
    plan: dict[str, Any],
    *,
    render: Callable[[Console], None],
    action: str,
    dry_run: bool,
    yes: bool,
    json_out: bool,
    stdin_consumed: bool = False,
) -> bool:
    """Preview one smart-connector write and decide whether to go ahead.

    The group's writes are stateful sequences (upload → attach → regenerate;
    PATCH loads, re-read the ids the server assigned, PATCH again) rather than
    independent `Plan` operations, so they carry their own preview/confirm step
    with `_run_mutation`'s contract: `--dry-run` stops after the preview, `--json`
    keeps stdout machine-readable by sending the preview to stderr, and a spec
    read from stdin can't be confirmed interactively.

    Returns True when the caller should apply.
    """
    target = err_console if json_out else console
    render(target)
    if dry_run:
        if json_out:
            out.emit_json(plan)
        else:
            target.print("[dim]--dry-run: nothing written.[/dim]")
        return False
    if not yes:
        if stdin_consumed:
            err_console.print(
                "[red]error:[/red] cannot prompt for confirmation after reading "
                "the spec from stdin. Preview with --dry-run first, then re-run "
                "with --yes (or use --spec-file)."
            )
            raise typer.Exit(code=2)
        if not Confirm.ask(f"Apply — {action}?", default=False):
            console.print("[yellow]aborted.[/yellow]")
            raise typer.Exit(code=1)
    return True


def _connector_plan_error(exc: Exception) -> NoReturn:
    """Print a planning failure the way the rest of the CLI does, then exit 1."""
    if isinstance(exc, ValidationError):
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ())) or "spec"
            err_console.print(
                f"[red]spec error:[/red] {loc}: {err.get('msg', 'invalid value')}"
            )
    else:
        err_console.print(f"[red]error:[/red] {exc}")
    raise typer.Exit(code=1) from exc


_CONNECTOR_EXPECTED: tuple[type[Exception], ...] = (
    PlanError,
    ConfigError,
    KizenAPIError,
)


@contextlib.contextmanager
def _connector_errors(*also: type[Exception]) -> Iterator[None]:
    """`cli_errors`, but routed through `_connector_plan_error`.

    Same contract as `cli_errors`, and identical output for everything except a
    pydantic `ValidationError` — which gets one `spec error:` line per bad field
    instead of a raw dump. `PlanError` is always expected here because every
    write in this group plans before it applies.
    """
    try:
        yield
    except _CONNECTOR_EXPECTED + also as e:
        _connector_plan_error(e)


@smart_connectors_app.command("create")
def smart_connectors_create(
    name: str = typer.Argument(..., help="Display name for the new connector."),
    obj: str = typer.Option(
        ...,
        "--object",
        "-o",
        help="Custom object api_name (or UUID) the connector writes to.",
    ),
    connector_type: str = typer.Option(
        "spreadsheet",
        "--type",
        help="spreadsheet|webhook|schedule|activity|bulkaction|"
        "polling_third_party_api|direct_api_connection.",
    ),
    description: str = typer.Option("", "--description", help="Connector description."),
    cadence: int = typer.Option(
        None,
        "--cadence",
        help="Seconds between runs. Required for --type schedule; also the "
        "batching window for webhook. See `smart-connectors metadata`.",
    ),
    activity_object: str = typer.Option(
        None,
        "--activity-object",
        help="For --type activity: the activity TYPE (name or UUID) to listen "
        "to — see `kizen activities list`, not a custom object.",
    ),
    sql_version: str = typer.Option(
        None,
        "--sql-version",
        help="SQL engine version. Webhook connectors need 4.1.x; lower versions "
        "fail sample generation.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without creating."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, result otherwise)."
    ),
) -> None:
    """Create a smart connector.

    It lands in `status: "setup"` with an empty draft script. Attach a reference
    file next (`set-input`) — that's what generates the SQL template.
    """
    with _connector_errors():
        plan = sc_tools.plan_create_connector(
            name=name,
            custom_object=obj,
            connector_type=connector_type,
            description=description or None,
            cadence=cadence,
            activity_object=activity_object,
            sql_version=sql_version,
        )

    def render(target: Console) -> None:
        t = Table(title="Create smart connector", show_header=False)
        t.add_column("field", style="bold")
        t.add_column("value")
        for key, value in plan["preview"].items():
            if value is not None:
                t.add_row(key, str(value))
        target.print(t)

    if not _preview_and_confirm(
        plan,
        render=render,
        action=f"create connector '{name}'",
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    ):
        return

    with cli_errors():
        result = sc_tools.apply_create_connector(plan["payload"])

    if json_out:
        out.emit_json(result)
        return
    console.print(
        f"[green]created[/green] {result['api_name']} "
        f"({result['connector_type']}, status {result['status']})"
    )
    console.print(f"[dim]Next: {plan['next_step']}[/dim]")


@smart_connectors_app.command("set-input")
def smart_connectors_set_input(
    input_file: str = typer.Argument(
        ..., help="Local file to attach as the reference/sample file."
    ),
    connector: str = typer.Option(
        ..., "--connector", "-c", help="Connector UUID or api_name."
    ),
    regenerate: bool = typer.Option(
        True,
        "--regenerate/--no-regenerate",
        help="Generate the SQL template + config from the file's columns and "
        "write them onto the draft script (default), or just attach the file.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing reference file. Refused by default — swapping "
        "one is a known-broken operation in Kizen (see the error text).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without uploading."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, result otherwise)."
    ),
) -> None:
    """Upload a file as the connector's reference input and generate its template.

    This is the remote counterpart to `add-input` (which only touches a local
    working directory): the file is uploaded to Kizen, attached to the connector,
    and — unless --no-regenerate — the server generates the SQL script and
    config from its real columns onto the draft.

    Each connector type wants a differently shaped file; the required shape is
    validated server-side and named in the plan.
    """
    with _connector_errors(FileNotFoundError):
        plan = sc_tools.plan_set_input(
            connector, input_file, regenerate=regenerate, allow_replace=force
        )

    def render(target: Console) -> None:
        t = Table(
            title=f"Attach reference file — {plan['connector_api_name']}",
            show_header=False,
        )
        t.add_column("field", style="bold")
        t.add_column("value")
        t.add_row("file", f"{plan['file']} ({plan['file_size']} b)")
        t.add_row("connector_type", str(plan["connector_type"]))
        if plan.get("expected_shape"):
            t.add_row("expected shape", plan["expected_shape"])
        if plan.get("replacing"):
            t.add_row("replacing", f"[yellow]{plan['replacing']}[/yellow]")
        t.add_row("regenerate template", "yes" if plan["regenerate"] else "no")
        target.print(t)

    if not _preview_and_confirm(
        plan,
        render=render,
        action="upload and attach the file",
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    ):
        return

    with cli_errors(PlanError):
        result = sc_tools.apply_set_input(plan)

    if json_out:
        out.emit_json(result)
        return
    console.print(
        f"[green]attached[/green] {result['file_name']} → {result['connector']}"
    )
    if result["regenerated"]:
        console.print(
            f"  draft script {result['script_id']}"
            f"{' (new)' if result.get('new_draft') else ''} regenerated "
            f"({result['sql_lines']} lines, SQL {result.get('sql_version')}; "
            f"input tables: "
            f"{', '.join(t for t in result['input_tables'] if t) or 'none'})"
        )
        if result.get("sql_version_restored"):
            console.print(
                f"  [dim]template generation downgraded the SQL version; "
                f"restored to {result['sql_version_restored']}[/dim]"
            )
        if result.get("dropped_output_tables"):
            console.print(
                "  [dim]dropped generated output table(s) "
                f"{', '.join(result['dropped_output_tables'])} — no such Kizen "
                "object, and sample generation crashes on them[/dim]"
            )
        console.print(
            f"[dim]Next: `smart-connectors pull {result['connector']}` to iterate "
            f"on the SQL, or `smart-connectors generate-sample {result['connector']}` "
            f"to produce the output sample publish requires.[/dim]"
        )


def _output_tables(outputs: list[dict[str, Any]]) -> str:
    """`contacts (4 rows, 12 cols), policies (3 rows, 9 cols)`."""
    return (
        ", ".join(
            f"{o['table']} ({o['rows']} rows, {o['columns']} cols)" for o in outputs
        )
        or "none"
    )


@smart_connectors_app.command("generate-sample")
def smart_connectors_generate_sample(
    connector: str = typer.Argument(..., help="Connector UUID or api_name."),
    script_id: str = typer.Option(
        None, "--script", help="Script id (default: the latest draft)."
    ),
    wait: bool = typer.Option(
        True, "--wait/--no-wait", help="Poll until generation finishes."
    ),
    timeout: float = typer.Option(
        300.0, "--timeout", help="Seconds to wait before giving up."
    ),
    json_out: bool = JSON_OPTION,
) -> None:
    """Run the draft server-side to generate its output sample.

    Writes no records. `push --publish` 400s with "Output sample file is not
    generated yet" until the sample exists. The tables it reports are read from
    that sample. They can differ from the connector's recognized scopes, which
    `configure-flow` validates against and which refresh on `push --publish`.
    """
    with _connector_errors():
        result = sc_tools.generate_output_sample(
            connector, script_id=script_id, wait=wait, timeout=timeout
        )

    if json_out:
        out.emit_json(result)
        return
    state = result["state"]
    colour = {"success": "green", "failed": "red"}.get(state or "", "yellow")
    console.print(
        f"sample generation: [{colour}]{state}[/{colour}] (script {result['script_id']})"
    )
    outputs = result["outputs"]
    if outputs is not None:
        console.print(f"  output tables: {_output_tables(outputs)}")
        tables = {o["table"] for o in outputs}
        scopes = set(result["scopes"])
        if tables != scopes:
            console.print(
                f"  [yellow]the sample wrote {', '.join(sorted(tables)) or 'nothing'}, "
                f"but the connector's recognized scopes are "
                f"{', '.join(sorted(scopes)) or 'none'}.[/yellow] `configure-flow` "
                "validates against the recognized scopes, which refresh on "
                "`push --publish`."
            )
    for w in result["warnings"]:
        console.print(f"  [yellow]![/yellow] {w}")
    if result.get("error"):
        err_console.print(f"  [red]{escape(str(result['error']))}[/red]", emoji=False)
    if result["timed_out"]:
        console.print(
            "[yellow]still running[/yellow] — re-check with `smart-connectors scripts`."
        )
    if state != "success":
        raise typer.Exit(code=1)


@smart_connectors_app.command("suggest-variables")
def smart_connectors_suggest_variables(
    connector: str = typer.Argument(..., help="Connector UUID or api_name."),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
    spec: bool = typer.Option(
        False,
        "--spec",
        help="Emit just the configure-flow spec block (JSON) to stdout.",
    ),
) -> None:
    """Ask Kizen to infer execution variables from the reference file's columns.

    Saves nothing — it's the starting point for a configure-flow spec, with the
    data types and input/output formats already inferred (`"Yes"/"No"` text
    becomes a boolean with input_format yes_no, and so on).
    """
    with cli_errors():
        result = sc_tools.suggest_execution_variables(connector)

    if spec:
        out.emit_json(result["spec"])
        return

    fmt = out.resolve_format(output, json_out)
    rows = result["raw"]

    def table() -> None:
        t = Table(title=f"Suggested execution variables — {connector}")
        t.add_column("name")
        t.add_column("data_source")
        t.add_column("data_type")
        t.add_column("scope")
        t.add_column("input_format")
        t.add_column("array")
        for r in rows:
            t.add_row(
                str(r.get("name") or "—"),
                str(r.get("data_source") or "—"),
                str(r.get("data_type") or "—"),
                str(r.get("scope") or "—"),
                str(r.get("input_format") or ""),
                "yes" if r.get("is_array") else "",
            )
        console.print(t)
        if not rows:
            console.print(
                "[dim]No suggestions — the connector needs a reference file "
                "(`set-input`) and a generated sample (`generate-sample`).[/dim]"
            )
        else:
            console.print(
                "[dim]Tip: `--spec` emits these as a configure-flow spec block.[/dim]"
            )

    out.render(
        fmt,
        json_data=result,
        table=table,
        csv_rows=rows,
        csv_columns=[
            out.Column(k, k)
            for k in (
                "name",
                "data_source",
                "data_type",
                "scope",
                "input_format",
                "output_format",
                "is_array",
            )
        ],
    )


@smart_connectors_app.command(
    "configure-flow",
    epilog="Spec shape (execution variables + load steps): see `kizen docs show smart-connector-flow`",
)
def smart_connectors_configure_flow(
    connector: str = typer.Argument(
        None, help="Connector UUID or api_name (else the spec's 'connector')."
    ),
    spec_file: str = typer.Option(
        "", "--spec-file", help="Path to the JSON flow spec. Omit to read stdin."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without writing."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (plan with --dry-run, result otherwise)."
    ),
) -> None:
    """Save a connector's execution variables and load steps from a spec file.

    This is what turns transformed SQL output into records: each load step names
    an object, how to match existing records, and which variable feeds which
    field. A step can expose the id of the record it wrote so a later step can
    point a relationship field at it.

    The spec refers to everything by name (object api_names, field api_names,
    variable names) and is resolved against live state before anything is
    written. Note that saving execution variables replaces the connector's
    existing set — the plan lists any that would be dropped.
    """
    spec, from_stdin = _read_spec(spec_file, "smart-connector flow")
    with _connector_errors(ValidationError):
        plan = sc_tools.plan_configure_flow(spec, connector=connector)

    def render(target: Console) -> None:
        target.print(
            f"[bold]Configure flow — {plan['connector_api_name']}[/bold] "
            f"(output tables: "
            + ", ".join(f"{k} ({v} cols)" for k, v in plan["scopes"].items())
            + ")"
        )
        if plan["execution_variables"]:
            vt = Table(
                title=f"Execution variables ({len(plan['execution_variables'])})"
            )
            vt.add_column("name")
            vt.add_column("reads")
            vt.add_column("type")
            vt.add_column("scope")
            for var in plan["execution_variables"]:
                vt.add_row(
                    str(var["name"]),
                    str(var.get("data_source") or f"= {var.get('value')}"),
                    str(var["data_type"]),
                    str(var["scope"]),
                )
            target.print(vt)
        if plan["dropped_variables"]:
            target.print(
                "[yellow]dropped:[/yellow] execution variable(s) "
                f"{', '.join(plan['dropped_variables'])} are live but not in the "
                "spec, and saving replaces the set"
            )
        for msg in plan.get("date_format_warnings") or []:
            target.print(f"[yellow]warning:[/yellow] {msg}")
        for load in plan["loads"]:
            lt = Table(
                title=f"Load {load['order']}: {load['object_label']} "
                f"(from '{load['scope']}')"
            )
            lt.add_column("kind")
            lt.add_column("field")
            lt.add_column("variable(s)")
            lt.add_column("on match")
            for rule in load["matching_rules"]:
                lt.add_row(
                    "match",
                    str(rule["field_label"] or "(kizen id)"),
                    rule["variable_ref"],
                    f"{rule['single_match_action']} / no match: {rule['no_match_action']}",
                )
            for rule in load["field_mapping_rules"]:
                lt.add_row(
                    "write",
                    str(rule["field_label"]),
                    ", ".join(rule["variable_refs"]),
                    str(rule.get("conflict_resolution") or ""),
                )
            if load["exposes_variable"]:
                lt.add_row("exposes", "(record id)", load["exposes_variable"], "")
            target.print(lt)
        if plan["existing_loads"]:
            target.print(
                f"[yellow]replacing[/yellow] {plan['existing_loads']} existing load step(s)"
            )
        if plan["deferred_loads"]:
            target.print(
                "[dim]Saved in rounds — "
                f"{', '.join(plan['deferred_loads'])} reference a record id an "
                "earlier step creates, which only gets a uuid once that step is saved.[/dim]"
            )

    if not _preview_and_confirm(
        plan,
        render=render,
        action=f"save {len(plan['loads'])} load step(s) and "
        f"{len(plan['execution_variables'])} execution variable(s)",
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    ):
        return

    with cli_errors(PlanError):
        result = sc_tools.apply_configure_flow(plan)

    if json_out:
        out.emit_json(result)
        return
    console.print(
        f"[green]configured[/green] {result['connector']} — "
        f"{result['loads_saved']} load step(s), "
        f"{result['variables_saved']} variable(s), in {result['rounds']} round(s)"
    )
    for name, uuid_ in (result["exposed_variables"] or {}).items():
        console.print(f"  exposes [bold]{name}[/bold] → {uuid_}")
    console.print(
        "[dim]Next: `smart-connectors activate` (a live run silently queues "
        "forever without it), then `start-flow --dry-run`.[/dim]"
    )
