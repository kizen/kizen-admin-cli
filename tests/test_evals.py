"""The eval suite is coherent offline: coverage, solutions, checks, metrics.

Nothing here reaches a network or an agent. The eval world is faked from
`evals/world/world.json` so every reference solution runs through the real
planner, which is the proof that each task can be built with this CLI.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest

import kizen_builder.tools.planners.automations as ma
from evals import graph, tasks
from kizen_builder import filtering
from kizen_builder.models.spec import AutomationDef
from kizen_builder.tools.planners.automations import (
    _STEP_BUILDERS,
    _TRIGGER_BUILDERS,
    LiveContext,
    _build_automation_payload,
)
from tests.conftest import FIXTURES, load_fixture

PREFIX = "evtest1"
TASKS = tasks.load_tasks()
#: Step types the planner wires without a builder (they take no config block).
CONFIGLESS_STEPS = {"branch", "merge_branches", "next_or_finish"}


def _uuid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"evals.{name}"))


def _fake_objects() -> dict[str, dict[str, Any]]:
    """The eval world as `objects get` would return it, with stable fake UUIDs."""
    world = tasks.load_world()
    contacts = json.loads((FIXTURES / "objects" / "client_client.json").read_text())
    objects: dict[str, dict[str, Any]] = {}
    for spec in world["objects"]:
        api = spec["api_name"]
        fields = list(contacts["fields"]) if spec.get("builtin") else []
        if spec.get("pipeline"):
            stages = [
                {"id": _uuid(f"{api}.stage.{s['name']}"), "name": s["name"]}
                for s in spec["stages"]
            ]
            fields.append(
                {
                    "id": _uuid(f"{api}.stage"),
                    "api_name": "stage",
                    "display_name": "Stage",
                    "field_type": "dropdown",
                    "is_default": True,
                    "options": [{**s, "code": s["name"]} for s in stages],
                }
            )
        else:
            stages = None
        for f in spec["fields"]:
            relation = f.get("relation")
            fields.append(
                {
                    "id": _uuid(f"{api}.{f['api_name']}"),
                    "api_name": f["api_name"],
                    "display_name": f["name"],
                    "field_type": f["field_type"],
                    "deleted": False,
                    "relation": relation
                    and {
                        "related_object": _object_id(
                            relation["target_object"], contacts
                        ),
                        "cardinality": relation["relation_type"],
                    },
                    "options": [
                        {
                            "id": _uuid(f"{api}.{f['api_name']}.{o}"),
                            "name": o,
                            "code": o,
                        }
                        for o in f.get("options", [])
                    ]
                    or None,
                }
            )
        objects[api] = {
            "id": _object_id(api, contacts),
            "api_name": api,
            "object_type": "pipeline" if spec.get("pipeline") else "standard",
            "fields": fields,
            "raw": None,
            **({"stages": stages} if stages else {}),
        }
    return objects


def _object_id(api_name: str, contacts: dict[str, Any]) -> str:
    return contacts["id"] if api_name == "client_client" else _uuid(api_name)


OBJECTS = _fake_objects()
OBJECTS_BY_ID = {o["id"]: o for o in OBJECTS.values()}


def _get_object(ref: str) -> dict[str, Any]:
    obj = OBJECTS.get(ref) or OBJECTS_BY_ID.get(ref)
    if obj is None:
        raise LookupError(f"object '{ref}' is not in the eval world")
    return obj


def _fill_tokens(spec: Any) -> Any:
    """Replace `<object>` / `<object.field>` / other tokens with fake UUIDs."""
    text = json.dumps(spec)
    for api, obj in OBJECTS.items():
        text = text.replace(f"<{api}>", obj["id"])
        for f in obj["fields"]:
            text = text.replace(f"<{api}.{f['api_name']}>", f["id"])
            for o in f.get("options") or []:
                text = text.replace(f"<{api}.{f['api_name']}.{o['name']}>", o["id"])
    text = text.replace("<transcription_model>", "kizen/transcribe")
    for token in ("<message_1>", "<message_2>"):
        text = text.replace(token, _uuid(token))
    assert not re.search(r'"<[A-Za-z0-9_. ]+>"', text), "unfilled token"
    return json.loads(text)


@pytest.fixture
def eval_world(monkeypatch):
    world = tasks.load_world()
    names = {
        kind: {x["name"]: _uuid(x["api_name"]) for x in world[kind]}
        | {x["api_name"]: _uuid(x["api_name"]) for x in world[kind]}
        for kind in ("activities", "forms", "surveys")
    }

    def lookup(kind: str):
        def resolve(self, identifier: str) -> str:
            try:
                return names[kind][identifier]
            except KeyError:
                raise LookupError(f"no {kind} named {identifier!r}") from None

        return resolve

    monkeypatch.setattr(ma, "get_object", _get_object)
    monkeypatch.setattr(
        LiveContext,
        "_fields_with_options",
        lambda self, ref: _get_object(ref)["fields"],
    )
    monkeypatch.setattr(LiveContext, "activity_type_uuid", lookup("activities"))
    monkeypatch.setattr(LiveContext, "form_uuid", lookup("forms"))
    monkeypatch.setattr(LiveContext, "survey_uuid", lookup("surveys"))
    monkeypatch.setattr(LiveContext, "mcp_server_uuid", lambda self, n: _uuid(n))
    monkeypatch.setattr(LiveContext, "service_account_uuid", lambda self, n: _uuid(n))
    setups = [
        {"id": _uuid(s["spec"]), "active": True, **tasks.load_setup(s["spec"], PREFIX)}
        for t in TASKS
        for s in t.setup
    ]
    monkeypatch.setattr(ma, "list_automations", lambda: setups)
    from kizen_builder.api import automations as auto_api

    llm = load_fixture("automations/llm_metadata.json")
    monkeypatch.setattr(auto_api, "get_metadata", lambda client: llm)

    class _Schema:
        def custom_object(self, api_name):
            return _get_object(api_name)

        def get_field(self, obj_id, name):
            for f in _get_object(obj_id)["fields"]:
                if name in (f["api_name"], f["id"]):
                    return {
                        "name": f["api_name"],
                        "id": f["id"],
                        "field_type": f["field_type"],
                        "is_default": bool(f.get("is_default")),
                        "options": f.get("options") or [],
                    }
            return None

    filtering.set_default_client(_Schema())
    yield
    filtering.set_default_client(None)


# --- coverage ---------------------------------------------------------------


def test_every_wired_trigger_is_covered():
    covered = {t for task in TASKS for t in task.triggers}
    assert covered == set(_TRIGGER_BUILDERS)


def test_every_wired_step_is_covered_or_exempt():
    wired = set(_STEP_BUILDERS) | CONFIGLESS_STEPS
    covered = {s for task in TASKS for s in task.steps}
    assert covered - wired == set(), "a task names a step type the CLI doesn't wire"
    assert wired - covered == set(tasks.UNCOVERED)


def test_every_surface_and_feature_has_a_task():
    assert {t.surface for t in TASKS} == {
        "contacts",
        "pipeline",
        "custom_object",
        "global",
    }
    features = {f for t in TASKS for f in t.features}
    assert {"loop", "branching", "merge", "code", "ai", "step_edit"} <= features


# --- tasks and their solutions ----------------------------------------------


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_task_is_well_formed(task):
    assert tasks.PREFIX_TOKEN in task.prompt
    assert all((tasks.ASSETS_DIR / f).is_file() for f in task.files)
    solutions = tasks.load_solution(task, PREFIX)
    assert [s["api_name"] for s in solutions] == [
        tasks.fill(e["api_name"], PREFIX) for e in task.expect
    ]
    # What a task says it covers is what its solution holds, no more or less.
    g = [graph.from_spec(s) for s in solutions]
    assert {s.type for x in g for s in x.steps} == set(task.steps)
    assert set(task.triggers) <= {t for x in g for t, _ in x.triggers}


def test_a_solution_runs_the_script_its_task_hands_over():
    for task in TASKS:
        if not task.files:
            continue
        (spec,) = tasks.load_solution(task, PREFIX)
        (step,) = [s for s in spec["steps"] if s["step_type"] == "code_step"]
        (name,) = task.files
        assert (
            step["action_code_step"]["script"]
            == (tasks.ASSETS_DIR / name).read_text().rstrip()
        )


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_solution_passes_its_own_checks(task):
    for spec, expect in zip(
        tasks.load_solution(task, PREFIX), task.expect, strict=True
    ):
        assert graph.run_checks(graph.from_spec(spec), tasks.fill(expect, PREFIX)) == []


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_solution_plans_against_the_eval_world(eval_world, task):
    specs = [tasks.load_setup(s["spec"], PREFIX) for s in task.setup]
    specs += tasks.load_solution(task, PREFIX)
    for spec in specs:
        spec = _fill_tokens(spec)
        payload = _build_automation_payload(
            AutomationDef.model_validate(spec), LiveContext()
        )
        assert [s["key"] for s in payload["steps"]] == [s["key"] for s in spec["steps"]]


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_an_empty_automation_fails_the_checks(task):
    for expect in task.expect:
        expect = tasks.fill(expect, PREFIX)
        if not expect.get("steps"):
            continue
        empty = graph.from_spec({"api_name": expect["api_name"], "type": "global"})
        assert graph.run_checks(empty, expect)


# --- the eval world ---------------------------------------------------------


def test_world_specs_validate():
    from kizen_builder.models.spec.activities import ActivityDef
    from kizen_builder.models.spec.email_templates import EmailTemplateDef
    from kizen_builder.models.spec.forms import FormDef
    from kizen_builder.models.spec.objects import FieldDef

    world = tasks.load_world()
    for obj in world["objects"]:
        for f in obj["fields"]:
            FieldDef.model_validate(f)
    for activity in _fill_tokens(world["activities"]):
        ActivityDef.model_validate(activity)
    for form in world["forms"] + world["surveys"]:
        FormDef.model_validate(form)
    for template in world["email_templates"]:
        EmailTemplateDef.model_validate(template)


def test_tasks_only_name_things_in_the_world():
    """A solution's `object.field` refs all resolve in world.json."""
    for task in TASKS:
        text = json.dumps(tasks.load_solution(task, PREFIX))
        for obj, fld in re.findall(r'"<?([a-z_]+)\.([a-z_]+)>?"', text):
            if obj in OBJECTS:
                assert fld in {f["api_name"] for f in OBJECTS[obj]["fields"]}, (
                    task.id,
                    fld,
                )


