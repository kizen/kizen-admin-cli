"""Resolve the active Kizen environment's credentials.

Credentials come from a **Central store** located at
``$XDG_CONFIG_HOME/kizen/credentials.toml`` keyed by profile name (see
:mod:`kizen_builder.profiles`).

Which profile is used is decided *positionally* — by the working directory's
``.kizen/profile`` pin — not by a mutable global "active profile". Resolution
order for the profile name:

    --profile CLI override  >  $KIZEN_PROFILE  >  .kizen/profile pin  >  $KIZEN_ENV

The pin also records the expected ``business_id``; whatever path selected the
profile, the resolved credentials' ``business_id`` must match it or we refuse.
That checksum is the hard guarantee that a command cannot act against the wrong
environment from a pinned directory. The canonical identity of a Kizen
environment is its ``business_id``.

**Helper mode** replaces all of the above except the pin check. When
``$KIZEN_AUTH_HELPER`` is in the process environment, a wrapper (app-builder)
owns authentication: the config comes from ``KIZEN_*`` variables alone, the
credential store is never opened, and requests carry a Bearer header that the
helper command prints (:mod:`kizen_builder.auth_helper`). The variable contract
is documented in ``docs/operating.md``.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import find_dotenv, load_dotenv

from kizen_builder import profiles

# Set once by the CLI root callback (``--profile``); read during resolution so
# the ~30 no-arg ``load_env_config()`` call sites don't need a new parameter.
_cli_profile_override: str | None = None


def set_profile_override(name: str | None) -> None:
    """Record the ``--profile`` value from the CLI for later resolution."""
    global _cli_profile_override
    _cli_profile_override = name


class ConfigError(Exception):
    """Raised when required env vars for the active environment are missing."""


# Helper mode's environment contract. The first one switches the mode on.
HELPER_ENV = "KIZEN_AUTH_HELPER"
_HELPER_VARS = (HELPER_ENV, "KIZEN_BASE_URL", "KIZEN_BUSINESS_ID", "KIZEN_USER_ID")


@dataclass(frozen=True)
class ApiKeyAuth:
    """A profile's API key, sent as ``X-API-KEY`` with the identity headers."""

    api_key: str = field(repr=False)


@dataclass(frozen=True)
class HelperAuth:
    """A command that prints ``{"Authorization": "Bearer <token>"}``.

    ``argv`` is already split; it runs without a shell.
    """

    argv: tuple[str, ...]


Auth = ApiKeyAuth | HelperAuth


@dataclass(frozen=True)
class EnvConfig:
    """Resolved credentials + base URL for one environment."""

    name: str  # Local label from conf file (e.g. "sandbox"). Cosmetic only.
    business_id: str  # Canonical identity — checked against the directory pin.
    user_id: str
    base_url: str
    auth: Auth


def helper_mode() -> bool:
    """Whether a wrapper supplies auth through ``$KIZEN_AUTH_HELPER``.

    Read from the process environment only: :func:`load_env_config` drops the
    variable if a ``.env`` file supplied it, so a checked-in file can't make
    the CLI run a command.
    """
    return HELPER_ENV in os.environ


def refuse_in_helper_mode(command: str, managed: str) -> None:
    """Stop a command that manages what app-builder owns in helper mode.

    ``managed`` completes "app-builder manages ...", e.g.
    "this installation".
    """
    if helper_mode():
        raise ConfigError(
            f"`kizen {command}` is unavailable here: app-builder manages {managed}."
        )


