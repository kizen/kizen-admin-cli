"""Text the CLI didn't write (SQL, code, logs, server errors) prints verbatim.

rich parses `console.print` strings as markup: `[/.-]` raises `MarkupError`,
and anything shaped like a lowercase tag (`row[field]`, `[completed]`) is
silently deleted. It also swaps emoji in for shortcodes (`:cd:` in an IPv6
address) and hard-wraps at the console's 220 columns. These pin the sites
that print remote text.
"""

from __future__ import annotations

import io
import json
import re

import httpx
import pytest
import respx
from rich.console import Console
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.api.client import KizenAPIError
from kizen_builder.cli import _shared
from kizen_builder.cli import code as cli_code
from kizen_builder.cli._run_render import print_step_log
from kizen_builder.tools import smart_connectors as sc_tools
from kizen_builder.tools.planners import automations as auto_planners
from tests.conftest import FAKE_BASE_URL

runner = CliRunner()

SQL_LINE = "+ '(?i)^([0-9]{1,2})[/.-]([0-9]{1,2})'"
IPV6_LINE = "+ where ip = '2001:db8:ab:cd::1'"
LONG_LINE = "+ select " + ", ".join(f"col_{i}" for i in range(40)) + " from t"
DIFF = (
    "--- remote\n+++ local\n@@ -1 +1,3 @@\n- 'old'\n"
    f"{SQL_LINE}\n{IPV6_LINE}\n{LONG_LINE}\n"
)


def _push_plan(*_a, **_kw):
    return {
        "connector": "conn-1",
        "script_id": "script-1",
        "changed": True,
        "local_sql": "",
        "remote_sql": "",
        "diff": DIFF,
        "script_status": "draft",
        "current_draft_id": "script-1",
        "warning": None,
    }