# --- reading a live automation ----------------------------------------------


def test_from_raw_reads_a_live_capture():
    g = graph.from_raw(load_fixture("automations/loops.raw.json"))
    assert (g.type, g.target_object, g.active) == (
        "record_based",
        "object_with_workflow",
        False,
    )
    expect = {
        "steps": {"loop": 2, "next_or_finish": 2, "condition": 1, "merge_branches": 1},
        "checks": [
            {"kind": "merged", "of": "condition"},
            {"kind": "block", "step": "loop", "match": {"array_variable": "arr0"}},
            {"kind": "branch", "of": "condition", "yes": ["delay"]},
            {"kind": "loop_body", "has": ["condition", "delay"]},
            {"kind": "order", "first": "next_or_finish", "then": "loop"},
        ],
    }
    assert graph.run_checks(g, expect) == []
    assert graph.run_checks(
        g, {"steps": {"code_step": 1}, "triggers": ["webhook"]}
    ) == [
        "missing trigger webhook",
        "code_step: 0 step(s), expected 1+",
    ]


def test_from_raw_follows_a_go_to():
    g = graph.from_raw(
        load_fixture("automations/update_variable_goto_branching.raw.json")
    )
    targets = {g.by_id(s.goto).type for s in g.of_type("go_to_automation_step")}
    assert targets
    assert (
        graph.run_checks(g, {"checks": [{"kind": "goto", "target": targets.pop()}]})
        == []
    )


