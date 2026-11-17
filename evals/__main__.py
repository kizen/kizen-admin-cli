"""`python -m evals` — see evals/README.md."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evals import report, runner, seed, tasks
from evals.runner import Config, HarnessError


def _config(args: argparse.Namespace, out: Path | None = None) -> Config:
    cfg = Config(out=out or Path("."), profile=getattr(args, "profile", None))
    if getattr(args, "kizen", None):
        cfg.kizen = shlex.split(args.kizen)
    if getattr(args, "agent_cmd", None):
        cfg.agent_cmd = shlex.split(args.agent_cmd)
    cfg.model = getattr(args, "model", None)
    cfg.timeout = getattr(args, "timeout", cfg.timeout)
    cfg.keep = getattr(args, "keep", False)
    return cfg


def cmd_list(args: argparse.Namespace) -> int:
    for t in tasks.select(tasks.load_tasks(), args.tasks):
        needs = f"  [needs {', '.join(t.requires)}]" if t.requires else ""
        print(f"{t.id:32} tier {t.tier}  {t.surface:14} {t.title}{needs}")
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    all_tasks = tasks.load_tasks()
    for label, attr in (
        ("TRIGGERS", "triggers"),
        ("STEPS", "steps"),
        ("FEATURES", "features"),
    ):
        print(label)
        names = sorted({n for t in all_tasks for n in getattr(t, attr)})
        for name in names:
            ids = [t.id.split("_")[0] for t in all_tasks if name in getattr(t, attr)]
            print(f"  {name:28} {' '.join(ids)}")
    print("NOT COVERED")
    for name, why in tasks.UNCOVERED.items():
        print(f"  {name:28} {why}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    seed.seed(_config(args), dry_run=args.dry_run)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    found = seed.doctor(_config(args))
    for item in found["missing"]:
        print(f"missing: {item}")
    for name, ok in found["capabilities"].items():
        print(f"{name}: {'yes' if ok else 'no'}")
    print(
        "eval world: "
        + ("incomplete, run `python -m evals seed`" if found["missing"] else "ok")
    )
    return 1 if found["missing"] else 0


def _one_task(pattern: str) -> tasks.Task:
    found = tasks.select(tasks.load_tasks(), [pattern])
    if len(found) != 1:
        raise HarnessError(f"{pattern!r} matches {len(found)} tasks; name exactly one")
    return found[0]


def cmd_prepare(args: argparse.Namespace) -> int:
    cfg = _config(args, Path(args.out))
    run_dir = runner.prepare(_one_task(args.task), cfg)
    print(f"run directory: {run_dir}")
    print(f"prompt:        {run_dir / 'prompt.md'}")
    print(
        "\nStart any agent in the working folder with the logging kizen first on PATH:\n"
    )
    print(f"  cd {run_dir / 'work'} && PATH={run_dir / 'bin'}:$PATH <your agent>\n")
    print("Give it the prompt, and when it stops:\n")
    print(f"  python -m evals score {run_dir} --cleanup")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    cfg = _config(args)
    run_dir = Path(args.run_dir).resolve()
    cfg.profile = cfg.profile or json.loads((run_dir / "run.json").read_text()).get(
        "profile"
    )
    result = runner.score(run_dir, cfg)
    print(json.dumps(result, indent=2))
    if args.cleanup:
        print("deleted: " + ", ".join(runner.cleanup(run_dir, cfg)))
    return 0 if result["passed"] else 1


def cmd_run(args: argparse.Namespace) -> int:
    out = Path(args.out or f"evals/results/{time.strftime('%Y%m%d-%H%M%S')}")
    cfg = _config(args, out)
    selected = tasks.select(tasks.load_tasks(), args.tasks)
    if not selected:
        raise HarnessError("no task matches")
    if not args.no_preflight:
        capabilities = seed.require_world(cfg)
        for t in list(selected):
            lacking = [r for r in t.requires if not capabilities.get(r)]
            if lacking:
                print(f"skipping {t.id}: this business lacks {', '.join(lacking)}")
                selected.remove(t)
    jobs = [(t, trial) for t in selected for trial in range(1, args.trials + 1)]
    print(f"{len(jobs)} run(s) → {out}")

    def one(job: tuple[tasks.Task, int]) -> dict:
        task, trial = job
        result = runner.run_one(task, cfg, trial)
        mark = "PASS" if result.get("passed") else "FAIL"
        print(
            f"{mark} {task.id} #{trial}: {result.get('cli_calls', '-')} cli calls, "
            f"{result.get('cli_failed', '-')} failed, {result.get('invalid_commands', '-')} invalid, "
            f"{result.get('tool_calls', '-')} tool calls"
        )
        return result

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(one, jobs))
    return _write_report(out, results)


def _write_report(out: Path, results: list[dict]) -> int:
    summary = report.summarize(results)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    text = report.render(summary)
    failed = report.failures(results)
    if failed:
        text += "\nFailed runs:\n\n" + failed + "\n"
    (out / "report.md").write_text(text)
    print("\n" + text)
    return 0 if all(r.get("passed") for r in results) else 1


def cmd_report(args: argparse.Namespace) -> int:
    out = Path(args.out)
    results = report.load_results(out)
    if not results:
        raise HarnessError(f"no result.json under {out}")
    _write_report(out, results)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    summaries = []
    for d in (args.before, args.after):
        results = report.load_results(Path(d))
        if not results:
            raise HarnessError(f"no result.json under {d}")
        summaries.append(report.summarize(results))
    print(report.compare(*summaries))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(
        name: str, func, help: str, profile: bool = True
    ) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help)
        p.set_defaults(func=func)
        if profile:
            p.add_argument(
                "--profile", help="Kizen profile of the disposable eval business."
            )
            p.add_argument(
                "--kizen",
                help="The kizen command to evaluate (default: this checkout's).",
            )
        return p

    p = add("list", cmd_list, "List the tasks.", profile=False)
    p.add_argument("tasks", nargs="*")
    add(
        "coverage",
        cmd_coverage,
        "Which task covers each trigger, step and feature.",
        profile=False,
    )
    p = add("seed", cmd_seed, "Create whatever of the eval world is missing.")
    p.add_argument("--dry-run", action="store_true")
    add("doctor", cmd_doctor, "Check the eval world and the optional capabilities.")
    p = add("prepare", cmd_prepare, "Set up one run to drive by hand.")
    p.add_argument("task")
    p.add_argument(
        "--out", default=f"evals/results/manual-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    p = add("score", cmd_score, "Score a prepared run after its session ends.")
    p.add_argument("run_dir")
    p.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete the run's automations afterwards.",
    )
    p = add("run", cmd_run, "Run tasks through a fresh agent session each, and report.")
    p.add_argument("tasks", nargs="*", help="Task id prefixes or tierN. Default: all.")
    p.add_argument(
        "--out", help="Results directory (default: evals/results/<timestamp>)."
    )
    p.add_argument("--trials", type=int, default=1, help="Sessions per task.")
    p.add_argument("--jobs", type=int, default=1, help="Sessions to run at once.")
    p.add_argument("--model", help="Passed to the agent as --model.")
    p.add_argument(
        "--agent-cmd", help="Replace the agent command. It gets the prompt on stdin."
    )
    p.add_argument("--timeout", type=int, default=1800, help="Seconds per session.")
    p.add_argument(
        "--keep", action="store_true", help="Don't delete the run's automations."
    )
    p.add_argument(
        "--no-preflight", action="store_true", help="Skip the eval-world check."
    )
    p = add(
        "report",
        cmd_report,
        "Rebuild report.md from a results directory.",
        profile=False,
    )
    p.add_argument("out")
    p = add("compare", cmd_compare, "Compare two results directories.", profile=False)
    p.add_argument("before")
    p.add_argument("after")

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except HarnessError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
