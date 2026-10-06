"""`kizen code` — unit-test code_step scripts in the live sandbox (coderunner)."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.markup import escape
from rich.table import Table

from kizen_builder.api.client import KizenAPIError
from kizen_builder.cli._mutations import _confirm_or_abort
from kizen_builder.cli._shared import _short, app, cli_errors, console, err_console
from kizen_builder.config import load_env_config
from kizen_builder.tools import coderunner as code_tools

code_app = typer.Typer(
    help=(
        "Unit-test code_step scripts in the live sandbox via "
        "`POST /api/coderunner/run`. Runs your script in the same Lambda as a "
        "real `code_step`; `kizen.api` calls in it use real credentials and "
        "can write live data. Asks y/N first (`--yes` skips)."
    ),
    no_args_is_help=True,
)
app.add_typer(code_app, name="code")


def _parse_input_spec(spec: str) -> dict[str, Any]:
    """Parse a `--input name=value:type` spec into {name, value, code}.

    `type` is a friendly data_type name (`number`) or a short code (`n`);
    it's the suffix after the LAST ':' (types carry no colon), so values may
    contain ':'. The name is everything before the first '='. A missing
    ':type' defaults to string. The token is resolved to a short code by the
    tool layer (`to_kizen_type`), so it's carried through as-is here.
    """
    name, sep, rest = spec.partition("=")
    if not sep or not name.strip():
        raise typer.BadParameter(f"--input must be name=value[:type] (got {spec!r}).")
    value, colon, code = rest.rpartition(":")
    if not colon:
        # No type given — the whole remainder is the value; default string.
        value, code = rest, "s"
    return {"name": name.strip(), "value": value, "code": code.strip() or "s"}


def _parse_output_spec(spec: str) -> dict[str, Any]:
    """Parse a `--output name:type` spec into {name, code}. Default string.

    `type` is a friendly data_type name or a short code (resolved downstream).
    """
    name, colon, code = spec.rpartition(":")
    if not colon:
        name, code = spec, "s"
    if not name.strip():
        raise typer.BadParameter(f"--output must be name[:type] (got {spec!r}).")
    return {"name": name.strip(), "code": code.strip() or "s"}


def _load_inputs_file(text: str) -> dict[str, dict[str, Any]]:
    """Parse a JSON inputs file into an ordered {name: {name, value, code}} map.

    Two accepted shapes (mirroring what the code-step UI sends):
      - typed:  {"n": {"type": "number", "value": 21}, …}
      - bare:   {"n": 21, "who": "world"}  (type defaults to string)
    `type` may be a friendly name or a short code. Returned so individual
    `--input` flags can override by name.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f"--inputs-file is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise typer.BadParameter(
            "--inputs-file must be a JSON object mapping input name → "
            '{"type", "value"} (or name → value).'
        )
    out: dict[str, dict[str, Any]] = {}
    for name, spec in data.items():
        if isinstance(spec, dict) and ("value" in spec or "type" in spec):
            code = str(spec.get("type") or "s")
            value = spec.get("value")
        else:
            code, value = "s", spec  # bare {name: value}
        out[name] = {"name": name, "value": value, "code": code}
    return out