def test_kept_ids_notices_a_recreated_step():
    before = graph.from_raw(load_fixture("automations/loops.raw.json"))
    raw = load_fixture("automations/loops.raw.json")
    raw["steps"][-1]["id"] = "00000000-0000-4000-8000-00000000beef"
    after = graph.from_raw(raw)
    check = {"checks": [{"kind": "kept_ids"}]}
    assert graph.run_checks(before, check, before) == []
    assert graph.run_checks(after, check, before) == [
        "1 pre-existing step(s) lost their id"
    ]


@pytest.mark.parametrize(
    ("expected", "actual", "ok"),
    [
        ("readonly", {"id": "x", "api_name": "readonly"}, True),
        ("readonly", {"id": "x", "api_name": "other"}, False),
        (
            {"schedule": {"type": "with_delay"}},
            {"schedule": {"type": "with_delay", "x": 1}},
            True,
        ),
        ([{"name": "a"}], [{"name": "b"}, {"name": "a", "id": 1}], True),
        ([{"name": "a"}], [{"name": "b"}], False),
        ("*", "", False),
        ("*", [1], True),
        (1, True, False),
        (3, 3.0, True),
    ],
)
def test_loosely_matches(expected, actual, ok):
    assert graph.loosely_matches(expected, actual) is ok


