"""Kizen Admin CLI — conversational solution-design tool for Kizen."""

from importlib.metadata import PackageNotFoundError, version

try:
    # pyproject.toml is the single source of truth. This reads what is actually
    # *installed*, which is what `kizen --version` and any upgrade check need to
    # report. Note it's a snapshot taken at install time: bumping the version in
    # pyproject without re-running `uv sync` / `pip install -e .` keeps
    # reporting the old number until the install is refreshed.
    __version__ = version("kizen-builder")
except PackageNotFoundError:  # running from a source tree, not installed
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
