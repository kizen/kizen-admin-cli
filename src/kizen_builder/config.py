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
helper command prints (:mod:`kizen_builder.auth_helper`).

**API-key mode** is its sibling: when ``$KICODE_API_KEY`` is in the process
environment, a wrapper supplies the key and identity the same way, and requests
carry the profile-style ``X-API-KEY`` headers. Setting both is an error. Helper
mode's contract is documented in ``docs/operating.md``; API-key mode serves
kicode only and is deliberately left out of the shipped docs.
"""

from __future__ import annotations

import os
import re
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


# The wrapper modes' environment contracts. The first of each switches its
# mode on; the identity variables are shared.
HELPER_ENV = "KIZEN_AUTH_HELPER"
KICODE_KEY_ENV = "KICODE_API_KEY"
# Visible ASCII only. Stricter than a header requires (interior spaces are
# legal there), but no key or id has one, and it rules out every value httpx
# would refuse with an error quoting it.
_HEADER_VALUE = re.compile(r"[\x21-\x7e]+")
# The rule header_value_ok() enforces, for messages that never show the value.
HEADER_VALUE_RULE = (
    "may contain only visible ASCII characters "
    "(no spaces, tabs, line breaks or control characters)"
)
_IDENTITY_VARS = ("KIZEN_BASE_URL", "KIZEN_BUSINESS_ID", "KIZEN_USER_ID")
_HELPER_VARS = (HELPER_ENV, *_IDENTITY_VARS)
_KICODE_KEY_VARS = (KICODE_KEY_ENV, *_IDENTITY_VARS)


@dataclass(frozen=True)
class ApiKeyAuth:
    """An API key, sent as ``X-API-KEY`` with the identity headers.

    ``env_var`` names the variable a wrapper supplied it in (API-key mode), or
    is ``None`` for a profile's key, so error advice can point at the source.
    """

    api_key: str = field(repr=False)
    env_var: str | None = None


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


def api_key_mode() -> bool:
    """Whether a wrapper supplies an API key through ``$KICODE_API_KEY``.

    Process environment only, like :func:`helper_mode`: a ``.env`` file can't
    switch it on.
    """
    return KICODE_KEY_ENV in os.environ


def header_value_ok(value: str) -> bool:
    """Whether ``value`` can be sent as an API-key or identity header.

    Checked before anything is sent, because httpx's error for an illegal
    header value quotes the value, and the value may be a secret.
    """
    return bool(_HEADER_VALUE.fullmatch(value))


def wrapper() -> str | None:
    """Who supplies the environment instead of a profile, as named in
    messages, or ``None`` when a profile does. Both wrapper modes at once is
    an error, not either one."""
    _refuse_both_wrappers()
    if helper_mode():
        return "app-builder"
    if api_key_mode():
        return "kicode"
    return None


def refuse_when_wrapped(command: str, managed: str) -> None:
    """Stop a command that manages what a wrapper owns in either wrapper mode.

    ``managed`` completes "<wrapper> manages ...", e.g. "this installation".
    """
    owner = wrapper()
    if owner:
        raise ConfigError(
            f"`kizen {command}` is unavailable here: {owner} manages {managed}."
        )


def _refuse_both_wrappers() -> None:
    if helper_mode() and api_key_mode():
        raise ConfigError(
            f"${HELPER_ENV} and ${KICODE_KEY_ENV} are both set, so there are two "
            "ways to authenticate. Use one or the other."
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

    In helper mode (``$KIZEN_AUTH_HELPER`` set) or API-key mode
    (``$KICODE_API_KEY`` set) the config comes from the ``KIZEN_*`` variables
    instead, no ``.env`` is loaded and the credential store is never read; the
    pin's ``business_id`` is still enforced. Both set is an error.
    """
    _refuse_both_wrappers()
    if helper_mode():
        return _load_helper_config(env_name)
    if api_key_mode():
        return _load_api_key_config(env_name)

    if dotenv_path is not None:
        load_dotenv(dotenv_path, override=False)
    else:
        load_dotenv(find_dotenv(usecwd=True), override=False)
    # The wrapper modes come from the real environment only (see
    # helper_mode()); we only get here when neither was set there.
    os.environ.pop(HELPER_ENV, None)
    os.environ.pop(KICODE_KEY_ENV, None)

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
    bad = [
        field
        for field in ("api_key", "business_id", "user_id")
        if not header_value_ok(getattr(creds, field))
    ]
    if bad:
        raise ConfigError(
            f"Profile '{name}' in {profiles.credentials_path()} has an invalid "
            f"{', '.join(bad)}: {'it' if len(bad) == 1 else 'each'} "
            f"{HEADER_VALUE_RULE}. Run `kizen init --profile {name}` to re-enter "
            f"{'it' if len(bad) == 1 else 'them'}."
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
    values = _wrapper_values(
        env_name,
        _HELPER_VARS,
        supplier="app-builder supplies the environment",
        needs="Running under app-builder",
    )
    try:
        argv = tuple(shlex.split(values[HELPER_ENV]))
    except ValueError as exc:
        raise ConfigError(f"${HELPER_ENV} can't be parsed: {exc}") from None
    return _wrapper_config(values, HelperAuth(argv))


def _load_api_key_config(env_name: str | None) -> EnvConfig:
    """Build an EnvConfig from the API-key-mode variables. Never reads the
    credential store or a ``.env`` file."""
    values = _wrapper_values(
        env_name,
        _KICODE_KEY_VARS,
        supplier="kicode supplies the environment",
        needs="Running under kicode",
    )
    bad = [
        var
        for var in (KICODE_KEY_ENV, "KIZEN_BUSINESS_ID", "KIZEN_USER_ID")
        if not header_value_ok(values[var])
    ]
    if bad:
        raise ConfigError(
            f"{', '.join(bad)} {'is' if len(bad) == 1 else 'are'} invalid: "
            f"{'it' if len(bad) == 1 else 'each'} {HEADER_VALUE_RULE}."
        )
    return _wrapper_config(
        values, ApiKeyAuth(values[KICODE_KEY_ENV], env_var=KICODE_KEY_ENV)
    )


def _wrapper_values(
    env_name: str | None, variables: tuple[str, ...], *, supplier: str, needs: str
) -> dict[str, str]:
    """A wrapper mode's stripped ``variables``, after refusing any profile
    selector and any missing one. ``variables[0]`` is the mode's switch.
    Messages name variables, never their values."""
    switch = variables[0]
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
            f"${switch} is set, so {supplier}, "
            f"but {' and '.join(selectors)} also selects a profile. "
            "Use one or the other."
        )

    values = {var: os.environ.get(var, "").strip() for var in variables}
    missing = [var for var, value in values.items() if not value]
    if missing:
        raise ConfigError(
            f"${switch} is set, but {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} missing or empty. "
            f"{needs} needs all of {', '.join(variables)}."
        )
    return values


def _wrapper_config(values: dict[str, str], auth: Auth) -> EnvConfig:
    """The EnvConfig for a wrapper mode's ``values``, after the pin check."""
    base_url = _wrapper_base_url(values["KIZEN_BASE_URL"])
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
        auth=auth,
    )


def _wrapper_base_url(value: str) -> str:
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