# --- metrics ----------------------------------------------------------------


def _call(
    argv: str, exit_code: int = 0, stderr: str = "", out: int = 10
) -> dict[str, Any]:
    return {"argv": argv.split(), "exit_code": exit_code, "stderr_tail": stderr,
            "stdout_tail": "", "stdout_bytes": out, "stderr_bytes": len(stderr),
            "duration_ms": 100}  # fmt: skip


def test_cli_metrics_classify_calls():
    from evals import metrics

    calls = [
        _call("docs show operating", out=9000),
        _call("automations --help"),
        _call("automations make --spec-file a.json", 2, "No such command 'make'."),
        _call("objects get eval_deals --json"),
        _call(
            "automations create --spec-file a.json --dry-run",
            1,
            "spec error: steps.0: bad",
        ),
        _call(
            "automations create --spec-file a.json --dry-run", 1, "plan error: no field"
        ),
        _call("automations create --spec-file a.json --dry-run"),
        _call(
            "automations create --spec-file a.json --yes", 1, "error: HTTP 400: nope"
        ),
        _call("automations create --spec-file a.json --yes"),
        _call(
            "automations get evx_a --raw",
            1,
            "error: no automation with api_name 'evx_a'",
        ),
    ]
    m = metrics.cli_metrics(calls)
    assert m["cli_calls"] == 10
    assert m["cli_failed"] == 5
    assert (m["invalid_commands"], m["spec_errors"], m["plan_errors"]) == (1, 1, 1)
    assert (m["api_errors"], m["other_errors"]) == (1, 1)
    assert (m["help_calls"], m["docs_calls"], m["docs_bytes"]) == (1, 1, 9000)
    assert (m["dry_runs"], m["applies"], m["failed_applies"]) == (3, 2, 1)
    assert m["read_calls"] == 3
    assert m["calls_before_first_apply"] == 8
    assert m["docs_topics"] == ["show operating"]


@pytest.mark.parametrize(
    ("argv", "kind"),
    [
        ("", "help"),
        ("automations steps add evx_a --help", "help"),
        ("docs list", "docs"),
        ("upgrade --check", "read"),
        ("automations show evx_a --json", "read"),
        ("automations steps add evx_a --parent s00_delay --dry-run", "dry_run"),
        ("automations steps remove evx_a s00_delay --yes", "apply"),
        ("automations activate evx_a --yes", "apply"),
        ("messages create evx_a --template Notice --yes", "apply"),
        ("messages templates list", "read"),
    ],
)
def test_kind_of(argv, kind):
    from evals import metrics

    assert metrics.kind_of(argv.split()) == kind


# --- the harness, end to end, against stand-ins ------------------------------

