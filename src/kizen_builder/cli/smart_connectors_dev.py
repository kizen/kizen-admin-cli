"""`kizen smart-connectors` local dev loop — pull, run, add-input, push."""

from __future__ import annotations

import typer
from rich.markup import escape
from rich.prompt import Confirm
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._shared import JSON_OPTION, cli_errors, console, err_console
from kizen_builder.cli.smart_connectors import smart_connectors_app
from kizen_builder.tools import smart_connectors as sc_tools
from kizen_builder.tools.plans import PlanError


@smart_connectors_app.command("pull")
def smart_connectors_pull(
    connector: str = typer.Argument(..., help="Connector UUID or api_name."),
    dir_: str = typer.Option(
        None, "--dir", "-d", help="Target directory (default: ./<api_name>)."
    ),
    live: bool = typer.Option(
        False, "--live", help="Pull the live script instead of the latest draft."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite an existing non-empty directory."
    ),
    seed_limit: int = typer.Option(
        1000,
        "--seed-limit",
        help="Max rows to export per seeded object (0 for all). Seeded objects "
        "can be whole tables; the local copy exists to exercise the joins.",
    ),
    json_out: bool = JSON_OPTION,
) -> None:
    """Assemble a local working directory (connector.sql + __config.json + data/)
    for a connector so you can iterate on its SQL locally.

    Seeded objects (`seeds list`) are exported to data/ too, from the same saved
    filter group the live run uses, so `run` exercises the same joins.
    """
    with cli_errors(LookupError, FileExistsError):
        res = sc_tools.pull_connector(
            connector,
            dest=dir_,
            use_live=live,
            overwrite=force,
            seed_limit=seed_limit or None,
        )

    if json_out:
        out.emit_json(res)
        return
    console.print(
        f"[green]pulled[/green] {res['connector']} "
        f"({res['script_status']} script, {res['sql_lines']} lines) → {res['workdir']}"
    )
    if res["inputs_downloaded"]:
        console.print(f"  input files: {', '.join(res['inputs_downloaded'])}")
    for seed in res.get("seeds_exported") or []:
        console.print(
            f"  seed kizen.{seed['table']} → data/{seed['file']} "
            f"({seed['rows']} row(s) from '{seed['filter_group']}')"
        )
    for w in res["warnings"]:
        console.print(f"  [yellow]![/yellow] {w}")
    console.print(
        f"[dim]Next: edit {res['workdir']}/connector.sql, then "
        f"`smart-connectors run --dir {res['workdir']}`.[/dim]"
    )


@smart_connectors_app.command("run")
def smart_connectors_run(
    dir_: str = typer.Option(
        ".", "--dir", "-d", help="Connector working directory (from `pull`)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Build the databases but skip running the user SQL."
    ),
    json_out: bool = JSON_OPTION,
) -> None:
    """Run connector.sql locally against embedded ClickHouse and report the
    output tables written to data/output/. Needs the 'connectors' extra."""
    try:
        meta = sc_tools.run_connector(dir_, dry_run=dry_run)
    except sc_tools.ConnectorRuntimeMissing as e:
        err_console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1) from e
    except FileNotFoundError as e:
        err_console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:  # runner surfaces SQL errors as exceptions
        err_console.print(f"[red]run failed:[/red] {escape(str(e))}", emoji=False)
        raise typer.Exit(code=1) from e

    if json_out:
        out.emit_json(meta)
        return
    files = meta.get("output_files", [])
    stats = meta.get("stats", {})
    if meta.get("partial_output"):
        console.print(
            "[yellow]partial output[/yellow] — the SQL errored; wrote what it could:"
        )
    else:
        console.print(f"[green]ran[/green] in {meta.get('time_to_process', '?')}s")
    t = Table(title="Output tables")
    t.add_column("file")
    t.add_column("rows", justify="right")
    t.add_column("size", justify="right")
    per_scope = meta.get("stats_per_scope", {})
    for f in files:
        name = f.get("file_name", "")
        scope = name[:-4] if name.endswith(".csv") else name
        rows = (per_scope.get(scope) or {}).get("num_rows", "")
        t.add_row(f.get("file_path", name), str(rows), f"{f.get('size', 0)} b")
    console.print(t)
    console.print(
        f"[dim]{stats.get('num_output_tables', len(files))} table(s), "
        f"{stats.get('num_rows', '?')} total rows.[/dim]"
    )
    if meta.get("error"):
        err_console.print(
            f"[yellow]SQL error:[/yellow] {escape(str(meta['error']))}", emoji=False
        )


@smart_connectors_app.command("add-input")
def smart_connectors_add_input(
    input_file: str = typer.Argument(
        ..., help="Path to a CSV / Excel / ZIP input file."
    ),
    dir_: str = typer.Option(
        ".", "--dir", "-d", help="Connector working directory (from `pull`)."
    ),
) -> None:
    """Normalize a new input file into the working directory's data/ and point
    __config.json at it. Useful for testing with a file from a live execution.
    Needs the 'connectors' extra."""
    try:
        progress = sc_tools.add_input(dir_, input_file)
    except sc_tools.ConnectorRuntimeMissing as e:
        err_console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1) from e
    except FileNotFoundError as e:
        err_console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1) from e
    for line in (progress or "").splitlines():
        if "Headers modified" in line or "->" in line:
            console.print(f"[dim]{line.strip()}[/dim]")
    console.print(f"[green]added[/green] {input_file} → {dir_}/data/ (config updated)")


