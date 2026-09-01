"""`kizen init` — store credentials centrally and pin this directory to the
profile.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import typer
from rich.prompt import Prompt
from rich.text import Text

from kizen_builder import docs as docs_res
from kizen_builder import profiles
from kizen_builder.api.client import KizenAPIError, KizenClient
from kizen_builder.cli._shared import app, console, err_console
from kizen_builder.config import EnvConfig

# The dotted-halftone Kizen logo mark from Kizen's node CLI (@kizenapps/cli's
# src/ui/Logo.tsx), transcribed verbatim from that package's published source
# map so the two CLIs show the same mark. 22 rows of exactly 43 columns —
# trailing spaces are part of the art, so don't let an editor strip them
# (`ruff format` leaves string-literal contents alone).
_BANNER_ART_LINES: tuple[str, ...] = (
    "                     ::::::::              ",
    "         ::::::    :::::::::::             ",
    "        ::::::   ::::::::::                ",
    "       ::::::   ::::::      :::::::        ",
    "       :::::   ::::::  :::::::::::::::::   ",
    "       :::::  :::::   :::::::::::::::::::  ",
    "       :::::  :::::   ::::::        :::::  ",
    "::::   :::::   :::           :::::     ::  ",
    ":::::  ::::::               ::::::::::     ",
    ":::::   ::::::               :::::::::::   ",
    " :::::    :::                     :::::::  ",
    "  :::::::                     :::    ::::: ",
    "   :::::::::::               ::::::   :::::",
    "     ::::::::::               ::::::  :::::",
    "  ::     :::::           :::   :::::   ::::",
    "  :::::        ::::::   :::::  :::::   ::: ",
    "  :::::::::::::::::::   :::::  :::::       ",
    "   :::::::::::::::::   :::::   :::::       ",
    "       :::::::::     ::::::   ::::::       ",
    "                ::::::::::   ::::::        ",
    "             :::::::::::    ::::::         ",
    "              ::::::::                     ",
)
_BANNER_ART_WIDTH = 43
_BANNER_ART_HEIGHT = 22
# Matches the node CLI's own rendering (ink's <Text color="cyan">) — the
# terminal's named cyan, not a truecolor hex.
_BANNER_ACCENT = "cyan"

# Compact fallback for a terminal too narrow/short for the full art — the
# node CLI has no equivalent; this tier is our own addition.
_COMPACT_BANNER_INNER_WIDTH = 18
_COMPACT_BANNER_LINES = (
    f"╔{'═' * _COMPACT_BANNER_INNER_WIDTH}╗",
    f"║{'KIZEN'.center(_COMPACT_BANNER_INNER_WIDTH)}║",
    f"╚{'═' * _COMPACT_BANNER_INNER_WIDTH}╝",
)
_COMPACT_BANNER_WIDTH = _COMPACT_BANNER_INNER_WIDTH + 2

# Named for this tool rather than reusing the node CLI's tagline ("Kizen App
# Development Toolkit"), which describes that tool.
_BANNER_TAGLINE = "Kizen Admin CLI"


def _init_is_interactive() -> bool:
    """Whether this invocation should show interactive-only chrome (the
    banner).

    A thin wrapper around `console.is_terminal` — the same signal `_ask()`
    relies on via Rich's own prompt handling — kept as its own function so a
    test can monkeypatch this one function instead of mutating the shared
    `console` singleton's private `_force_terminal` attribute. The singleton
    is process-wide, so poking at it directly would leak between tests.
    """
    return console.is_terminal


def _print_banner() -> None:
    """A short banner before the first prompt: the Kizen logo mark above a
    tagline, real terminals only.

    Never runs for piped output or under `CliRunner` (both leave
    `console.is_terminal` `False`), and never appears in `--help` output
    (Typer/Click don't call the command body for `--help`).

    The art is skipped in favor of a compact fallback, or dropped entirely
    for just the tagline, when the real terminal is too small to hold it
    without wrapping into garbage. `console.size` can't answer that:
    `_shared.py` fixes the shared console's width at 220 (so tables render
    consistently at any terminal size), which means `console.size.width`
    always reports 220 here, not the terminal's actual width.
    `shutil.get_terminal_size` is what Rich itself falls back to internally
    for a console with no fixed width, so this reads the same real signal.
    """
    if not _init_is_interactive():
        return

    term_width, term_height = shutil.get_terminal_size(fallback=(80, 24))

    if term_width >= _BANNER_ART_WIDTH and term_height >= _BANNER_ART_HEIGHT + 3:
        # Each row prints as its own plain `Text` — never parsed as Rich
        # markup (a `Text` object bypasses markup parsing entirely, unlike a
        # string) and never reflowed (`no_wrap` + `overflow="crop"`), so
        # Rich can't refold or truncate-with-ellipsis a line on its own.
        for line in _BANNER_ART_LINES:
            console.print(
                Text(line, style=_BANNER_ACCENT, no_wrap=True, overflow="crop")
            )
        console.print()
        console.print(Text(_BANNER_TAGLINE, style=f"bold {_BANNER_ACCENT}"))
        console.print()
    elif term_width >= _COMPACT_BANNER_WIDTH and term_height >= 5:
        for line in _COMPACT_BANNER_LINES:
            console.print(
                Text(
                    line,
                    style=f"bold {_BANNER_ACCENT}",
                    no_wrap=True,
                    overflow="crop",
                )
            )
        console.print()
        console.print(Text(_BANNER_TAGLINE, style=f"bold {_BANNER_ACCENT}"))
        console.print()
    else:
        console.print(Text(_BANNER_TAGLINE, style=f"bold {_BANNER_ACCENT}"))
        console.print()


def _validate_creds(cfg: EnvConfig) -> None:
    """Confirm credentials work with a cheap live read before we store them."""
    with KizenClient(cfg) as client:
        client.get("/api/custom-objects", params={"page_size": 1})


def _default_profile_name(directory: Path) -> str:
    """A sane profile default from the folder name: 'Builder - Acme' -> 'acme'."""
    name = re.sub(r"[^a-z0-9]+", "-", directory.name.lower()).strip("-")
    # Folders are commonly named "Builder - <env>"; the prefix isn't the env.
    name = re.sub(r"^builder-", "", name)
    return name or "default"


def _ask(
    label: str,
    default: str | None,
    *,
    flag: str,
    password: bool = False,
    choices: list[str] | None = None,
) -> str:
    """Ask for a value, falling back to the default when there's no input.

    Handles all three ways `kizen init` gets driven: an interactive terminal,
    piped answers (both read normally), and a fully non-interactive run with
    nothing on stdin — which would otherwise abort on the first prompt even
    when every value was supplied by flag or has a usable default. A missing
    value with no default is a clean usage error naming the flag to pass,
    rather than an EOF traceback.

    `choices`, when given, makes Rich re-prompt on anything else — used for
    the environment picker so a mistyped answer can't silently fall through.
    Matching is case-insensitive: Rich defaults to case-sensitive choices, and
    a business literally named "Go" typing it as typed — capitalized — would
    otherwise loop on the rejection message forever with no clue why.
    `default=None` means there's genuinely nothing to fall back to: Rich's own
    "no default" sentinel is Ellipsis, not `None` or `""`, so the `default`
    kwarg is omitted rather than passed through — passing `None` would make
    Rich hand back `None` on a bare Enter, bypassing `choices` entirely.
    """
    prompt_cls = _EnvironmentPrompt if choices else Prompt
    try:
        if default is None:
            return prompt_cls.ask(
                label, password=password, choices=choices, case_sensitive=False
            )
        return prompt_cls.ask(
            label,
            default=default,
            password=password,
            choices=choices,
            case_sensitive=False,
        )
    except EOFError:
        if default:
            return default
        err_console.print(
            f"[red]error:[/red] {label} is required and there's nothing on stdin. "
            f"Pass {flag} (or set the matching KIZEN_* environment variable)."
        )
        raise typer.Exit(code=2) from None


_FREE_TEXT_CHOICE = "url"


class _EnvironmentPrompt(Prompt):
    """Rich's stock rejection message just says "please select one of the
    available options" — it doesn't say that "url" *is* one of those options
    and picking it opens a second free-text prompt. Someone whose environment
    isn't one of the curated names has no way to guess that from the error
    alone and can end up re-typing their actual host forever."""

    illegal_choice_message = (
        '[prompt.invalid.choice]Not one of the listed names. Type "url" to '
        "enter a custom address instead."
    )


def _ask_base_url(default: str | None, *, flag: str = "--base-url") -> str:
    """Ask which named environment this is, resolving to a full host.

    `url` stays a deliberate choice, not a bare-Enter default — the only
    remaining way to reach a host outside the curated map.
    """
    choices = [*profiles.ENVIRONMENT_HOSTS, _FREE_TEXT_CHOICE]
    default_choice = None
    if default:
        default_choice = next(
            (
                name
                for name, host in profiles.ENVIRONMENT_HOSTS.items()
                if host == default
            ),
            _FREE_TEXT_CHOICE,
        )

    choice = _ask("Environment", default_choice, flag=flag, choices=choices)
    if choice == _FREE_TEXT_CHOICE:
        url_default = default if default_choice == _FREE_TEXT_CHOICE else None
        return _ask("BASE_URL", url_default, flag=flag).rstrip("/")
    return profiles.ENVIRONMENT_HOSTS[choice]


@app.command()
def init(
    profile: str = typer.Option(
        None,
        "--profile",
        "-p",
        "--env",
        "-e",
        help="Profile name for this env (e.g. acme-sandbox). Prompts if omitted.",
    ),
    api_key_opt: str = typer.Option(
        None, "--api-key", envvar="KIZEN_API_KEY", help="API key (else prompts)."
    ),
    business_id_opt: str = typer.Option(
        None,
        "--business-id",
        envvar="KIZEN_BUSINESS_ID",
        help="Business id (else prompts).",
    ),
    user_id_opt: str = typer.Option(
        None, "--user-id", envvar="KIZEN_USER_ID", help="User id (else prompts)."
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help=(
            "Kizen environment: a name (go, fmo, staging, integration) or a "
            "full URL for self-hosted/one-off setups. Prompts if omitted."
        ),
    ),
    no_pin: bool = typer.Option(
        False, "--no-pin", help="Store credentials only; don't pin this directory."
    ),
    skip_validation: bool = typer.Option(
        False, "--skip-validation", help="Don't verify credentials with a live call."
    ),
    refresh_stubs: bool = typer.Option(
        False,
        "--refresh-stubs",
        help=(
            "Overwrite existing CLAUDE.md / AGENTS.md with the current stub. "
            "Discards anything you added to them."
        ),
    ),
) -> None:
    """Set up this directory as a Kizen environment folder.

    Stores credentials centrally (`~/.config/kizen/credentials.toml`, 0600),
    pins this directory to the profile via `.kizen/profile` so every command
    run here targets it — refusing any env with a different business_id — and
    writes the agent-instruction stubs.

    Every value can be supplied as a flag or a `KIZEN_*` environment variable;
    anything still missing is prompted for, so this works interactively and
    headlessly from the same command.
    """
    _print_banner()
    cwd = Path.cwd()
    if not profile:
        profile = _ask("Profile name", _default_profile_name(cwd), flag="--profile")

    existing = profiles.get_profile(profile)

    api_key = api_key_opt or _ask(
        "API_KEY",
        existing.api_key if existing else "",
        flag="--api-key",
        password=True,
    )
    business_id = business_id_opt or _ask(
        "BUSINESS_ID",
        existing.business_id if existing else "",
        flag="--business-id",
    )
    user_id = user_id_opt or _ask(
        "USER_ID",
        existing.user_id if existing else "",
        flag="--user-id",
    )
    if base_url:
        try:
            base_url_in = profiles.resolve_base_url(base_url)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="--base-url") from exc
    else:
        base_url_in = _ask_base_url(existing.base_url if existing else None)

    creds = profiles.ProfileCreds(
        name=profile,
        api_key=api_key,
        business_id=business_id,
        user_id=user_id,
        base_url=base_url_in,
    )

    if not skip_validation:
        cfg = EnvConfig(
            name=profile.lower(),
            api_key=api_key,
            business_id=business_id,
            user_id=user_id,
            base_url=base_url_in,
        )
        try:
            _validate_creds(cfg)
        except KizenAPIError as exc:
            err_console.print(
                f"[red]Credential check failed[/red] ({exc.status_code}): {exc.message}\n"
                "Nothing was written. Re-run and re-enter the values, or pass "
                "--skip-validation to store them anyway."
            )
            raise typer.Exit(code=1) from exc
        console.print("[green]credentials verified[/green] against the live env")

    stored_at = profiles.write_profile(creds)
    console.print(f"[green]stored profile[/green] [bold]{profile}[/bold] → {stored_at}")

    if no_pin:
        console.print(
            "[dim]directory not pinned; set KIZEN_PROFILE or pass --profile "
            "to target this env.[/dim]"
        )
    else:
        pin_path = profiles.write_pin(profile, business_id, cwd)
        console.print(
            f"[green]pinned[/green] [bold]{cwd.name}[/bold] to "
            f"[bold]{profile}[/bold] (business_id {business_id}) → {pin_path}"
        )

    # Folder scaffolding is independent of the pin — a folder someone drives
    # with --profile still wants the instruction stubs.
    for dead in docs_res.clear_legacy_links(cwd):
        console.print(f"[yellow]removed stale link[/yellow] {dead.name}")

    for written in docs_res.write_stubs(cwd, profile, force=refresh_stubs):
        console.print(f"[green]wrote[/green] {written.name}")

    console.print(
        "\n[bold]Next:[/bold] open this folder in Claude Code and describe what "
        "you want to build.\n"
        "[dim]The agent reads CLAUDE.md, which points it at "
        "`kizen docs show operating`.[/dim]"
    )
