"""Build the eval world in a Kizen business, and check that it is there.

The world is `evals/world/world.json`: three custom objects, a few fields on
Contacts, two activity types, a form, a survey and an email template. Every
task names things from it. `seed` creates whatever is missing, through the
CLI itself, and is safe to run again. Point it at a disposable business.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from evals import tasks
from evals.runner import Config, HarnessError, _must, kizen

Say = Callable[[str], None]


def _rows(text: str) -> list[dict[str, Any]]:
    """A `--json` listing as rows, whether it is a bare list or wrapped in a dict."""
    data = json.loads(text)
    if isinstance(data, list):
        return data
    return next((v for v in data.values() if isinstance(v, list)), [])


def _listing(cfg: Config, *command: str) -> list[dict[str, Any]]:
    return _rows(_must(kizen(cfg, *command, "--json"), " ".join(command)))


def _names(rows: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for r in rows:
        out |= {str(r[k]) for k in ("api_name", "name", "display_name") if r.get(k)}
    return out


def _get_object(cfg: Config, api_name: str) -> dict[str, Any]:
    return json.loads(
        _must(
            kizen(cfg, "objects", "get", api_name, "--json"), f"objects get {api_name}"
        )
    )


def _apply(
    cfg: Config, dry_run: bool, say: Say, *command: str, spec: Any = None
) -> None:
    say(("would run: " if dry_run else "") + "kizen " + " ".join(command))
    if dry_run:
        return
    stdin = json.dumps(spec) if spec is not None else None
    _must(kizen(cfg, *command, "--yes", stdin=stdin), " ".join(command))


def _fill_tokens(spec: Any, cfg: Config) -> Any:
    """Replace `<object>` and `<object.field>` tokens with this business's UUIDs."""
    text = json.dumps(spec)
    for api in {o["api_name"] for o in tasks.load_world()["objects"]}:
        if f"<{api}" not in text:
            continue
        obj = _get_object(cfg, api)
        text = text.replace(f"<{api}>", obj["id"])
        for f in obj["fields"]:
            text = text.replace(f"<{api}.{f['api_name']}>", f["id"])
    return json.loads(text)


def seed(cfg: Config, dry_run: bool = False, say: Say = print) -> None:
    world = tasks.load_world()
    existing = _names(_listing(cfg, "objects", "list"))
    for spec in world["objects"]:
        api = spec["api_name"]
        missing_object = api not in existing
        if missing_object:
            flags = [
                "--name",
                spec["name"],
                "--entity-name",
                spec["entity_name"],
                "--api-name",
                api,
            ]
            if spec.get("pipeline"):
                flags.append("--pipeline")
            _apply(cfg, dry_run, say, "objects", "create", *flags)
        if missing_object and dry_run:
            say(
                f"would then add {api}'s stages, category and {len(spec['fields'])} fields"
            )
            continue
        if spec.get("pipeline"):
            have = _names(_listing(cfg, "objects", "stages", "list", api))
            for stage in spec["stages"]:
                if stage["name"] not in have:
                    _apply(cfg, dry_run, say, "objects", "stages", "create", api,
                           "--name", stage["name"], "--status", stage["status"],
                           "--pct", str(stage["pct"]))  # fmt: skip
        obj = _get_object(cfg, api)
        category = spec["category"]
        if category["name"] not in _names(obj.get("categories") or []):
            _apply(cfg, dry_run, say, "categories", "create", api,
                   "--api-name", category["api_name"], "--name", category["name"])  # fmt: skip
        have = {f["api_name"] for f in obj["fields"]}
        new = [f for f in spec["fields"] if f["api_name"] not in have]
        if new:
            _apply(cfg, dry_run, say, "fields", "create", api,
                   spec={"category": category["name"], "fields": new})  # fmt: skip

    for kind, command in (
        ("activities", ("activities",)),
        ("forms", ("forms",)),
        ("surveys", ("surveys",)),
        ("email_templates", ("messages", "templates")),
    ):
        have = _names(_listing(cfg, *command, "list"))
        for spec in world[kind]:
            if spec["name"] in have or spec.get("api_name") in have:
                continue
            filled = spec if dry_run else _fill_tokens(spec, cfg)
            _apply(cfg, dry_run, say, *command, "create", spec=filled)
    say("eval world is in place" if not dry_run else "dry run: nothing was changed")


def doctor(cfg: Config) -> dict[str, Any]:
    """What of the eval world is missing, and which optional capabilities exist."""
    world = tasks.load_world()
    missing: list[str] = []
    objects = _names(_listing(cfg, "objects", "list"))
    for spec in world["objects"]:
        api = spec["api_name"]
        if api not in objects:
            missing.append(f"object {api}")
            continue
        obj = _get_object(cfg, api)
        have = {f["api_name"] for f in obj["fields"]}
        missing += [
            f"field {api}.{f['api_name']}"
            for f in spec["fields"]
            if f["api_name"] not in have
        ]
        if spec.get("pipeline"):
            stages = _names(_listing(cfg, "objects", "stages", "list", api))
            missing += [
                f"stage {api}: {s['name']}"
                for s in spec["stages"]
                if s["name"] not in stages
            ]
    for kind, command in (
        ("activities", ("activities",)),
        ("forms", ("forms",)),
        ("surveys", ("surveys",)),
        ("email_templates", ("messages", "templates")),
    ):
        have = _names(_listing(cfg, *command, "list"))
        missing += [
            f"{kind[:-1]} {s['name']}" for s in world[kind] if s["name"] not in have
        ]

    def probe(needle: str, *command: str) -> bool:
        proc = kizen(cfg, *command, "--json")
        return proc.returncode == 0 and needle in proc.stdout

    models = kizen(cfg, "automations", "llm-models", "--json")
    capabilities = {
        "transcription_model": models.returncode == 0
        and any(_supports_transcription(m) for m in _rows(models.stdout)),
        "mcp_readonly": probe('"readonly"', "mcp-servers", "list"),
    }
    return {"missing": missing, "capabilities": capabilities}


def _supports_transcription(model: dict[str, Any]) -> bool:
    return any("transcri" in str(k) and v is True for k, v in _flatten(model))


def _flatten(value: Any, key: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _flatten(v, f"{key}.{k}")]
    return [(key, value)]


def require_world(cfg: Config) -> dict[str, bool]:
    """The capabilities of a seeded business, or an error naming what's missing."""
    report = doctor(cfg)
    if report["missing"]:
        shown = ", ".join(report["missing"][:8])
        raise HarnessError(
            f"the eval world is incomplete ({len(report['missing'])} missing: {shown}). "
            "Run `python -m evals seed` first."
        )
    return report["capabilities"]
