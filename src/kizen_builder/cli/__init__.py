"""Typer CLI — thin shim over `kizen_builder.tools`.

Environment selection is positional: the working directory is pinned to one
profile by a `.kizen/profile` file, and credentials for that profile come
from the central store (`~/.config/kizen/credentials.toml`). No env label
argument is needed on any command. See `kizen docs show operating`.

One module per Kizen surface. Each module registers its own commands and
sub-apps on import, so this package's job is to import them all — and to do it
in the right order.

**The order below is load-bearing.** Typer renders `--help` in registration
order, so the sequence of these imports *is* the order groups appear in
`kizen --help`, and the order commands appear under each group. It is curated
(docs/envs first, related surfaces adjacent), not alphabetical — which is why
the block is fenced off from isort. Reordering it silently reshuffles the help
output for every user.

`scripts/dump_cli_tree.py` captures the whole tree plus every `--help` so a
change here can be diffed rather than eyeballed.
"""

from __future__ import annotations

from kizen_builder.cli._shared import app, console, err_console

# isort: off
from kizen_builder.cli import _mutations  # noqa: F401
from kizen_builder.cli import docs  # noqa: F401
from kizen_builder.cli import envs  # noqa: F401
from kizen_builder.cli import objects  # noqa: F401
from kizen_builder.cli import stages  # noqa: F401
from kizen_builder.cli import dashboards  # noqa: F401
from kizen_builder.cli import layouts  # noqa: F401
from kizen_builder.cli import records  # noqa: F401
from kizen_builder.cli import records_write  # noqa: F401
from kizen_builder.cli import filters  # noqa: F401
from kizen_builder.cli import team  # noqa: F401
from kizen_builder.cli import permissions  # noqa: F401
from kizen_builder.cli import automations  # noqa: F401
from kizen_builder.cli import steps  # noqa: F401
from kizen_builder.cli import messages  # noqa: F401
from kizen_builder.cli import runs  # noqa: F401
from kizen_builder.cli import fields  # noqa: F401
from kizen_builder.cli import categories  # noqa: F401
from kizen_builder.cli import filter_groups  # noqa: F401
from kizen_builder.cli import quick_filters  # noqa: F401
from kizen_builder.cli import columns  # noqa: F401
from kizen_builder.cli import activities  # noqa: F401
from kizen_builder.cli import activities_fields  # noqa: F401
from kizen_builder.cli import activities_instances  # noqa: F401
from kizen_builder.cli import forms  # noqa: F401
from kizen_builder.cli import automations_write  # noqa: F401
from kizen_builder.cli import folders  # noqa: F401
from kizen_builder.cli import apply  # noqa: F401
from kizen_builder.cli import smart_connectors  # noqa: F401
from kizen_builder.cli import smart_connectors_seeds  # noqa: F401
from kizen_builder.cli import smart_connectors_run  # noqa: F401
from kizen_builder.cli import smart_connectors_reads  # noqa: F401
from kizen_builder.cli import smart_connectors_executions  # noqa: F401
from kizen_builder.cli import smart_connectors_dev  # noqa: F401
from kizen_builder.cli import upgrade  # noqa: F401
from kizen_builder.cli import init  # noqa: F401
from kizen_builder.cli import code  # noqa: F401

# isort: on

__all__ = ["app", "console", "err_console"]
