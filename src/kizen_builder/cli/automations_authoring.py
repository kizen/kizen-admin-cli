"""`kizen automations step-types | trigger-types | lint`: help writing a spec.

None of these touch an environment. `step-types` and `trigger-types` print
what one type's config block takes, with a block to start from; `lint` checks
a whole spec file.
"""

from __future__ import annotations

import json
import re
from typing import Any

import typer

from kizen_builder.cli._mutations import _read_spec
from kizen_builder.cli._shared import err_console
from kizen_builder.cli.automations import autos_app
from kizen_builder.tools import automation_lint, step_catalog

_DESCRIPTION_WIDTH = 150


def _first_sentence(text: str) -> str:
    """A field description cut to one line; `--json` carries it whole."""
    text = " ".join(text.split())
    end = re.search(r"(?<!e\.g)(?<!i\.e)\. ", text)
    if end and end.start() < _DESCRIPTION_WIDTH:
        return text[: end.start() + 1]
    if len(text) <= _DESCRIPTION_WIDTH:
        return text
    return text[: _DESCRIPTION_WIDTH - 1].rstrip() + "…"


def _unknown(e: step_catalog.UnknownType) -> typer.Exit:
    err_console.print(f"[red]error:[/red] {e}", markup=True, emoji=False)
    return typer.Exit(code=2)


def _print_type(info: dict[str, Any]) -> None:
    kind, name = info["kind"], info["type"]
    echo = typer.echo
    echo(f"{name}" + (f": {info['purpose']}" if info.get("purpose") else ""))
    echo(
        f"config block: {info['block']}"
        if info["block"]
        else "config block: none (the step carries no settings)"
    )

    for v in info["variants"]:
        echo(f"\n# {v['name']}: {v['summary']}")
        echo(json.dumps(v["spec"], indent=2))

    for s in info.get("selectors") or []:
        default = f"  (default: {s['default']})" if s.get("default") else ""
        echo(f"\n{s['path']}: pick one{default}")
        width = max(len(o["value"]) for o in s["options"])
        for o in s["options"]:
            needs = "with " + ", ".join(o["needs"]) if o["needs"] else "nothing else"
            note = f"  ({o['note']})" if o.get("note") else ""
            echo(f"  {o['value']:<{width}}  {needs}{note}")
        if s.get("note"):
            echo(f"  {s['note']}")

    if info["fields"]:
        echo("\nfields (* required):")
        for f in info["fields"]:
            star = "*" if f["required"] else " "
            line = f"  {star} {f['path']}  [{f['type']}]"
            if f.get("choices"):
                line += "  " + " | ".join(f["choices"])
            elif f.get("known_choices"):
                line += "  seen: " + " | ".join(f["known_choices"])
            echo(line)
            if f.get("description"):
                echo(f"      {_first_sentence(f['description'])}")

    if info.get("lookups"):
        echo("\nlook these up first:")
        for lk in info["lookups"]:
            note = f"  ({lk['note']})" if lk.get("note") else ""
            echo(f"  {lk['path']}: {lk['command']}{note}")

    for note in info.get("notes") or []:
        echo(f"\nnote: {note}")

    if info.get("concepts"):
        echo(
            "\nshared concepts: "
            + ", ".join(info["concepts"])
            + f"  (kizen automations {kind}-types concepts)"
        )
    echo(
        f"\nOne block alone: kizen automations {kind}-types show {name} "
        "--variant <name>\n"
        f"Full schema: --json.  Quirks and wire format: {info['doc']}"
    )


