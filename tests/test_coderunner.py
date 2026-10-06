"""coderunner (`kizen code test`): {t,v} encoding, wire payload, and the
CLI's rendering / validation-error handling.

The endpoint runs a script in the code_step Lambda sandbox standalone. Types
are given as friendly data_type names (number, datetime, …) or short codes
(n, dt, …); both resolve via to_kizen_type (mirroring the production client's
DATA_TYPE_TO_KIZEN), defaulting anything unrecognized to 's'. Scalar values
are sent as raw strings — the server coerces by the `t` code (no local
number/bool coercion); only JSON-looking values ([/{) are parsed, for lists.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.cli import code as cli_code
from kizen_builder.tools import coderunner as cr
from tests.conftest import FAKE_BASE_URL, plain

runner = CliRunner()
RUN_URL = f"{FAKE_BASE_URL}/api/coderunner/run"


# --- type resolution (friendly names + short codes) ----------------------


def test_friendly_names_map_to_short_codes():
    # Mirrors DATA_TYPE_TO_KIZEN exactly.
    assert cr.to_kizen_type("number") == "n"
    assert cr.to_kizen_type("integer") == "n"
    assert cr.to_kizen_type("string") == "s"
    assert cr.to_kizen_type("text") == "s"
    assert cr.to_kizen_type("boolean") == "b"
    assert cr.to_kizen_type("date") == "d"
    assert cr.to_kizen_type("datetime") == "dt"
    assert cr.to_kizen_type("phone") == "p"
    assert cr.to_kizen_type("phonenumber") == "p"
    assert cr.to_kizen_type("uuid") == "u"
    assert cr.to_kizen_type("file") == "f"
    assert cr.to_kizen_type("list") == "l"


def test_short_codes_pass_through_unchanged():
    for code in ("s", "n", "b", "d", "dt", "p", "u", "f", "l"):
        assert cr.to_kizen_type(code) == code


def test_unrecognized_type_defaults_to_string():
    assert cr.to_kizen_type("integer_field") == "s"
    assert cr.to_kizen_type("email") == "s"  # no email code exists
    assert cr.to_kizen_type("") == "s"
    assert cr.to_kizen_type(None) == "s"


def test_case_insensitive_type_names():
    assert cr.to_kizen_type("Number") == "n"
    assert cr.to_kizen_type("DateTime") == "dt"


# --- value encoding (strings, server coerces) ----------------------------


def test_scalar_values_sent_as_raw_strings():
    # No local number/bool coercion — the raw string is sent as-is.
    assert cr.encode_input_value("s", "21") == "21"
    assert cr.encode_input_value("n", "21") == "21"
    assert cr.encode_input_value("b", "true") == "true"
    assert cr.encode_input_value("d", "2026-01-01") == "2026-01-01"


def test_non_string_value_passed_through():
    # A JSON-file value that's already a number/list is left untouched.
    assert cr.encode_input_value("n", 21) == 21
    assert cr.encode_input_value("l", [1, 2]) == [1, 2]


def test_list_value_parsed_from_json():
    # JSON parsing happens ONLY for the list ('l') type.
    assert cr.encode_input_value("l", '[{"t":"s","v":"a"}]') == [{"t": "s", "v": "a"}]


def test_json_string_into_string_input_is_not_parsed():
    # Regression: a JSON blob in a *string* input (e.g. a code-step ticker_list
    # variable) must survive verbatim, not be parsed into a list and later
    # re-stringified with Python repr (single quotes), which breaks json.loads.
    assert cr.encode_input_value("s", '["BND", "VTI"]') == '["BND", "VTI"]'
    assert cr.encode_input_value("s", '{"BND": 73.64}') == '{"BND": 73.64}'


# --- build_payload -------------------------------------------------------


def test_build_payload_sends_string_values_and_resolves_friendly_names():
    payload = cr.build_payload(
        script="outputs.doubled=inputs.n*2",
        inputs=[
            {"name": "n", "code": "number", "value": "21"},  # friendly name
            {"name": "who", "code": "s", "value": "world"},  # short code
        ],
        outputs=[
            {"name": "doubled", "code": "number"},
            {"name": "flag", "code": "boolean"},
        ],
    )
    assert payload["runtime"] == "python-3-13"
    # v is the raw string; production sends {t:'n', v:'21'}.
    assert payload["inputs"] == {
        "n": {"t": "n", "v": "21"},
        "who": {"t": "s", "v": "world"},
    }
    assert payload["output_types"] == {"doubled": "n", "flag": "b"}
    assert "secrets" not in payload


def test_build_payload_empty_inputs_outputs_are_explicit_null():
    payload = cr.build_payload(script="x=1")
    assert payload["inputs"] is None
    assert payload["output_types"] is None


def test_build_payload_empty_script_rejected():
    with pytest.raises(cr.CodeRunnerError):
        cr.build_payload(script="   ")


def test_build_payload_rejects_duplicate_input():
    with pytest.raises(cr.CodeRunnerError):
        cr.build_payload(
            script="x=1",
            inputs=[
                {"name": "n", "code": "n", "value": "1"},
                {"name": "n", "code": "n", "value": "2"},
            ],
        )


def test_unsupported_runtime_hard_fails_client_side():
    with pytest.raises(cr.CodeRunnerError) as exc:
        cr.build_payload(script="x=1", runtime="python-2-7")
    assert "python-3-12" in str(exc.value) and "python-3-13" in str(exc.value)


def test_missing_runtime_defaults():
    assert cr.build_payload(script="x=1", runtime=None)["runtime"] == "python-3-13"
    assert cr.build_payload(script="x=1", runtime="")["runtime"] == "python-3-13"


# --- tool round-trip -----------------------------------------------------


@respx.mock
def test_run_code_step_posts_payload_and_normalizes_response():
    route = respx.post(RUN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "request_id": "req-1",
                "duration_ms": 42,
                "values": {"doubled": {"t": "n", "v": "42.0"}},
                "logs": [],
                "http_requests": {"count": 0, "not_logged": 0, "requests": []},
                "error": None,
            },
        )
    )
    result = cr.run_code_step(
        script="outputs.doubled=inputs.n*2",
        inputs=[{"name": "n", "code": "number", "value": "21"}],
        outputs=[{"name": "doubled", "code": "number"}],
    )
    body = json.loads(route.calls[-1].request.content)
    assert body["inputs"] == {"n": {"t": "n", "v": "21"}}
    assert body["output_types"] == {"doubled": "n"}
    assert result["values"] == {"doubled": {"t": "n", "v": "42.0"}}
    assert result["duration_ms"] == 42
    assert result["error"] is None


# --- CLI wiring ----------------------------------------------------------


@respx.mock
def test_cli_test_command_renders_outputs(tmp_path):
    respx.post(RUN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "request_id": "req-9",
                "duration_ms": 12,
                "values": {"greeting": {"t": "s", "v": "hello world"}},
                "logs": [],
                "http_requests": [],
                "error": None,
            },
        )
    )
    script = tmp_path / "s.py"
    script.write_text("outputs.greeting='hello '+inputs.who")
    result = runner.invoke(
        cli.app,
        [
            "code",
            "test",
            "--yes",
            "--script",
            str(script),
            "--input",
            "who=world:s",
            "--declare-output",
            "greeting:s",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "hello world" in result.stdout


@respx.mock
def test_cli_test_command_json_output(tmp_path):
    raw = {
        "request_id": "req-9",
        "duration_ms": 12,
        "values": {"n2": {"t": "n", "v": "4.0"}},
        "logs": [],
        "http_requests": [],
        "error": None,
    }
    respx.post(RUN_URL).mock(return_value=httpx.Response(200, json=raw))
    script = tmp_path / "s.py"
    script.write_text("outputs.n2=inputs.n*2")
    result = runner.invoke(
        cli.app,
        [
            "code",
            "test",
            "--yes",
            "--script",
            str(script),
            "--input",
            "n=2:n",
            "--declare-output",
            "n2:n",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout) == raw


@respx.mock
def test_cli_surfaces_validation_400_error(tmp_path):
    # Bad type code -> HTTP 400 with a field-keyed error dict, not the envelope.
    respx.post(RUN_URL).mock(
        return_value=httpx.Response(
            400, json={"inputs": ["Invalid Kizen type 'string'"]}
        )
    )
    script = tmp_path / "s.py"
    script.write_text("outputs.x=1")
    result = runner.invoke(
        cli.app,
        [
            "code",
            "test",
            "--yes",
            "--script",
            str(script),
            "--input",
            "n=1:string",
            "--declare-output",
            "x:n",
        ],
    )
    assert result.exit_code == 1
    assert "Invalid Kizen type" in result.stderr


@respx.mock
def test_cli_surfaces_script_error_traceback(tmp_path):
    # A script that raises comes back as HTTP 400 carrying the FULL response
    # envelope (verified live) — request_id + logs + traceback, not a
    # field-keyed validation dict. It's a completed-but-failed run: render the
    # logs + traceback, then exit non-zero.
    respx.post(RUN_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "request_id": "req-err",
                "duration_ms": 5.0,
                "values": None,
                "logs": ["got here"],
                "http_requests": {"count": 0, "not_logged": 0, "requests": []},
                "error": {
                    "error": "ZeroDivisionError",
                    "detail": "Traceback ... division by zero",
                },
            },
        )
    )
    script = tmp_path / "s.py"
    script.write_text("outputs.x=1/0")
    result = runner.invoke(
        cli.app,
        ["code", "test", "--yes", "--script", str(script), "--declare-output", "x:n"],
    )
    assert result.exit_code == 1  # completed-but-failed run
    assert "ZeroDivisionError" in result.stdout
    assert "division by zero" in result.stdout
    assert "got here" in result.stdout  # logs still surfaced


@respx.mock
def test_script_error_400_envelope_returned_as_result_not_raised():
    # The tool must detect the envelope (request_id present) and return it,
    # not re-raise KizenAPIError.
    respx.post(RUN_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "request_id": "req-err",
                "duration_ms": 3.0,
                "values": None,
                "logs": ["a log line"],
                "http_requests": {"count": 0, "not_logged": 0, "requests": []},
                "error": {"error": "ValueError", "detail": "boom"},
            },
        )
    )
    result = cr.run_code_step(script="raise ValueError('boom')")
    assert result["error"]["error"] == "ValueError"
    assert result["logs"] == ["a log line"]
    assert result["values"] == {}


@respx.mock
def test_cli_renders_http_requests_dict_shape(tmp_path):
    respx.post(RUN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "request_id": "req-http",
                "duration_ms": 20.0,
                "values": {"ok": {"t": "b", "v": True}},
                "logs": ["fetched objects"],
                "http_requests": {
                    "count": 1,
                    "not_logged": 0,
                    "requests": [
                        {
                            "method": "GET",
                            "url": "https://app.go.kizen.com/api/custom-objects",
                            "headers": {"X-API-KEY": "[REDACTED]"},
                            "responseStatusCode": 200,
                            "duration": 12.3,
                        }
                    ],
                },
                "error": None,
            },
        )
    )
    script = tmp_path / "s.py"
    script.write_text("outputs.ok=True")
    result = runner.invoke(
        cli.app,
        ["code", "test", "--yes", "--script", str(script), "--declare-output", "ok:b"],
    )
    assert result.exit_code == 0, result.stdout
    assert "custom-objects" in result.stdout
    assert "fetched objects" in result.stdout  # logs rendered
    assert "http_requests" in result.stdout


@respx.mock
def test_cli_shows_no_logs_hint_when_empty(tmp_path):
    respx.post(RUN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "request_id": "r",
                "duration_ms": 1.0,
                "values": {"x": {"t": "n", "v": "1.0"}},
                "logs": [],
                "http_requests": {"count": 0, "not_logged": 0, "requests": []},
                "error": None,
            },
        )
    )
    script = tmp_path / "s.py"
    script.write_text("outputs.x=1")
    result = runner.invoke(
        cli.app,
        ["code", "test", "--yes", "--script", str(script), "--declare-output", "x:n"],
    )
    assert result.exit_code == 0, result.stdout
    assert "outputs.log" in result.stdout  # empty-logs hint mentions the channel


def test_flatten_nested_validation_errors():
    body = {"inputs": {"x": {"t": ["Invalid Kizen type 'mail'"]}}}
    flat = cli_code._flatten_errors(body)
    assert flat == [("inputs.x.t", "Invalid Kizen type 'mail'")]


def test_input_spec_parsing():
    # short code and friendly name both carried through as-is (resolved later)
    assert cli_code._parse_input_spec("n=21:n") == {
        "name": "n",
        "value": "21",
        "code": "n",
    }
    assert cli_code._parse_input_spec("n=21:number") == {
        "name": "n",
        "value": "21",
        "code": "number",
    }
    # value containing a colon; type is the last :segment
    assert cli_code._parse_input_spec("t=12:30:s") == {
        "name": "t",
        "value": "12:30",
        "code": "s",
    }
    # no type -> default s
    assert cli_code._parse_input_spec("who=world") == {
        "name": "who",
        "value": "world",
        "code": "s",
    }


def test_output_spec_parsing():
    assert cli_code._parse_output_spec("doubled:number") == {
        "name": "doubled",
        "code": "number",
    }
    assert cli_code._parse_output_spec("greeting") == {"name": "greeting", "code": "s"}


# --- inputs-file / outputs-file parsing + flag override ------------------


def test_load_inputs_file_typed_and_bare():
    typed = cli_code._load_inputs_file(
        '{"n": {"type": "number", "value": 21}, "who": {"type": "string", "value": "world"}}'
    )
    assert typed["n"] == {"name": "n", "value": 21, "code": "number"}
    assert typed["who"] == {"name": "who", "value": "world", "code": "string"}
    # bare {name: value} form defaults type to s
    bare = cli_code._load_inputs_file('{"n": 21, "who": "world"}')
    assert bare["n"] == {"name": "n", "value": 21, "code": "s"}
    assert bare["who"] == {"name": "who", "value": "world", "code": "s"}


def test_load_outputs_file():
    out = cli_code._load_outputs_file('{"doubled": "number", "greeting": "string"}')
    assert out["doubled"] == {"name": "doubled", "code": "number"}
    assert out["greeting"] == {"name": "greeting", "code": "string"}


@respx.mock
def test_cli_inputs_file_with_flag_override(tmp_path):
    route = respx.post(RUN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "request_id": "r",
                "duration_ms": 1.0,
                "values": {},
                "logs": [],
                "http_requests": {"count": 0, "not_logged": 0, "requests": []},
                "error": None,
            },
        )
    )
    script = tmp_path / "s.py"
    script.write_text("outputs.x=1")
    inputs_json = tmp_path / "inputs.json"
    inputs_json.write_text(
        '{"n": {"type": "number", "value": 5}, "who": {"type": "string", "value": "file"}}'
    )
    outputs_json = tmp_path / "outputs.json"
    outputs_json.write_text('{"x": "number"}')
    result = runner.invoke(
        cli.app,
        [
            "code",
            "test",
            "--yes",
            "--script",
            str(script),
            "--inputs-file",
            str(inputs_json),
            "--outputs-file",
            str(outputs_json),
            # override 'who' from the file; add nothing else
            "--input",
            "who=flagwins:string",
        ],
    )
    assert result.exit_code == 0, result.stdout
    body = json.loads(route.calls[-1].request.content)
    assert body["inputs"]["n"] == {
        "t": "n",
        "v": 5,
    }  # from file (JSON int passed through)
    assert body["inputs"]["who"] == {"t": "s", "v": "flagwins"}  # flag overrode file
    assert body["output_types"] == {"x": "n"}


@respx.mock
def test_cli_unsupported_runtime_errors_before_call(tmp_path):
    route = respx.post(RUN_URL).mock(return_value=httpx.Response(200, json={}))
    script = tmp_path / "s.py"
    script.write_text("outputs.x=1")
    result = runner.invoke(
        cli.app,
        ["code", "test", "--yes", "--script", str(script), "--runtime", "python-2-7"],
    )
    assert result.exit_code == 1
    assert "Unsupported" in result.stderr or "unsupported" in result.stderr
    assert not route.called  # never hit the network


# --- http detail view (--http-detail / -v, failure auto-expand) ----------


def test_fmt_http_body_pretty_prints_json():
    out = cli_code._fmt_http_body('{"a":1,"b":[2,3]}')
    assert '"a": 1' in out  # indented / pretty
    assert "\n" in out


def test_fmt_http_body_raw_when_not_json_and_empty():
    assert cli_code._fmt_http_body("not json at all").plain == "not json at all"
    assert "empty" in cli_code._fmt_http_body("")
    assert "empty" in cli_code._fmt_http_body(None)


def test_fmt_http_body_truncates_large():
    out = cli_code._fmt_http_body("x" * 5000, limit=100)
    assert "truncated" in out
    assert len(out) < 5000


def _http_run(requests):
    return {
        "request_id": "r",
        "duration_ms": 1.0,
        "values": {},
        "logs": [],
        "http_requests": {
            "count": len(requests),
            "not_logged": 0,
            "requests": requests,
        },
        "error": None,
    }


@respx.mock
def test_cli_http_detail_flag_expands_bodies(tmp_path):
    req = {
        "method": "POST",
        "url": "https://app.go.kizen.com/api/custom-objects/x/entity-records",
        "headers": {"X-API-KEY": "[REDACTED]"},
        "body": '{"query": []}',
        "responseStatusCode": 200,
        "responseBody": '{"count": 0}',
        "duration": 5.0,
    }
    respx.post(RUN_URL).mock(return_value=httpx.Response(200, json=_http_run([req])))
    script = tmp_path / "s.py"
    script.write_text("outputs.x=1")
    # Without -v: compact only, no bodies.
    plain = runner.invoke(cli.app, ["code", "test", "--yes", "--script", str(script)])
    assert "request body" not in plain.stdout
    # With -v: request + response bodies shown, key redacted.
    verbose = runner.invoke(
        cli.app, ["code", "test", "--yes", "--script", str(script), "-v"]
    )
    assert verbose.exit_code == 0, verbose.stdout
    assert "request body" in verbose.stdout
    assert "response body" in verbose.stdout
    assert "[REDACTED]" in verbose.stdout


@respx.mock
def test_cli_failed_request_auto_expands_without_flag(tmp_path):
    ok = {
        "method": "GET",
        "url": "https://app.go.kizen.com/api/a",
        "body": "",
        "responseStatusCode": 200,
        "responseBody": "{}",
        "duration": 1.0,
    }
    bad = {
        "method": "POST",
        "url": "https://app.go.kizen.com/api/b",
        "body": '{"bad": true}',
        "responseStatusCode": 500,
        "responseBody": '{"detail": "boom"}',
        "duration": 2.0,
    }
    respx.post(RUN_URL).mock(
        return_value=httpx.Response(200, json=_http_run([ok, bad]))
    )
    script = tmp_path / "s.py"
    script.write_text("outputs.x=1")
    result = runner.invoke(cli.app, ["code", "test", "--yes", "--script", str(script)])
    assert result.exit_code == 0, result.stdout
    # The failed (500) request's body auto-expands; the 200 one does not.
    assert "boom" in result.stdout
    assert "response body" in result.stdout


# --- confirm gate: the script runs in the code_step Lambda and can write ---


def _ok_route():
    return respx.post(RUN_URL).mock(
        return_value=httpx.Response(200, json=_http_run([]))
    )


@respx.mock
def test_cli_without_terminal_or_yes_exits_2_without_posting(tmp_path):
    route = _ok_route()
    script = tmp_path / "s.py"
    script.write_text("outputs.x=1")
    result = runner.invoke(cli.app, ["code", "test", "--script", str(script)])
    assert result.exit_code == 2, result.output
    assert "can't prompt" in result.stderr
    assert not route.called


@respx.mock
def test_cli_declined_prompt_exits_1_without_posting(tmp_path, monkeypatch, terminal):
    route = _ok_route()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "s.py").write_text("outputs.x=1")
    result = runner.invoke(cli.app, ["code", "test", "--script", "s.py"], input="n\n")
    assert result.exit_code == 1, result.output
    assert "Run s.py in testenv on python-3-13?" in plain(result.stdout)
    assert "aborted" in result.stdout
    assert not route.called


@respx.mock
def test_cli_accepted_prompt_posts_once(tmp_path, terminal):
    route = _ok_route()
    script = tmp_path / "s.py"
    script.write_text("outputs.x=1")
    result = runner.invoke(
        cli.app, ["code", "test", "--script", str(script)], input="y\n"
    )
    assert result.exit_code == 0, result.output
    assert route.call_count == 1


@respx.mock
def test_cli_yes_posts_once_and_json_stdout_is_json(tmp_path, terminal):
    route = _ok_route()
    script = tmp_path / "s.py"
    script.write_text("outputs.x=1")
    # No input: a prompt would read EOF and fail the invocation.
    result = runner.invoke(
        cli.app, ["code", "test", "--script", str(script), "--yes", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    json.loads(result.stdout)


@respx.mock
def test_cli_stdin_script_without_yes_exits_2_without_posting(terminal):
    route = _ok_route()
    result = runner.invoke(cli.app, ["code", "test"], input="outputs.x=1\n")
    assert result.exit_code == 2, result.output
    assert not route.called


@respx.mock
def test_cli_stdin_inputs_file_without_yes_exits_2_without_posting(tmp_path, terminal):
    route = _ok_route()
    script = tmp_path / "s.py"
    script.write_text("outputs.x=1")
    result = runner.invoke(
        cli.app,
        ["code", "test", "--script", str(script), "--inputs-file", "-"],
        input='{"n": 1}',
    )
    assert result.exit_code == 2, result.output
    assert not route.called