_FAKE_KIZEN = """\
import json, pathlib, sys
store = pathlib.Path({store!r})
db = json.loads(store.read_text()) if store.exists() else {{}}
args = [a for a in sys.argv[1:] if a not in ("--yes", "--json", "--raw", "--dry-run")]
dry = "--dry-run" in sys.argv
def save(): store.write_text(json.dumps(db))
def raw(spec):
    steps = [{{"id": s["key"], "step_type": s["step_type"], "parent_step_id": s.get("parent_key"),
              "parent_condition": s.get("parent_branch") or "",
              **{{k: v for k, v in s.items() if k.startswith(("step_", "action_")) and k != "step_type"}}}}
             for s in spec.get("steps", [])]
    for s in steps:  # a read gives a go-to's target as a step, not a key
        if s["step_type"] == "go_to_automation_step":
            s["action_go_to_automation_step"] = {{"step": {{"id": s["action_go_to_automation_step"]["step_key"]}}}}
    return {{"id": spec["api_name"], "api_name": spec["api_name"], "type": spec.get("type", "record_based"),
            "custom_object": {{"name": spec.get("target_object")}}, "active": bool(spec.get("active")),
            "triggers": spec.get("triggers", []), "steps": steps}}
if args == ["--version"]:
    print("kizen 0.0-fake")
elif args[:2] in (["automations", "create"], ["automations", "update"]):
    spec = json.load(sys.stdin)
    if not dry:
        was = db.get(spec["api_name"], {{}})
        db[spec["api_name"]] = {{**raw(spec), "active": was.get("active", False)}}; save()
elif args[:2] == ["objects", "get"]:
    print(json.dumps(json.loads(pathlib.Path({objects!r}).read_text())[args[2]]))
elif args[:2] == ["automations", "llm-models"]:
    print(json.dumps([{{"model_name": "kizen/transcribe", "usage": {{"audio": {{"transcription": True}}}}}}]))
elif args[:2] == ["messages", "create"]:
    n = len(db.setdefault("_messages", [])); db["_messages"].append(args[2]); save()
    print(json.dumps({{"results": [{{"server_uuid": f"00000000-0000-4000-8000-{{n:012d}}"}}]}}))
elif args[:2] == ["automations", "get"]:
    if args[2] not in db:
        sys.exit(f"error: no automation with api_name '{{args[2]}}'")
    print(json.dumps(db[args[2]]))
elif args[:2] == ["automations", "list"]:
    print(json.dumps([v for k, v in db.items() if k != "_messages"]))
elif args[:2] == ["automations", "delete"]:
    db.pop(args[2], None); save()
elif args[:2] == ["automations", "activate"]:
    db[args[2]]["active"] = True; save()
elif args[:2] == ["automations", "deactivate"]:
    db[args[2]]["active"] = False; save()
elif args[:1] == ["docs"]:
    print("x" * 500)
else:
    print("No such command", file=sys.stderr); sys.exit(2)
"""

_FAKE_AGENT = """\
import json, pathlib, subprocess, sys
prompt = sys.stdin.read()
spec = json.loads(pathlib.Path({solution!r}).read_text())[0]
def tool(*argv, stdin=None):
    print(json.dumps({{"type": "assistant", "message": {{"content": [
        {{"type": "tool_use", "name": "Bash", "input": {{"command": " ".join(argv)}}}}]}}}}))
    subprocess.run(list(argv), input=stdin, text=True, capture_output=True)
tool("kizen", "docs", "show", "automation")
tool("kizen", "automations", "build")
tool("kizen", "automations", "create", "--dry-run", stdin=json.dumps(spec))
if {succeed!r}:
    tool("kizen", "automations", "create", "--yes", stdin=json.dumps(spec))
print(json.dumps({{"type": "result", "num_turns": 5, "total_cost_usd": 0.25, "duration_ms": 4000,
                  "is_error": False, "usage": {{"input_tokens": 100, "output_tokens": 20}}}}))
"""