def test_push_dry_run_prints_bracketed_sql_verbatim(monkeypatch):
    monkeypatch.setattr(sc_tools, "plan_push", _push_plan)
    result = runner.invoke(cli.app, ["smart-connectors", "push", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert SQL_LINE in result.stdout
    assert IPV6_LINE in result.stdout
    assert LONG_LINE in result.stdout


def test_push_dry_run_json_emits_the_diff_unchanged(monkeypatch):
    monkeypatch.setattr(sc_tools, "plan_push", _push_plan)
    result = runner.invoke(cli.app, ["smart-connectors", "push", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["diff"] == DIFF


def test_execution_sql_prints_script_verbatim(monkeypatch):
    script = "select row[field] from t where d ~ '[/.-]'"
    monkeypatch.setattr(
        sc_tools, "get_execution_script", lambda *a: {"user_script": script}
    )
    result = runner.invoke(cli.app, ["smart-connectors", "executions", "sql", "c", "e"])
    assert result.exit_code == 0, result.output
    assert script in result.stdout


def test_execution_sql_empty_placeholder_stays_styled(monkeypatch):
    monkeypatch.setattr(
        sc_tools, "get_execution_script", lambda *a: {"user_script": ""}
    )
    result = runner.invoke(cli.app, ["smart-connectors", "executions", "sql", "c", "e"])
    assert result.exit_code == 0, result.output
    assert "(empty)" in result.stdout
    assert "[dim]" not in result.stdout


def test_automations_diff_shows_bracketed_code_step_values(monkeypatch):
    fake_result = {
        "env": "testenv",
        "api_name": "x",
        "id": "auto-1",
        "revision": 4,
        "diff": [
            {
                "path": "steps.76af48bd.action_code_step.script",
                "before": "v = row[field]",
                "after": "x [/.-]",
            }
        ],
    }
    monkeypatch.setattr(auto_planners, "diff_automation", lambda spec: fake_result)
    spec = json.dumps({"api_name": "x", "name": "X", "type": "global", "steps": []})
    result = runner.invoke(cli.app, ["automations", "diff", "x"], input=spec)
    assert result.exit_code == 0, result.output
    assert "'v = row[field]' → 'x [/.-]'" in result.stdout


def test_cli_errors_prints_server_message_verbatim_with_styled_prefix(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(
        _shared,
        "err_console",
        Console(file=buf, force_terminal=True, color_system="standard", width=220),
    )
    message = "bad pattern '[/.-]' near [bold] status:ok:done"

    def boom(spec):
        raise KizenAPIError(400, message)

    monkeypatch.setattr(auto_planners, "diff_automation", boom)
    spec = json.dumps({"api_name": "x", "name": "X", "type": "global", "steps": []})
    result = runner.invoke(cli.app, ["automations", "diff", "x"], input=spec)

    assert result.exit_code == 1
    rendered = buf.getvalue()
    # The CLI's own tag still renders as red, the server text does not.
    assert "\x1b[31merror:\x1b[0m" in rendered
    plain = re.sub(r"\x1b\[[0-9;]*m", "", rendered)
    assert f"error: HTTP 400: {message}" in plain
    assert "[red]" not in plain
    assert "Traceback" not in result.output


def test_print_step_log_prints_stdout_and_traceback_verbatim(capsys):
    print_step_log(
        1,
        {
            "kind": "step",
            "type": "code_step",
            "description": "parse [/x] rows",
            "detailed_log": {
                "stdout": "got row[field] and [/x]",
                "traceback": "KeyError: row[field] [/x]",
            },
        },
    )
    out = capsys.readouterr().out
    assert "#1 step (code_step) — parse [/x] rows" in out
    assert "stdout: got row[field] and [/x]" in out
    assert "KeyError: row[field] [/x]" in out


@pytest.mark.parametrize(
    "detailed_log",
    [{"logs": ["saw row[field] at :x:"]}, {"k": ["row[field]", ":x:"]}],
    ids=["logs", "json-dump"],
)
def test_print_step_log_prints_logs_and_json_dump_verbatim(capsys, detailed_log):
    print_step_log(1, {"kind": "step", "detailed_log": detailed_log})
    out = capsys.readouterr().out
    assert "row[field]" in out
    assert ":x:" in out


def test_coderunner_result_prints_logs_and_error_detail_verbatim(capsys):
    cli_code._render_coderunner_result(
        {
            "env": "testenv",
            "duration_ms": 5,
            "logs": ["saw row[field]", "closing [/x]"],
            "error": {
                "error": "KeyError: 'field'",
                "detail": "Traceback:\n  v = row[field]\nKeyError [/x]",
            },
        }
    )
    out = capsys.readouterr().out
    assert "saw row[field]" in out
    assert "closing [/x]" in out
    assert "v = row[field]" in out
    assert "KeyError [/x]" in out


def test_connector_planner_error_prints_server_message_verbatim(monkeypatch):
    message = "field [/data] invalid"

    def boom(*_a, **_kw):
        raise KizenAPIError(400, message)

    monkeypatch.setattr(sc_tools, "plan_send_webhook", boom)
    result = runner.invoke(
        cli.app, ["smart-connectors", "send-webhook", "c", "--body", "{}"]
    )
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stderr)
    assert f"error: HTTP 400: {message}" in plain


@respx.mock
def test_code_test_http_audit_prints_remote_text_literally(tmp_path):
    run = {
        "request_id": "r",
        "duration_ms": 1.0,
        "values": {},
        "logs": [],
        "http_requests": {
            "count": 2,
            "not_logged": 0,
            "requests": [
                {
                    "method": "GET",
                    "url": "https://example.test/a?filter[status]=open&page[size]=10",
                    "body": "",
                    "requestErrorType": "ECONN [/x]",
                    "responseStatusCode": 200,
                    "responseBody": "",
                    "duration": 1.0,
                },
                {
                    "method": "GET",
                    "url": "https://example.test/x[/y]",
                    "responseStatusCode": 200,
                },
            ],
        },
        "error": None,
    }
    respx.post(f"{FAKE_BASE_URL}/api/coderunner/run").mock(
        return_value=httpx.Response(200, json=run)
    )
    script = tmp_path / "s.py"
    script.write_text("outputs.x=1")
    result = runner.invoke(
        cli.app, ["code", "test", "--yes", "--script", str(script), "-v"]
    )
    assert result.exit_code == 0, result.output
    assert "(empty)" in result.stdout
    assert "[dim]" not in result.stdout
    assert "ECONN [/x]" in result.stdout
    assert "https://example.test/a?filter[status]=open&page[size]=10" in result.stdout
    assert "https://example.test/x[/y]" in result.stdout
