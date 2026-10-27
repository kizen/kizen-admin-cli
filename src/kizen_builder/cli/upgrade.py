"""`kizen upgrade` — keep the installed CLI current, whatever shape it was
installed in.
"""

from __future__ import annotations

import typer
from rich.prompt import Confirm
from rich.table import Table

from kizen_builder import upgrade as upgrade_mod
from kizen_builder.cli._shared import app, console, err_console
from kizen_builder.config import helper_mode, refuse_in_helper_mode


def _print_check(result: upgrade_mod.CheckResult, install: upgrade_mod.Install) -> None:
    """Render a check result. Loud only when there's something to act on."""
    if result.out_of_date:
        console.print(
            f"[yellow]update available[/yellow] — {result.summary()}.\n"
            "Run [bold]kizen upgrade[/bold] to install it."
        )
        return
    if result.conclusive:
        console.print(f"[dim]kizen-builder {result.current} — up to date.[/dim]")
        return
    console.print(
        f"[dim]kizen-builder {result.current} — {result.summary()} "
        f"({install.detail}).[/dim]"
    )


@app.command()
def upgrade(
    check: bool = typer.Option(
        False,
        "--check",
        help="Only report whether a newer version exists; change nothing.",
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Ignore the cached check result and ask the remote again.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show the commands that would run, without running them.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
) -> None:
    """Update this CLI in place.

    Works out how the tool was installed — editable git checkout, `uv tool`,
    `pipx`, or a direct VCS install — and runs the right commands for that
    shape. For a checkout that means pulling *and* re-syncing dependencies, so
    a new upstream dependency doesn't surface later as a bare ImportError.

    `--check` is the session-start form: it reports whether a newer version
    exists, caches the answer for a day, and always exits 0 — offline, behind a
    proxy, or with no remote configured, it just says so quietly. Under
    app-builder it says the installation is managed there, and still exits 0.
    """
    if check and helper_mode():
        # app-builder owns the install, so there is nothing to look up. Keep
        # --check's exit-0 contract rather than refusing.
        console.print("[dim]this installation is managed by app-builder.[/dim]")
        return
    refuse_in_helper_mode("upgrade", "this installation")
    install = upgrade_mod.detect_install()

    if check:
        # Deliberately unconditionally successful: a version check that can
        # fail a command is worse than no version check.
        result = upgrade_mod.check_latest(install, refresh=refresh)
        _print_check(result, install)
        return

    try:
        steps = upgrade_mod.upgrade_steps(install)
    except upgrade_mod.UpgradeUnsupported as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        if exc.advice:
            err_console.print(f"[dim]{exc.advice}[/dim]")
        raise typer.Exit(code=1) from exc

    console.print(f"[dim]{install.detail}[/dim]")
    # Steps almost always share one directory; naming it once keeps long paths
    # out of every row, where they'd wrap the command past legibility.
    directories = {step.cwd for step in steps if step.cwd}
    shared = directories.pop() if len(directories) == 1 else None
    if shared is not None:
        console.print(f"[dim]running in {shared}[/dim]")
    table = Table(title="upgrade plan")
    table.add_column("command")
    table.add_column("why")
    for step in steps:
        where = f"(in {step.cwd}) " if step.cwd and shared is None else ""
        table.add_row(f"{where}{step.display()}", step.why)
    console.print(table)

    if dry_run:
        console.print("[dim]--dry-run: nothing was run.[/dim]")
        return

    if not yes:
        try:
            confirmed = Confirm.ask("Run these?", default=True)
        except EOFError:
            err_console.print(
                "[red]error:[/red] nothing on stdin to confirm with. "
                "Re-run with --yes, or --dry-run to just see the plan."
            )
            raise typer.Exit(code=2) from None
        if not confirmed:
            console.print("[dim]aborted; nothing was run.[/dim]")
            raise typer.Exit(code=1)

    ok, message = upgrade_mod.run_steps(steps)
    if not ok:
        err_console.print(f"[red]upgrade failed:[/red] {message}")
        raise typer.Exit(code=1)

    console.print(
        "\n[green]upgraded[/green] — the next `kizen` command runs the new version.\n"
        "[dim]Run `kizen --version` to confirm.[/dim]"
    )