def load_env_config(
    env_name: str | None = None,
    dotenv_path: Path | None = None,
) -> EnvConfig:
    """Resolve the active environment's credentials.

    Selects a profile name (``env_name`` arg > ``--profile`` override >
    ``$KIZEN_PROFILE`` > directory pin > ``$KIZEN_ENV``), loads its credentials
    from the central store and enforces the directory pin's ``business_id``
    checksum before returning.

    In helper mode (``$KIZEN_AUTH_HELPER`` set) the config comes from the
    ``KIZEN_*`` helper variables instead, no ``.env`` is loaded and the
    credential store is never read; the pin's ``business_id`` is still
    enforced.
    """
    if helper_mode():
        return _load_helper_config(env_name)

    if dotenv_path is not None:
        load_dotenv(dotenv_path, override=False)
    else:
        load_dotenv(find_dotenv(usecwd=True), override=False)
    # Helper mode comes from the real environment only (see helper_mode()).
    os.environ.pop(HELPER_ENV, None)

    pin = profiles.load_pin()

    resolved = (
        env_name
        or _cli_profile_override
        or os.environ.get("KIZEN_PROFILE")
        or (pin.profile if pin else None)
        or os.environ.get("KIZEN_ENV")
    )
    if not resolved:
        raise ConfigError(
            "No environment specified. "
            "Run `kizen init` to configure one, or pass --profile <name>."
        )

    config = _load_profile(resolved)

    # Hard pin: whatever selected the profile, its identity must match what the
    # directory declared. Refuse loudly rather than act on the wrong env.
    if pin and pin.business_id and config.business_id != pin.business_id:
        raise ConfigError(
            f"Directory pinned to profile '{pin.profile}' "
            f"(business_id {pin.business_id}), but resolved profile "
            f"'{config.name}' has business_id {config.business_id}. Refusing.\n"
            f"To retarget this directory, edit {pin.path}."
        )

    return config


def _load_profile(name: str) -> EnvConfig:
    """Build an EnvConfig from the central credential store."""
    creds = profiles.get_profile(name)
    if creds is None:
        raise ConfigError(
            f"No profile named '{name}' in {profiles.credentials_path()}. "
            f"Run `kizen init --profile {name}` to configure it."
        )
    if not creds.api_key or not creds.business_id or not creds.user_id:
        raise ConfigError(
            f"Profile '{name}' in {profiles.credentials_path()} is missing "
            f"required fields. Run `kizen init --profile {name}` to re-enter them."
        )
    return EnvConfig(
        name=name.lower(),
        business_id=creds.business_id,
        user_id=creds.user_id,
        base_url=creds.base_url,
        auth=ApiKeyAuth(creds.api_key),
    )


def _load_helper_config(env_name: str | None) -> EnvConfig:
    """Build an EnvConfig from the helper-mode variables. Never reads the
    credential store or a ``.env`` file."""
    selectors = [
        label
        for label, value in (
            ("an explicit environment name", env_name),
            ("--profile", _cli_profile_override),
            ("$KIZEN_PROFILE", os.environ.get("KIZEN_PROFILE")),
        )
        if value
    ]
    if selectors:
        raise ConfigError(
            f"${HELPER_ENV} is set, so app-builder supplies the environment, "
            f"but {' and '.join(selectors)} also selects a profile. "
            "Use one or the other."
        )

    values = {var: os.environ.get(var, "").strip() for var in _HELPER_VARS}
    missing = [var for var, value in values.items() if not value]
    if missing:
        raise ConfigError(
            f"${HELPER_ENV} is set, but {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} missing or empty. "
            f"Running under app-builder needs all of {', '.join(_HELPER_VARS)}."
        )

    try:
        argv = tuple(shlex.split(values[HELPER_ENV]))
    except ValueError as exc:
        raise ConfigError(f"${HELPER_ENV} can't be parsed: {exc}") from None
    base_url = _helper_base_url(values["KIZEN_BASE_URL"])
    business_id = values["KIZEN_BUSINESS_ID"]

    pin = profiles.load_pin()
    if pin and pin.business_id and business_id != pin.business_id:
        raise ConfigError(
            f"Directory pinned to profile '{pin.profile}' "
            f"(business_id {pin.business_id}), but KIZEN_BUSINESS_ID is "
            f"{business_id}. Refusing.\n"
            f"To retarget this directory, edit {pin.path}."
        )

    return EnvConfig(
        name=urlsplit(base_url).netloc,
        business_id=business_id,
        user_id=values["KIZEN_USER_ID"],
        base_url=base_url,
        auth=HelperAuth(argv),
    )


def _helper_base_url(value: str) -> str:
    """``$KIZEN_BASE_URL`` as an https origin, like a profile's ``base_url``."""
    parts = urlsplit(value.rstrip("/"))
    if (
        parts.scheme != "https"
        or not parts.netloc
        or parts.path
        or parts.query
        or parts.fragment
    ):
        raise ConfigError(
            f"KIZEN_BASE_URL must be an https origin such as "
            f"https://app.go.kizen.com, with no path; got {value!r}."
        )
    return f"https://{parts.netloc}"
