"""Offline automation spec checks: every problem in one dry-run."""

from __future__ import annotations

import json

import pytest
import typer
from typer.testing import CliRunner

from kizen_builder.cli._mutations import _run_mutation
from kizen_builder.tools.findings import Finding, SpecProblems

runner = CliRunner()


def _raises(problems: SpecProblems):
    def build():
        raise problems

    return build


_PROBLEMS = SpecProblems(
    [
        Finding(
            "'days' is not one of: minute, hour, day",
            path="action_schedule_activity.schedule.with_delay.delay_type",
            step="act",
            index=0,
            got="days",
            allowed=["minute", "hour", "day"],
            suggestion="day",
            hint="kizen automations step-types show schedule_activity",
        ),
        Finding("required key is missing", path="name"),
    ],
    [Finding("step orders should run 0 to 0", path="steps")],
)


def test_findings_print_one_line_each_with_a_see_line(capsys):
    with pytest.raises(typer.Exit) as exc:
        _run_mutation(_raises(_PROBLEMS), dry_run=True, yes=False, json_out=False)
    assert exc.value.exit_code == 1
    err = capsys.readouterr().err
    assert err.splitlines() == [
        "spec error: step 'act' action_schedule_activity.schedule.with_delay."
        "delay_type: 'days' is not one of: minute, hour, day (did you mean 'day'?)",
        "  see: kizen automations step-types show schedule_activity",
        "spec error: name: required key is missing",
        "warning: steps: step orders should run 0 to 0",
    ]


def test_findings_json_goes_to_stdout(capsys):
    with pytest.raises(typer.Exit) as exc:
        _run_mutation(_raises(_PROBLEMS), dry_run=True, yes=False, json_out=True)
    assert exc.value.exit_code == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    body = json.loads(captured.out)
    assert body["ok"] is False
    assert body["errors"][0] == {
        "message": "'days' is not one of: minute, hour, day",
        "path": "action_schedule_activity.schedule.with_delay.delay_type",
        "step": "act",
        "index": 0,
        "got": "days",
        "allowed": ["minute", "hour", "day"],
        "suggestion": "day",
        "hint": "kizen automations step-types show schedule_activity",
    }
    assert body["errors"][1] == {"message": "required key is missing", "path": "name"}
    assert body["warnings"] == [
        {"message": "step orders should run 0 to 0", "path": "steps"}
    ]


def test_a_finding_without_a_key_names_the_step_by_position():
    assert Finding("bad", path="order", index=2).text() == "step #2 order: bad"
