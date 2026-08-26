"""`kizen messages` — email templates plus the automation-scoped messages that
`notify_member_via_email` steps reference. A separate surface, not nested
under `automations`, since templates aren't automation-specific.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.table import Table

from kizen_builder import output as out
from kizen_builder.cli._mutations import _read_spec, _run_mutation
from kizen_builder.cli._shared import (
    JSON_OPTION,
    OUTPUT_OPTION,
    app,
    cli_errors,
    console,
    err_console,
)
from kizen_builder.models.spec.email_templates import EmailTemplateDef
from kizen_builder.tools import email_craft
from kizen_builder.tools import messages as message_tools
from kizen_builder.tools.planners import messages as message_planners

messages_app = typer.Typer(
    help=(
        "Email templates and automation-scoped messages — the content "
        "notify_member_via_email steps reference (see `kizen docs show email-templates`). "
        "Kizen's builder UI 'select email' picker only recognizes a message "
        "as selected when it's seeded from a real template; that's what "
        "`create` does."
    ),
    no_args_is_help=True,
)
app.add_typer(messages_app, name="messages")

messages_templates_app = typer.Typer(help="Email templates.", no_args_is_help=True)
messages_app.add_typer(messages_templates_app, name="templates")


@messages_templates_app.command("list")
def messages_templates_list(
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """List email templates (pass one's name or UUID as `--template`)."""
    fmt = out.resolve_format(output, json_out)
    with cli_errors():
        templates = message_tools.list_templates()

    def table() -> None:
        t = Table(title="Email templates")
        t.add_column("id", style="dim")
        t.add_column("name")
        t.add_column("subject")
        for tmpl in templates:
            t.add_row(
                tmpl.get("id") or "", tmpl.get("name") or "", tmpl.get("subject") or ""
            )
        console.print(t)

    out.render(fmt, json_data=templates, table=table)


@messages_templates_app.command("get")
def messages_templates_get(
    template: str = typer.Argument(..., help="Email template name or UUID."),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Emit the full raw API payload (`craft_json` and `content` "
        "included) — the template source for building a new one. Implies JSON.",
    ),
    output: str = OUTPUT_OPTION,
    json_out: bool = JSON_OPTION,
) -> None:
    """Show one email template, including how well its two content fields agree.

    A template stores the editable `craft_json` tree and the compiled
    `content` HTML that actually gets sent as independent fields, and the
    server derives neither from the other. Two checks are reported, because
    they catch different drift: `structure coupled` matches every
    `Section`/`Row` node against its `section-<nodeId>` class in the HTML,
    and `text in sync` checks each `Text` node's copy actually appears
    there. Either one reading `no` means the builder view and what
    recipients receive have diverged.
    """
    fmt = out.resolve_format(output, json_out)
    with cli_errors(LookupError, ValueError):
        detail = message_tools.get_template_detail(template)

    if raw:
        out.emit_json(detail)
        return

    summary = message_tools.craft_summary(detail)

    def table() -> None:
        console.print(
            f"[bold]{detail.get('name')}[/bold]  "
            f"[dim](id={detail.get('id')}, type={detail.get('type')})[/dim]"
        )
        console.print(f"subject: {detail.get('subject') or ''}")

        t = Table(title="Content fields")
        t.add_column("field")
        t.add_column("value")
        t.add_row("craft_json nodes", str(summary["node_count"]))
        t.add_row(
            "node types",
            ", ".join(f"{k}×{v}" for k, v in summary["node_types"].items()) or "—",
        )
        t.add_row("content bytes", str(summary["content_bytes"]))
        t.add_row("section- classes", str(summary["section_classes"]))
        for label, key in (
            ("structure coupled", "structure_coupled"),
            ("text in sync", "text_in_sync"),
        ):
            t.add_row(label, "[green]yes[/green]" if summary[key] else "[red]no[/red]")
        for label, key in (
            ("classes with no node", "classes_without_node"),
            ("Section/Row with no class", "containers_without_class"),
            ("Text nodes not in content", "text_nodes_missing_from_content"),
        ):
            if summary[key]:
                t.add_row(f"[red]{label}[/red]", ", ".join(summary[key]))
        console.print(t)

    out.render(fmt, json_data={**summary, "id": detail.get("id")}, table=table)


def _validate_email_spec(spec_dict: dict) -> EmailTemplateDef:
    try:
        return EmailTemplateDef.model_validate(spec_dict)
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(p) for p in err.get("loc", ())) or "spec"
            msg = err.get("msg", "invalid value")
            err_console.print(f"[red]spec error:[/red] {loc}: {msg}")
        raise typer.Exit(code=1) from e


