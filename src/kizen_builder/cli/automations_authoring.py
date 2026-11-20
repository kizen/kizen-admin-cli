"""`kizen automations step-types`: what each step's and trigger's config
block takes, and a whole automation to start from.

Nothing here touches an environment. It prints the catalog in
`tools/step_catalog.py`.
"""

from __future__ import annotations

import json
import re
from typing import Any

import typer
from rich.markup import escape
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._shared import JSON_OPTION, OUTPUT_OPTION, console, err_console
from kizen_builder.cli.automations import autos_app
from kizen_builder.tools import step_catalog

_DESCRIPTION_WIDTH = 150
_EPILOG = "Spec shape (an AutomationDef: triggers + step graph): see `kizen docs show automation`"

types_app = typer.Typer(
    help=(
        "What each automation step and trigger type takes: its config block, "
        "fields, choices, and an entry to start from. No live env needed."
    ),
    no_args_is_help=True,
)
autos_app.add_typer(types_app, name="step-types")


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
    err_console.print(
        f"[red]error:[/red] {escape(str(e))}", emoji=False, soft_wrap=True
    )
    return typer.Exit(code=2)


def _print_type(info: dict[str, Any]) -> None:
    kind, name = info["kind"], info["type"]
    echo = typer.echo
    echo(f"{name} ({kind}): {info['purpose']}")
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
            if f.get("same_as"):
                line += f"  same keys as {f['same_as']}"
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
            + "  (kizen automations step-types concepts)"
        )
    echo(
        f"\nOne {kind} alone: kizen automations step-types show {name} "
        "--variant <name>\n"
        f"Full schema: --json.  Quirks and wire format: {info['doc']}"
    )


@types_app.command("list", epilog=_EPILOG)
def types_list(output: str = OUTPUT_OPTION, json_out: bool = JSON_OPTION) -> None:
    """List every step and trigger type this CLI can write."""
    fmt = out.resolve_format(output, json_out)
    rows = step_catalog.list_types()

    def table() -> None:
        t = Table(title="Step and trigger types")
        for column in ("type", "kind", "purpose", "variants"):
            t.add_column(column)
        for r in rows:
            t.add_row(
                r["type"],
                r["kind"],
                r["purpose"],
                ", ".join(r["variants"]),
            )
        console.print(t)
        console.print("One type in full: kizen automations step-types show <type>")

    out.render(
        fmt,
        json_data=rows,
        table=table,
        csv_rows=rows,
        csv_columns=[
            out.Column("type", "type"),
            out.Column("kind", "kind"),
            out.Column("purpose", "purpose"),
            out.Column("variants", lambda r: ", ".join(r["variants"])),
        ],
    )


@types_app.command("show", epilog=_EPILOG)
def types_show(
    type_name: str = typer.Argument(
        ..., metavar="TYPE", help="A step or trigger type, from `step-types list`."
    ),
    variant: str = typer.Option(
        "",
        "--variant",
        help="Print only this variant's entry as JSON, ready for a spec file "
        "(or `steps add`, for a step).",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit everything as JSON, with the JSON Schema."
    ),
) -> None:
    """Show one step or trigger type: entries to start from, the keys each
    choice needs, every field, and the values to look up first. A common
    name such as `wait` or `ai_step` shows the type it stands for."""
    try:
        name = step_catalog.resolve(type_name)
        info = (
            step_catalog.variant_spec(name, variant)
            if variant
            else step_catalog.describe(name)
        )
    except step_catalog.UnknownType as e:
        raise _unknown(e) from e
    if name != type_name:
        note = f"'{type_name}' isn't a type name; showing {name}."
        typer.echo(note, err=bool(variant or json_out))
    if variant or json_out:
        typer.echo(json.dumps(info, indent=2))
        return
    _print_type(info)


@types_app.command("concepts", epilog=_EPILOG)
def types_concepts(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """The ideas most step types share: who, when, variables, fields, value
    sources, filters, merge fields."""
    if json_out:
        typer.echo(json.dumps(step_catalog.CONCEPTS, indent=2))
        return
    for name, text in step_catalog.CONCEPTS.items():
        typer.echo(f"{name}\n  {text}\n")


@types_app.command("skeleton", epilog=_EPILOG)
def types_skeleton(
    obj: str = typer.Option(
        "", "--object", help="The automation's target_object api_name."
    ),
) -> None:
    """Print a minimal automation spec as JSON: name, api_name, type,
    target_object, a manual trigger and one step. Add steps from `show`."""
    typer.echo(json.dumps(step_catalog.skeleton(obj or None), indent=2))