@smart_connectors_app.command("push")
def smart_connectors_push(
    dir_: str = typer.Option(
        ".", "--dir", "-d", help="Connector working directory (from `pull`)."
    ),
    connector: str = typer.Option(
        None, "--connector", help="Override connector (else read from the pull marker)."
    ),
    script_id: str = typer.Option(
        None, "--script", help="Override draft script id (else from the marker)."
    ),
    publish: bool = typer.Option(
        False,
        "--publish",
        help="After updating the draft, run its output sample and publish it "
        "live if the sample succeeds. Waits up to 300 s for the sample.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the SQL diff without writing anything."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON (diff with --dry-run, result otherwise)."
    ),
) -> None:
    """Write the local connector.sql back onto the connector's draft SQL script,
    and optionally publish it live. Previews the diff and confirms first.

    Publishing forks a new draft, and the pull marker in --dir is moved onto
    it, so the next `push` from the same directory works without a re-pull.
    """
    with cli_errors(LookupError, PlanError, FileNotFoundError):
        plan = sc_tools.plan_push(dir_, connector=connector, script_id=script_id)

    # Under --json, all human/diff text goes to stderr so stdout stays pure JSON.
    diff_console = err_console if json_out else console
    if plan.get("warning"):
        diff_console.print(f"[yellow]warning:[/yellow] {plan['warning']}")
    if not plan["changed"]:
        if json_out and dry_run:
            out.emit_json({"changed": False})
        else:
            console.print(
                f"[dim]No changes — local connector.sql matches the remote {plan['script_status'] or 'draft'}.[/dim]"
            )
        if not publish:
            return

    if plan["changed"]:
        diff_console.print(
            f"[bold]Diff vs remote {plan['script_status'] or 'draft'} {plan['script_id']}:[/bold]"
        )
        if plan["diff"]:
            diff_console.print(plan["diff"], markup=False, emoji=False, soft_wrap=True)
        else:
            diff_console.print("[dim](no textual diff)[/dim]")
    if publish:
        diff_console.print(
            "[yellow]--publish set:[/yellow] after the update the output sample "
            "runs, and the draft is promoted LIVE only if it succeeds."
        )

    if dry_run:
        if json_out:
            out.emit_json(
                {
                    "changed": plan["changed"],
                    "diff": plan["diff"],
                    "publish": publish,
                    "script_status": plan["script_status"],
                    "warning": plan.get("warning"),
                }
            )
        else:
            console.print("[dim]--dry-run: nothing written.[/dim]")
        return

    if not yes:
        action = "update the draft" + (" and publish it live" if publish else "")
        if not Confirm.ask(f"Apply — {action}?", default=False):
            console.print("[yellow]aborted.[/yellow]")
            raise typer.Exit(code=1)

    with cli_errors():
        try:
            result = sc_tools.apply_push(
                plan["connector"], plan["script_id"], plan["local_sql"], publish=publish
            )
        except PlanError as e:
            # Carries the server's sample error, which is remote text.
            err_console.print(f"[red]error:[/red] {escape(str(e))}", emoji=False)
            raise typer.Exit(code=1) from e

    if result.get("published"):
        new_draft = result["new_draft_id"]
        result["marker_updated"] = bool(new_draft) and sc_tools.advance_marker(
            dir_, from_script_id=plan["script_id"], to_script_id=new_draft
        )

    if json_out:
        out.emit_json(result)
        return
    console.print(
        f"[green]pushed[/green] → draft script {result['updated_script_id']} updated"
    )
    if not result.get("published"):
        return
    status = result.get("connector_status") or "unknown"
    console.print(
        "[green]script published[/green] — live runs now use it. Connector "
        f"status: {escape(status)}",
        emoji=False,
    )
    if result.get("warning"):
        err_console.print(
            f"[yellow]warning:[/yellow] {escape(result['warning'])}", emoji=False
        )
    elif status != "operational":
        console.print(
            "[dim]Live runs need `operational`: `smart-connectors activate`.[/dim]"
        )
    if result["marker_updated"]:
        console.print(
            f"[dim]{sc_tools.MARKER_NAME} now points at the new draft "
            f"{result['new_draft_id']}.[/dim]"
        )
    elif result["new_draft_id"]:
        console.print(
            f"[dim]The connector's draft is now {result['new_draft_id']} — push "
            f"to it with --script {result['new_draft_id']}.[/dim]"
        )
    else:
        console.print(
            f"[dim]No new draft found, so {sc_tools.MARKER_NAME} was not moved and "
            "still names the published script. Find the current draft with "
            f"`smart-connectors scripts {escape(plan['connector'])}`.[/dim]",
            emoji=False,
        )
