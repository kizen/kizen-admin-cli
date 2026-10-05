"""Flag names mean one thing across the whole CLI.

The tree walk covers every command, so a new command that reuses a retired
name fails here rather than shipping a flag that reads two ways.
"""

from __future__ import annotations

import json

import pytest
import typer
from typer.testing import CliRunner

import kizen_builder.cli as cli
from kizen_builder.cli._shared import OUTPUT_OPTION, warn_renamed_flag
from kizen_builder.tools import coderunner as code_tools
from kizen_builder.tools import permissions as perm_tools
from kizen_builder.tools import smart_connectors as sc_tools
from kizen_builder.tools.planners import activities as act_planners
from kizen_builder.tools.planners import fields as field_planners
from kizen_builder.tools.planners import forms as form_planners
from kizen_builder.tools.planners import permissions as perm_planners
from kizen_builder.tools.plans import Plan
from tests.conftest import iter_commands

# `--live` read as both "write real records" and "use the live script";
# `--force` as both "overwrite local files" and "ignore plan blockers".
RETIRED = {"--live", "--force"}


def _visible_options():
    """Yield (path, param) for every visible option in the command tree."""
    root = typer.main.get_command(cli.app)
    for path, command in iter_commands(root, []):
        for param in command.params:
            if param.param_type_name == "option" and not param.hidden:
                yield path, param


def test_no_visible_option_uses_a_retired_name():
    found = [
        f"`kizen {' '.join(path)}` {name}"
        for path, param in _visible_options()
        for name in [*param.opts, *param.secondary_opts]
        if name in RETIRED
    ]
    assert not found, "retired flag name is visible:\n" + "\n".join(found)


def test_warn_renamed_flag_writes_to_stderr_only(capsys):
    warn_renamed_flag("--force", "--overwrite")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "warning: --force is deprecated; use --overwrite." in captured.err


def test_each_short_flag_has_one_long_name():
    meanings: dict[str, dict[str, list[str]]] = {}
    for path, param in _visible_options():
        names = [*param.opts, *param.secondary_opts]
        long_name = next((n for n in names if n.startswith("--")), None)
        for name in names:
            if not name.startswith("--"):
                where = meanings.setdefault(name, {}).setdefault(str(long_name), [])
                where.append(f"`kizen {' '.join(path)}`")
    clashes = [
        f"{short}: "
        + "; ".join(f"{long} on {', '.join(where)}" for long, where in longs.items())
        for short, longs in sorted(meanings.items())
        if len(longs) > 1
    ]
    assert not clashes, "short flag with two meanings:\n" + "\n".join(clashes)
    assert set(meanings["-p"]) == set(meanings["-e"]) == {"--profile"}


def test_every_output_option_is_the_format():
    wrong = [
        f"`kizen {' '.join(path)}`"
        for path, param in _visible_options()
        if "--output" in param.opts and param.help != OUTPUT_OPTION.help
    ]
    assert not wrong, "--output that isn't the output format:\n" + "\n".join(wrong)


def _stub_renamed_flag_tools(monkeypatch) -> list:
    """Stub every tool the renamed flags reach, recording each call in order."""
    calls: list = []
    plan = Plan(id="p1", env="test", summary="stub plan")
    stubs = {
        (field_planners, "plan_add_field_options"): plan,
        (act_planners, "plan_add_activity_field_options"): plan,
        (form_planners, "plan_add_form_field_options"): plan,
        (perm_planners, "plan_create_permission_group"): plan,
        (perm_tools, "describe_group"): {"id": "g1", "name": "Sales", "blocks": []},
        (code_tools, "run_code_step"): {"raw": {"values": {}}, "error": None},
        (sc_tools, "plan_create_connector"): {"preview": {"name": "Orders"}},
        (sc_tools, "plan_add_seed"): {
            "connector_api_name": "c",
            "custom_object": "orders",
            "filter_group": "Active",
            "fields": None,
            "view": "kizen.orders",
            "replacing": False,
            "regenerate": True,
        },
        (sc_tools, "plan_remove_seed"): {
            "connector_api_name": "c",
            "custom_object": "orders",
            "view": "kizen.orders",
            "payload": [],
        },
        (sc_tools, "build_webhook_sample"): {"path": "s.csv"},
        (sc_tools, "run_connector"): {"output_files": []},
        (sc_tools, "plan_set_status"): {"connector_api_name": "c", "changed": False},
    }
    for (module, name), result in stubs.items():

        def fake(*args, _name=name, _result=result, **kwargs):
            calls.append((_name, args, kwargs))
            return _result

        monkeypatch.setattr(module, name, fake)
    monkeypatch.setattr(perm_tools, "resolve_group", lambda ref: {"id": f"id-{ref}"})
    return calls