@pytest.fixture
def harness(tmp_path):
    """A Config whose `kizen` and agent are scripts, plus a way to script the agent."""
    import sys

    from evals.runner import Config

    fake = tmp_path / "fake_kizen.py"
    objects = tmp_path / "objects.json"
    objects.write_text(json.dumps(OBJECTS))
    fake.write_text(
        _FAKE_KIZEN.format(store=str(tmp_path / "store.json"), objects=str(objects))
    )
    cfg = Config(
        out=tmp_path / "out", profile="evalprofile", kizen=[sys.executable, str(fake)]
    )

    def agent(task: tasks.Task, prefix_file: Any, succeed: bool = True) -> None:
        script = tmp_path / "agent.py"
        script.write_text(
            _FAKE_AGENT.format(solution=str(prefix_file), succeed=succeed)
        )
        cfg.agent_cmd = [sys.executable, str(script)]

    return cfg, agent, tmp_path


def _solution_file(tmp_path, task: tasks.Task, run_dir) -> Any:
    prefix = json.loads((run_dir / "run.json").read_text())["prefix"]
    path = tmp_path / "solution.json"
    path.write_text(json.dumps(tasks.load_solution(task, prefix)))
    return path


@pytest.mark.parametrize("succeed", [True, False])
def test_a_run_is_prepared_driven_scored_and_cleaned_up(harness, succeed):
    from evals import report, runner

    cfg, agent, tmp_path = harness
    task = next(t for t in TASKS if t.id == "t07_closing_soon_gate")
    run_dir = runner.prepare(task, cfg)
    assert (run_dir / "work" / "CLAUDE.md").is_file()
    prompt = (run_dir / "prompt.md").read_text()
    assert tasks.PREFIX_TOKEN not in prompt and "## Task" in prompt

    agent(task, _solution_file(tmp_path, task, run_dir), succeed)
    runner.launch(run_dir, cfg)
    result = runner.score(run_dir, cfg)

    assert result["passed"] is succeed
    assert bool(result["problems"]) is not succeed
    assert result["cli_calls"] == (4 if succeed else 3)
    assert result["invalid_commands"] == 1
    assert (result["docs_calls"], result["dry_runs"]) == (1, 1)
    assert result["applies"] == (1 if succeed else 0)
    assert (result["tool_calls"], result["turns"], result["cost_usd"]) == (
        4 if succeed else 3,
        5,
        0.25,
    )
    assert runner.cleanup(run_dir, cfg) == (
        [f"{result['prefix']}_closing_soon"] if succeed else []
    )

    summary = report.summarize(report.load_results(cfg.out))
    assert summary["tasks"][task.id]["passed"] == int(succeed)
    assert "t07_closing_soon_gate" in report.render(summary)
    assert "→" in report.compare(summary, summary)


def test_setup_automations_exist_before_the_session_and_go_afterwards(harness):
    from evals import runner

    cfg, agent, tmp_path = harness
    task = next(t for t in TASKS if t.id == "t11_weekly_account_sweep")
    run_dir = runner.prepare(task, cfg)
    prefix = json.loads((run_dir / "run.json").read_text())["prefix"]
    before = runner.read_raw(cfg, f"{prefix}_account_review")
    assert before is not None and before["active"] is True
    agent(task, _solution_file(tmp_path, task, run_dir))
    runner.launch(run_dir, cfg)
    assert runner.score(run_dir, cfg)["passed"] is True
    assert sorted(runner.cleanup(run_dir, cfg)) == [
        f"{prefix}_account_review",
        f"{prefix}_weekly_sweep",
    ]
    assert runner.read_raw(cfg, f"{prefix}_weekly_sweep") is None


def test_run_one_reports_a_harness_failure_as_a_failed_run(harness):
    from evals import runner

    cfg, _, _ = harness
    cfg.agent_cmd = ["/nonexistent/agent"]
    result = runner.run_one(TASKS[0], cfg)
    assert result["passed"] is False
    assert "agent command not found" in result["harness_error"]


# --- rubrics and the judge ---------------------------------------------------


def test_every_task_has_a_rubric_of_distinct_checkable_criteria():
    shared = tasks.shared_rubric()
    assert shared["outcome"] and shared["process"]
    shared_ids = {c["id"] for group in shared.values() for c in group}
    for task in TASKS:
        ids = [c["id"] for c in task.rubric]
        assert len(ids) >= 2, task.id
        assert len(set(ids)) == len(ids) and not set(ids) & shared_ids, task.id
        for c in task.rubric:
            assert c["text"].endswith((".", '."')), (task.id, c["id"])
            assert "{{" not in c["text"], (
                "a criterion describes a merge field, never spells one"
            )