def _types_app(kind: str) -> typer.Typer:
    noun = f"{kind} type"
    types_app = typer.Typer(
        help=(
            f"What each automation {noun} takes: its config block, fields, "
            "choices, and a block to start from. No live env needed."
        ),
        no_args_is_help=True,
    )

    @types_app.command("list")
    def list_cmd(
        json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
    ) -> None:
        rows = step_catalog.list_types(kind)
        if json_out:
            typer.echo(json.dumps(rows, indent=2))
            return
        width = max(len(r["type"]) for r in rows)
        for r in rows:
            purpose = r.get("purpose") or (r["block"] or "")
            variants = (
                f"  [variants: {', '.join(r['variants'])}]" if r["variants"] else ""
            )
            typer.echo(f"{r['type']:<{width}}  {purpose}{variants}")
        typer.echo(f"\nOne type in full: kizen automations {kind}-types show <type>")

    list_cmd.__doc__ = f"List every {noun} this CLI can write."

    @types_app.command("show")
    def show_cmd(
        type_name: str = typer.Argument(
            ..., metavar="TYPE", help=f"A {noun}, from `{kind}-types list`."
        ),
        variant: str = typer.Option(
            "",
            "--variant",
            help=(
                f"Print only this variant's {kind} as JSON, ready for a spec "
                "file" + (" or `steps add`." if kind == "step" else ".")
            ),
        ),
        json_out: bool = typer.Option(
            False, "--json", help="Emit everything as JSON, with the JSON Schema."
        ),
    ) -> None:
        try:
            if variant:
                typer.echo(
                    json.dumps(
                        step_catalog.variant_spec(type_name, variant, kind), indent=2
                    )
                )
                return
            info = step_catalog.describe(type_name, kind)
        except step_catalog.UnknownType as e:
            raise _unknown(e) from e
        if json_out:
            typer.echo(json.dumps(info, indent=2))
            return
        _print_type(info)

    show_cmd.__doc__ = (
        f"Show one {noun}: blocks to start from, the keys each choice needs, "
        "every field, and the values to look up first."
    )

    if kind == "step":

        @types_app.command("concepts")
        def concepts_cmd(
            json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
        ) -> None:
            """The ideas most step types share: who, when, variables, fields,
            value sources, filters, merge fields."""
            if json_out:
                typer.echo(json.dumps(step_catalog.CONCEPTS, indent=2))
                return
            for name, text in step_catalog.CONCEPTS.items():
                typer.echo(f"{name}\n  {text}\n")

    return types_app


autos_app.add_typer(_types_app("step"), name="step-types")
autos_app.add_typer(_types_app("trigger"), name="trigger-types")


def _finding_line(level: str, f: dict[str, Any]) -> str:
    where = []
    for kind in ("step", "trigger"):
        if f.get(kind) is not None:
            where.append(f"{kind} '{f[kind]}'")
        elif kind in f:
            where.append(f"{kind} #{f.get('index')}")
    if f.get("path"):
        where.append(f["path"])
    line = (
        f"{level}: {' '.join(where)}: {f['message']}"
        if where
        else f"{level}: {f['message']}"
    )
    if f.get("suggestion"):
        line += f" (did you mean '{f['suggestion']}'?)"
    return line


@autos_app.command(
    "lint",
    epilog="Spec shape (an AutomationDef: triggers + step graph): see `kizen docs show automation`",
)
def autos_lint(
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Path to a JSON file containing an AutomationDef. Default: read from stdin.",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit {ok, errors, warnings}; each finding names its step and path.",
    ),
) -> None:
    """Check an automation spec without an environment.

    Reports every problem it finds in one pass: config block shape and
    choices, the step graph, variables no step declares, and keys a chosen
    option is normally written with. Exits 1 on any error; warnings alone exit
    0. What needs the environment (fields, activity types, team members) is
    checked by `create --dry-run` or `update --dry-run`.
    """
    spec, _ = _read_spec(spec_file)
    result = automation_lint.lint_spec(spec)
    if json_out:
        typer.echo(json.dumps(result, indent=2, default=str))
    else:
        hints: list[str] = []
        for level, key in (("error", "errors"), ("warning", "warnings")):
            for f in result[key]:
                typer.echo(_finding_line(level, f))
                if f.get("hint") and f["hint"] not in hints:
                    hints.append(f["hint"])
        for hint in hints:
            typer.echo(f"  see: {hint}")
        n_err, n_warn = len(result["errors"]), len(result["warnings"])
        if result["ok"]:
            typer.echo(
                f"ok: no errors, {n_warn} warning(s). Next: kizen automations "
                "create --spec-file <file> --dry-run"
            )
        else:
            typer.echo(f"{n_err} error(s), {n_warn} warning(s)")
    if not result["ok"]:
        raise typer.Exit(code=1)