def _load_outputs_file(text: str) -> dict[str, dict[str, Any]]:
    """Parse a JSON outputs file into an ordered {name: {name, code}} map.

    Shape: `{"doubled": "number", "greeting": "string"}` — value is the type
    (friendly name or short code). `{"doubled": {"type": "number"}}` is also
    accepted for symmetry with the inputs file.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f"--outputs-file is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise typer.BadParameter(
            "--outputs-file must be a JSON object mapping output name → type."
        )
    out: dict[str, dict[str, Any]] = {}
    for name, spec in data.items():
        code = spec.get("type") if isinstance(spec, dict) else spec
        out[name] = {"name": name, "code": str(code or "s")}
    return out


def _flatten_errors(body: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Flatten a DRF-style nested error body to (dotted-path, message) pairs.

    coderunner validation errors nest, e.g.
    `{"inputs": {"x": {"t": ["Invalid Kizen type 'mail'"]}}}` → one row
    `("inputs.x.t", "Invalid Kizen type 'mail'")`. Lists of strings and bare
    strings/dicts are handled too.
    """
    out: list[tuple[str, str]] = []
    if isinstance(body, dict):
        for k, v in body.items():
            out.extend(_flatten_errors(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(body, list):
        for item in body:
            out.extend(_flatten_errors(item, prefix))
    else:
        out.append((prefix, str(body)))
    return out


def _fmt_ms(ms: Any) -> str:
    """Format a duration in ms (the endpoint returns a float like 9.4)."""
    if ms is None:
        return "?"
    try:
        return f"{float(ms):.1f}ms"
    except (TypeError, ValueError):
        return str(ms)


def _fmt_http_body(body: Any, limit: int = 4000) -> str:
    """Render an http_requests body/header value for the detail view.

    Bodies come over the wire as strings; pretty-print JSON when it parses,
    otherwise show the raw text. Truncate very large bodies (full payload is
    always available via `--json`).
    """
    if body is None or body == "":
        return "[dim](empty)[/dim]"
    if isinstance(body, (dict, list)):
        text = json.dumps(body, indent=2)
    else:
        text = str(body)
        with contextlib.suppress(ValueError, TypeError):
            text = json.dumps(json.loads(text), indent=2)
    if len(text) > limit:
        text = (
            text[:limit]
            + f"\n[dim]… (truncated at {limit} chars — --json for full)[/dim]"
        )
    return text


def _render_http_request_detail(r: dict[str, Any]) -> None:
    """Expand one http_requests entry: request + response, bodies included."""
    method = r.get("method", "")
    url = r.get("url", "")
    status = r.get("responseStatusCode", "")
    dur = _fmt_ms(r.get("duration")) if r.get("duration") is not None else ""
    err = r.get("requestErrorType")
    heading = f"[bold]{method}[/bold] {url}  →  {status}"
    if dur:
        heading += f"  ({dur})"
    if err:
        heading += f"  [red]{err}[/red]"
    console.print(heading)
    console.print("  [dim]request headers:[/dim]")
    console.print(
        f"    {_fmt_http_body(r.get('headers')).replace(chr(10), chr(10) + '    ')}",
        markup=False,
        emoji=False,
        soft_wrap=True,
    )
    console.print("  [dim]request body:[/dim]")
    console.print(
        f"    {_fmt_http_body(r.get('body')).replace(chr(10), chr(10) + '    ')}",
        markup=False,
        emoji=False,
        soft_wrap=True,
    )
    rb = r.get("responseBody")
    note = ""
    if isinstance(rb, str) and rb:
        try:
            json.loads(rb)
        except (ValueError, TypeError):
            # coderunner caps captured response bodies (~1KB), so a long body
            # is often truncated mid-JSON — flag it rather than look malformed.
            note = "  [dim](may be truncated by the sandbox's ~1KB capture)[/dim]"
    console.print(f"  [dim]response body:[/dim]{note}")
    console.print(
        f"    {_fmt_http_body(rb).replace(chr(10), chr(10) + '    ')}",
        markup=False,
        emoji=False,
        soft_wrap=True,
    )


def _render_coderunner_result(
    result: dict[str, Any], http_detail: bool = False
) -> None:
    """Pretty-print a coderunner run: values, logs, http audit, and errors.

    Surfaces every part of the CodeRunnerResponse. `logs` is the primary
    debug channel (populated by `outputs.log(...)`; plain `print()` does
    NOT land there), so it's always shown — explicitly "(no logs)" when empty.

    The http audit renders as a compact table by default. `http_detail`
    expands every request into request/response bodies; a failed request
    (status >= 400 or a transport error) auto-expands even without the flag.
    """
    header = f"[dim]env {result.get('env')} · {_fmt_ms(result.get('duration_ms'))}"
    if result.get("request_id"):
        header += f" · request {result['request_id']}"
    header += "[/dim]"
    console.print(header)

    error = result.get("error")
    if error:
        # The sandbox raised — error is {error, detail} with a full traceback.
        if isinstance(error, dict):
            console.print(
                f"[red]script error:[/red] {escape(str(error.get('error')))}",
                emoji=False,
            )
            detail = error.get("detail")
            if detail:
                console.print(f"[red]{escape(str(detail))}[/red]", emoji=False)
        else:
            console.print(f"[red]script error:[/red] {escape(str(error))}", emoji=False)

    values = result.get("values") or {}
    if values:
        vt = Table(title="outputs")
        vt.add_column("name", style="cyan")
        vt.add_column("t")
        vt.add_column("v")
        for name, cell in values.items():
            if isinstance(cell, dict):
                vt.add_row(name, str(cell.get("t", "")), _short(cell.get("v"), 120))
            else:
                vt.add_row(name, "", _short(cell, 120))
        console.print(vt)
    elif not error:
        console.print("[dim](no outputs)[/dim]")

    # logs — the debug channel. Always render, even when empty.
    logs = result.get("logs") or []
    if logs:
        console.print(f"[bold]logs[/bold] ({len(logs)})")
        for line in logs:
            console.print(f"  {line}", markup=False, emoji=False, soft_wrap=True)
    else:
        console.print(
            '[dim]logs: (none — use outputs.log("…") to emit; plain '
            "print() is not captured)[/dim]"
        )

    # http_requests — the wire shape is a dict: {count, not_logged, requests}.
    hr = result.get("http_requests") or {}
    if isinstance(hr, list):  # defensive: tolerate a bare list
        hr = {"count": len(hr), "not_logged": 0, "requests": hr}
    reqs = hr.get("requests") or []
    count = hr.get("count", len(reqs))
    if count or reqs:
        not_logged = hr.get("not_logged") or 0
        suffix = f", {not_logged} not logged" if not_logged else ""
        rt = Table(title=f"http_requests — {count} call(s){suffix} (secrets redacted)")
        rt.add_column("method")
        rt.add_column("url")
        rt.add_column("status")
        rt.add_column("ms")
        for r in reqs:
            if not isinstance(r, dict):
                rt.add_row("", _short(r, 120), "", "")
                continue
            rt.add_row(
                str(r.get("method", "")),
                _short(r.get("url", ""), 120),
                str(r.get("responseStatusCode", "")),
                _fmt_ms(r.get("duration")) if r.get("duration") is not None else "",
            )
        console.print(rt)

        # Detail view: every request with --http-detail; otherwise only the
        # ones that failed (that's when the bodies matter most).
        def _failed(r: dict[str, Any]) -> bool:
            status = r.get("responseStatusCode")
            return bool(r.get("requestErrorType")) or (
                isinstance(status, int) and status >= 400
            )

        to_expand = [
            r for r in reqs if isinstance(r, dict) and (http_detail or _failed(r))
        ]
        if to_expand:
            if not http_detail:
                console.print(
                    "[dim](failed request detail below — --http-detail/-v shows "
                    "all)[/dim]"
                )
            for r in to_expand:
                _render_http_request_detail(r)


@code_app.command("test")
def code_test(
    script_file: str = typer.Option(
        None, "--script", help="Path to the script file. Omit to read from stdin."
    ),
    inputs: list[str] = typer.Option(
        [],
        "--input",
        help="An input: name=value:type (repeatable). type is a data_type name "
        "or its short code — string/s, number/n, boolean/b, phone/p, uuid/u "
        "(bare id), list/l ([{t,v},…] JSON), date/d (YYYY-MM-DD), datetime/dt "
        "(ISO 8601), file/f (uuid); defaults to string. e.g. --input n=21:number "
        "--input who=world:string. Overrides same-named entries in --inputs-file.",
    ),
    outputs: list[str] = typer.Option(
        [],
        "--output",
        help="An output: name:type (repeatable). Declares the expected output "
        "type (same names/codes as --input). e.g. --output doubled:number "
        "--output greeting:string. Overrides --outputs-file.",
    ),
    inputs_file: str = typer.Option(
        None,
        "--inputs-file",
        help='JSON file of inputs for bulk runs: {"n": {"type": "number", '
        '"value": 21}, …} (or a bare {"n": 21}). \'-\' reads stdin (only when '
        "--script is a file). Individual --input flags override by name.",
    ),
    outputs_file: str = typer.Option(
        None,
        "--outputs-file",
        help='JSON file of output types: {"doubled": "number", "greeting": '
        '"string"}. Individual --output flags override by name.',
    ),
    secrets: list[str] = typer.Option(
        [],
        "--secret",
        help="Secret api-name to inject into the sandbox (repeatable). The "
        "value lives in the env; only the name is passed.",
    ),
    runtime: str = typer.Option(
        None, "--runtime", help="python-3-13 (default) or python-3-12."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the y/N confirmation prompt."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw response as JSON."
    ),
    http_detail: bool = typer.Option(
        False,
        "--http-detail",
        "-v",
        help="Expand each captured kizen.api call with its request and "
        "response bodies (failed calls expand automatically). Secrets stay "
        "redacted; --json still has the full payload.",
    ),
) -> None:
    """Run a code_step script in the live sandbox and print its outputs.

    The script uses the same namespace as an automation code_step —
    `inputs.<name>` to read, `outputs.<name> = …` to write, and
    `outputs.log("…")` to emit a debug line (one string argument only; plain
    `print()` is NOT captured). `kizen.api` works inside the sandbox with
    auth auto-injected; paths there are relative to `/api` (use
    `/custom-objects`, not `/api/custom-objects`).

    Inputs and outputs are typed by a data_type **name** (`number`,
    `datetime`, …) or its short code (`n`, `dt`, …) — both work, and
    anything unrecognized defaults to `string` (it is NOT a raw
    `field_type`: `integer` maps to number for you). See `--input` for
    the full code table. For many inputs, use `--inputs-file` /
    `--outputs-file` (JSON); `--input`/`--output` flags override
    same-named file entries. Add `--http-detail` / `-v` to see each
    `kizen.api` call's request and response bodies when debugging.

    Runs your script in the same Lambda as a real `code_step`; `kizen.api`
    calls in it use real credentials and can write live data. Asks y/N first
    (`--yes` skips). A script or `--inputs-file -` read from stdin leaves
    nothing to answer the prompt with, so it needs `--yes`.
    """
    if script_file:
        try:
            script = Path(script_file).read_text()
        except OSError as e:
            err_console.print(f"[red]error:[/red] can't read {script_file}: {e}")
            raise typer.Exit(code=1) from e
    else:
        if inputs_file == "-":
            err_console.print(
                "[red]error:[/red] can't read both the script and --inputs-file "
                "from stdin. Pass --script <file> when --inputs-file is '-'."
            )
            raise typer.Exit(code=2)
        if sys.stdin.isatty():
            err_console.print(
                "[red]error:[/red] no script — pass --script <file> or pipe one "
                "on stdin."
            )
            raise typer.Exit(code=2)
        script = sys.stdin.read()

    # Merge: file entries first, then --input/--output flags override by name.
    input_map: dict[str, dict[str, Any]] = {}
    if inputs_file:
        if inputs_file == "-":
            text = sys.stdin.read()
        else:
            try:
                text = Path(inputs_file).read_text()
            except OSError as e:
                err_console.print(f"[red]error:[/red] can't read {inputs_file}: {e}")
                raise typer.Exit(code=1) from e
        input_map.update(_load_inputs_file(text))
    for s in inputs:
        d = _parse_input_spec(s)
        input_map[d["name"]] = d
    parsed_inputs = list(input_map.values())

    output_map: dict[str, dict[str, Any]] = {}
    if outputs_file:
        try:
            text = Path(outputs_file).read_text()
        except OSError as e:
            err_console.print(f"[red]error:[/red] can't read {outputs_file}: {e}")
            raise typer.Exit(code=1) from e
        output_map.update(_load_outputs_file(text))
    for s in outputs:
        d = _parse_output_spec(s)
        output_map[d["name"]] = d
    parsed_outputs = list(output_map.values())

    if not yes:
        with cli_errors():
            env = load_env_config().name
        _confirm_or_abort(
            f"Run {script_file or 'stdin'} in {env} on "
            f"{runtime or code_tools.DEFAULT_RUNTIME}? Its kizen.api calls can "
            "write live data.",
            yes=False,
            hint="Re-run with --yes.",
            stdin_consumed=not script_file or inputs_file == "-",
        )

    # The inner handler pre-empts `cli_errors` for the one API failure that
    # deserves more than a single line; everything else here — a bad config, a
    # CodeRunnerError — is the ordinary `error:` line.
    with cli_errors(code_tools.CodeRunnerError):
        try:
            result = code_tools.run_code_step(
                script=script,
                inputs=parsed_inputs,
                outputs=parsed_outputs,
                secrets=secrets,
                runtime=runtime,
            )
        except KizenAPIError as e:
            # A genuine *request*-validation 400 (bad type code, unknown secret)
            # — a field-keyed dict, not the run envelope (script errors are
            # returned by the tool as results, not raised). Flatten to leaves.
            err_console.print(
                f"[red]coderunner rejected the request[/red] (HTTP {e.status_code}):"
            )
            if isinstance(e.body, (dict, list)):
                for path, msg in _flatten_errors(e.body):
                    loc = f"[red]{path}:[/red] " if path else "[red]- [/red]"
                    err_console.print(f"  {loc}{_short(msg, 300)}")
            else:
                err_console.print(f"  [red]{e.message}[/red]")
            raise typer.Exit(code=1) from e

    if json_out:
        typer.echo(json.dumps(result.get("raw"), indent=2))
    else:
        _render_coderunner_result(result, http_detail=http_detail)
    # A script that raised is a completed-but-failed run — exit non-zero so it
    # scripts cleanly, but only after the result (logs + traceback) is shown.
    if result.get("error"):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