def test_prompts_never_spell_a_merge_field():
    for task in TASKS:
        assert "{{" not in task.prompt, task.id


def _legend() -> dict[str, str]:
    from evals import judge

    return judge.legend_from_objects(list(OBJECTS.values()))


def test_legend_names_fields_options_and_stages():
    legend = _legend()
    deals = OBJECTS["eval_deals"]
    assert legend[deals["id"]] == "object eval_deals"
    assert (
        legend[_uuid("eval_deals.stage.Closed Won")] == "eval_deals stage 'Closed Won'"
    )
    assert legend[_uuid("eval_deals.risk")] == "field eval_deals.risk (dropdown)"
    assert legend[_uuid("eval_deals.risk.High")] == "option 'High' of eval_deals.risk"


def test_outcome_prompt_holds_the_end_state_and_not_the_answer_key():
    from evals import judge

    task = next(t for t in TASKS if t.id == "t12_ticket_find_contact")
    raw = {
        "api_name": f"{PREFIX}_find_contact",
        "steps": [{"id": "a", "stats": {"n": 1}}],
    }
    prompt = judge.outcome_prompt(
        task, PREFIX, {raw["api_name"]: raw, "gone": None}, _legend()
    )
    for c in [*tasks.shared_rubric()["outcome"], *task.rubric]:
        assert f"`{c['id']}`" in prompt
    assert "create_contact.py" in prompt and "kizen.api.post" in prompt
    assert '"stats"' not in prompt and "not found" in prompt
    assert "object eval_tickets" in prompt
    # Nothing from the reference solution, and nothing the agent said.
    assert "found_contacts_item" not in prompt and "TOOL CALL" not in prompt


def test_process_prompt_holds_the_call_log_and_a_clipped_transcript():
    from evals import judge

    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Reading the docs."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "kizen docs show automation"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": [{"type": "text", "text": "x" * 9000}]}]}},
    ]  # fmt: skip
    calls = [_call("automations make", 2, "No such command 'make'.")]
    prompt = judge.process_prompt(TASKS[0], PREFIX, events, calls)
    assert "AGENT: Reading the docs." in prompt
    assert "TOOL CALL Bash" in prompt and "characters cut" in prompt
    assert '"outcome": "usage"' in prompt and "No such command" in prompt
    assert all(f"`{c['id']}`" in prompt for c in tasks.shared_rubric()["process"])


def _fake_judge(
    verdict: str = "pass", fail: set[str] | None = None, error_on: str = ""
):
    """A judge that gives every criterion in the prompt one verdict."""
    from evals import judge

    seen: list[str] = []

    def call(model, system, user, schema):
        section = "process" if "friction" in schema["properties"] else "outcome"
        seen.append(section)
        if section == error_on:
            raise judge.JudgeError("the API answered 529: overloaded")
        ids = re.findall(r"^- `([a-z_0-9]+)`:", user, re.M)
        answer: dict[str, Any] = {
            "criteria": [
                {
                    "id": i,
                    "verdict": "fail" if i in (fail or ()) else verdict,
                    "evidence": "e",
                }
                for i in ids
            ]
        }
        if section == "process":
            answer["friction"] = [
                {
                    "cause": "docs_hard_to_find",
                    "what_happened": "x",
                    "cli_calls_wasted": 3,
                },
                {"cause": "agent_mistake", "what_happened": "y", "cli_calls_wasted": 2},
            ]
        return answer, {"model": model, "input_tokens": 1000, "output_tokens": 100}

    call.seen = seen  # type: ignore[attr-defined]
    return call


def _scored_run(harness, task_id: str, **judge_kwargs):
    from evals import runner

    cfg, agent, tmp_path = harness
    cfg.judge, cfg.judge_call = True, _fake_judge(**judge_kwargs)
    task = next(t for t in TASKS if t.id == task_id)
    run_dir = runner.prepare(task, cfg)
    agent(task, _solution_file(tmp_path, task, run_dir))
    runner.launch(run_dir, cfg)
    return runner.score(run_dir, cfg), run_dir, cfg