_SC = ["smart-connectors"]
_SEEDS_ADD = [*_SC, "seeds", "add", "c", "--dry-run", "--object", "orders"]
_WEBHOOK = [*_SC, "webhook-sample", "s.csv", "--body", "{}"]
_GROUP_CREATE = ["permissions", "group-create", "--name", "N", "--base", "clone"]


@pytest.mark.parametrize(
    ("command", "new", "old", "warning"),
    [
        *(
            ([*group, "options", "add", "x", "f", "--dry-run"], ["--option", "A"],
             ["-o", "A"], "-o is deprecated; use --option.")
            for group in (
                ["fields"],
                ["activities", "fields"],
                ["forms", "fields"],
                ["surveys", "fields"],
            )
        ),
        *(
            (command, ["--object", "orders"], ["-o", "orders"],
             "-o is deprecated; use --object.")
            for command in (
                [*_SC, "create", "Orders", "--dry-run"],
                [*_SC, "seeds", "add", "c", "--dry-run"],
                [*_SC, "seeds", "remove", "c", "--dry-run"],
            )
        ),
        (_SEEDS_ADD, ["--filter-group", "Active"], ["--group", "Active"],
         "--group is deprecated; use --filter-group."),
        (_SEEDS_ADD, ["--filter-group", "Active"], ["-g", "Active"],
         "--group is deprecated; use --filter-group."),
        (_WEBHOOK, ["--employee", "a@x.test"], ["-e", "a@x.test"],
         "-e is deprecated; use --employee."),
        (["code", "test"], ["--declare-output", "x:n"], ["--output", "x:n"],
         "--output is deprecated; use --declare-output."),
        ([*_SC, "run"], ["--skip-sql"], ["--dry-run"],
         "--dry-run is deprecated; use --skip-sql."),
        ([*_GROUP_CREATE, "--dry-run"], ["--source-group", "Admin"],
         ["--from", "Admin"], "--from is deprecated; use --source-group."),
        (["permissions", "group", "Sales"], ["--field-permissions"], ["--fields"],
         "--fields is deprecated; use --field-permissions."),
        (_SC, ["deactivate", "c", "--dry-run"],
         ["activate", "c", "--dry-run", "--status", "inactive"],
         "--status inactive is deprecated; use smart-connectors deactivate."),
        ([*_SC, "activate", "c", "--dry-run"], [], ["--status", "operational"],
         "--status is deprecated; operational is the default."),
    ],
)  # fmt: skip
def test_old_flag_spellings_warn_and_behave_like_the_new_ones(
    monkeypatch, command, new, old, warning
):
    calls = _stub_renamed_flag_tools(monkeypatch)

    current = CliRunner().invoke(
        cli.app, [*command, *new, "--json"], input="outputs.x = 1"
    )
    assert current.exit_code == 0, current.output
    assert "deprecated" not in current.stderr
    expected = list(calls)
    assert expected, "the new spelling reached no tool"
    calls.clear()

    aliased = CliRunner().invoke(
        cli.app, [*command, *old, "--json"], input="outputs.x = 1"
    )
    assert aliased.exit_code == 0, aliased.output
    assert f"warning: {warning}" in " ".join(aliased.stderr.split())
    assert calls == expected
    assert json.loads(aliased.stdout) == json.loads(current.stdout)


@pytest.mark.parametrize(
    ("command", "flag"),
    [
        (["smart-connectors", "create", "Orders"], "--object"),
        (["smart-connectors", "seeds", "add", "c"], "--object"),
        (["smart-connectors", "seeds", "remove", "c"], "--object"),
        (["smart-connectors", "webhook-sample", "s.csv", "--body", "{}"], "--employee"),
    ],
)
def test_a_required_option_that_lost_its_short_form_is_still_required(
    monkeypatch, command, flag
):
    calls = _stub_renamed_flag_tools(monkeypatch)
    result = CliRunner().invoke(cli.app, command)
    assert result.exit_code == 2
    assert f"error: pass {flag}." in result.stderr
    assert not calls