@messages_templates_app.command(
    "create",
    epilog="Spec shape: see `kizen docs show email-templates`. "
    "Generate one with `messages templates craft-config`.",
)
def messages_templates_create(
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Path to a JSON email-template spec. Default: read from stdin.",
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
    """Create an email template from a spec file — `craft_json` and `content`
    built together from one pass over the spec's `sections`, so the two can
    never be authored out of sync.

    No flag and no spec key accepts a raw `craft_json` or `content` value —
    that hand-author-both-and-hope-they-agree foot-gun is exactly what this
    command exists to close. `sender_type` is always `"business"`, the only
    value ever observed live. Any Image block's local file is uploaded for
    real before the plan is built (there's no way to know its real
    `fileId`/`src`/`naturalWidth`/`naturalHeight` otherwise) — except under
    `--dry-run`, which resolves images offline instead (placeholder
    `fileId`/`src`, real dimensions read from the file's own header bytes)
    so a dry run never writes.
    """
    spec_dict, from_stdin = _read_spec(spec_file, what="email template")
    with cli_errors(ValueError, FileNotFoundError):
        spec = _validate_email_spec(spec_dict)
        resolved_sections = (
            email_craft.offline_resolve_spec_images(spec)
            if dry_run
            else email_craft.resolve_spec_images(spec)
        )
    _run_mutation(
        lambda: message_planners.plan_create_template_from_spec(
            spec, resolved_sections
        ),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
        stdin_consumed=from_stdin,
    )


@messages_templates_app.command(
    "clone",
    epilog="Wire format (the two coupled content fields): see `kizen docs show email-templates`",
)
def messages_templates_clone(
    source: str = typer.Argument(..., help="Source template name or UUID."),
    name: str = typer.Option(..., "--name", help="Name for the copy."),
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
    """Copy an email template, both content fields included.

    `craft_json` and `content` are carried over together — copying one
    without the other yields a template whose builder view and real sent
    output disagree.
    """
    _run_mutation(
        lambda: message_planners.plan_clone_template(source, name),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@messages_templates_app.command(
    "update",
    epilog="Wire format (the two coupled content fields): see `kizen docs show email-templates`",
)
def messages_templates_update(
    template: str = typer.Argument(..., help="Template name or UUID."),
    name: str = typer.Option(None, "--name", help="Rename the template."),
    subject: str = typer.Option(None, "--subject", help="Set the subject line."),
    craft_json_file: str = typer.Option(
        None, "--craft-json-file", help="Path to a JSON file to send as `craft_json`."
    ),
    content_file: str = typer.Option(
        None, "--content-file", help="Path to an HTML file to send as `content`."
    ),
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Path to a JSON email-template spec — rebuilds `craft_json`/`content` "
        "together the same way `create` does. Mutually exclusive with "
        "--name/--subject/--craft-json-file/--content-file.",
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
    """PATCH one email template's fields.

    Two ways to update: `--spec-file` rebuilds `craft_json`/`content` from a
    spec, both fields together, the same way `create` does. The raw path
    (`--craft-json-file`/`--content-file`/`--name`/`--subject`) PATCHes
    fields verbatim instead — the server stores both content fields
    independently and compiles neither from the other (confirmed live
    2026-08-25), so sending `craft_json` without `content` on the raw path —
    or the reverse — leaves the builder showing one email while recipients
    receive another. Check the result either way with `messages templates
    get`.
    """
    raw_flags_used = (
        name is not None or subject is not None or craft_json_file or content_file
    )
    if spec_file and raw_flags_used:
        err_console.print(
            "[red]error:[/red] --spec-file cannot be combined with "
            "--name/--subject/--craft-json-file/--content-file."
        )
        raise typer.Exit(code=2)

    if spec_file:
        spec_dict, from_stdin = _read_spec(spec_file, what="email template")
        with cli_errors(ValueError, FileNotFoundError):
            spec = _validate_email_spec(spec_dict)
            resolved_sections = (
                email_craft.offline_resolve_spec_images(spec)
                if dry_run
                else email_craft.resolve_spec_images(spec)
            )
        _run_mutation(
            lambda: message_planners.plan_update_template(
                template, spec=spec, resolved_sections=resolved_sections
            ),
            dry_run=dry_run,
            yes=yes,
            json_out=json_out,
            stdin_consumed=from_stdin,
        )
        return

    patch: dict[str, object] = {}
    if name is not None:
        patch["name"] = name
    if subject is not None:
        patch["subject"] = subject
    if craft_json_file:
        patch["craft_json"] = json.loads(Path(craft_json_file).read_text())
    if content_file:
        patch["content"] = Path(content_file).read_text()

    _run_mutation(
        lambda: message_planners.plan_update_template(template, patch),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@messages_templates_app.command("delete")
def messages_templates_delete(
    template: str = typer.Argument(..., help="Template name or UUID."),
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
    """Delete an email template."""
    _run_mutation(
        lambda: message_planners.plan_delete_template(template),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )


@messages_templates_app.command(
    "craft-config",
    epilog="Spec shape: see `kizen docs show email-templates`",
)
def messages_templates_craft_config(
    spec_file: str = typer.Option(
        "",
        "--spec-file",
        help="Path to a JSON email-template spec. Omit to list the available "
        "block kinds and column layouts instead.",
    ),
    out_html: str = typer.Option(
        "", "--out-html", help="Write the compiled `content` HTML to this file."
    ),
) -> None:
    """Preview the `{craft_json, content}` pair a spec would produce —
    offline, no live calls, runs the exact emitter `create`/`update
    --spec-file` use.

    Unlike `dashboards dashlet-config`, this command's output is **not**
    meant to be pasted into a create/update spec: any Image block is
    resolved without uploading (no live calls here), so its `fileId`/`src`
    are obvious placeholder tokens, not real ones. Use `--out-html` to drop
    the compiled body somewhere a browser (or Outlook) can open it.
    """
    if not spec_file:
        t = Table(title="Block kinds")
        t.add_column("kind")
        for kind in email_craft.known_block_kinds():
            t.add_row(kind)
        console.print(t)

        lt = Table(title="Column layouts (v1)")
        lt.add_column("layout")
        lt.add_column("columns")
        for name in email_craft.known_layouts():
            columns = email_craft.COLUMN_LAYOUTS[name].columns
            lt.add_row(name, ", ".join(str(c) for c in columns))
        console.print(lt)
        return

    spec_dict, _ = _read_spec(spec_file, what="email template")
    with cli_errors(ValueError, FileNotFoundError):
        spec = _validate_email_spec(spec_dict)
        resolved_sections = email_craft.offline_resolve_spec_images(spec)
        sections = email_craft.assemble_sections(resolved_sections)
        craft_json, content = email_craft.build_email_content(sections)

    if out_html:
        Path(out_html).write_text(content)
        err_console.print(f"[dim]wrote compiled content to {out_html}[/dim]")

    out.emit_json({"craft_json": craft_json, "content": content})


@messages_app.command("create")
def messages_create(
    api_name: str = typer.Argument(..., help="Automation api_name."),
    template: str = typer.Option(
        ...,
        "--template",
        help="Email template name or UUID (see `messages templates list`).",
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
    """Create an automation-scoped message from an email template.

    Reference the resulting UUID as the `email_template_id` (or `id`) in a
    notify_member_via_email step spec passed to `automations steps
    add`/`edit`. A message created any other way (raw content, no
    template behind it) won't show as selected in Kizen's builder UI even
    though the step technically references it.
    """
    _run_mutation(
        lambda: message_planners.plan_create_automation_message(api_name, template),
        dry_run=dry_run,
        yes=yes,
        json_out=json_out,
    )