def test_a_judged_run_is_correct_only_when_checks_and_rubric_both_pass(harness):
    result, run_dir, _ = _scored_run(harness, "t07_closing_soon_gate")
    assert (result["passed"], result["judged_correct"], result["correct"]) == (
        True,
        True,
        True,
    )
    assert result["process_clean"] is True
    assert result["friction_calls"] == 3  # the agent's own mistake isn't the tool's
    assert result["judge_tokens"] == 2200
    judged = json.loads((run_dir / "judge.json").read_text())
    assert [v["id"] for v in judged["outcome"]][:2] == [
        "named_as_asked",
        "nothing_extra",
    ]
    assert (run_dir / "judge.outcome.prompt.md").is_file()


def test_a_failed_criterion_makes_a_passing_build_incorrect(harness):
    from evals import report

    result, _, cfg = _scored_run(
        harness, "t07_closing_soon_gate", fail={"condition_amount"}
    )
    assert (result["passed"], result["judged_correct"], result["correct"]) == (
        True,
        False,
        False,
    )
    assert result["outcome_failed"] == ["condition_amount"]
    results = report.load_results(cfg.out)
    summary = report.summarize(results)
    row = summary["tasks"]["t07_closing_soon_gate"]
    assert (row["correct"], row["passed"]) == (0, 1)
    assert "judge: failed condition_amount" in report.failures(results)
    assert summary["friction"]["docs_hard_to_find"] == {
        "times": 1,
        "cli_calls_wasted": 3,
    }
    assert "docs_hard_to_find" in report.render(summary)
    assert report.friction_notes(results).startswith(
        "- t07_closing_soon_gate (docs_hard_to_find"
    )


def test_cannot_tell_is_not_a_pass(harness):
    result, _, _ = _scored_run(harness, "t01_contact_lifecycle", verdict="cannot_tell")
    assert result["judged_correct"] is False
    assert (
        "sets_lifecycle_new" in result["outcome_unclear"]
        and not result["outcome_failed"]
    )


def test_a_judge_failure_is_an_error_not_a_verdict(harness):
    from evals import report

    result, _, cfg = _scored_run(harness, "t01_contact_lifecycle", error_on="outcome")
    assert result["judged_correct"] is None and result["correct"] is None
    assert result["judge_errors"] == ["outcome: the API answered 529: overloaded"]
    assert result["process_clean"] is True  # the other call still counted
    summary = report.summarize(report.load_results(cfg.out))
    assert (
        summary["overall"]["judge_errors"] == 1 and summary["overall"]["correct"] == 0
    )
    assert "The judge failed on 1 run(s)" in report.render(summary)


def test_a_criterion_the_judge_skipped_is_unclear():
    from evals import judge

    criteria = [{"id": "a", "text": "A."}, {"id": "b", "text": "B."}]
    answer = {"criteria": [{"id": "a", "verdict": "pass", "evidence": "x"},
                           {"id": "zzz", "verdict": "pass", "evidence": "x"}]}  # fmt: skip
    assert [v["verdict"] for v in judge._verdicts(answer, criteria)] == [
        "pass",
        "cannot_tell",
    ]


def test_the_judge_schemas_are_closed():
    from evals import judge

    for schema in (judge.OUTCOME_SCHEMA, judge.PROCESS_SCHEMA):
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
    assert judge.PROCESS_SCHEMA["properties"]["friction"]["items"]["properties"][
        "cause"
    ]["enum"] == list(judge.FRICTION_CAUSES)


# --- the oracle --------------------------------------------------------------


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_the_oracle_builds_and_passes_every_reference_solution(harness, task):
    from evals import oracle, runner

    cfg, _, _ = harness
    cfg.judge, cfg.judge_call = True, _fake_judge()
    result = oracle.run_oracle(task, cfg)
    assert result["problems"] == [] and result["correct"] is True
    assert runner.kizen(cfg, "automations", "list", "--json").stdout.strip() == "[]"
