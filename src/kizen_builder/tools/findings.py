"""One problem in a spec, and the error that carries every one found.

A planner that checks a whole spec before planning collects a
:class:`Finding` per problem and raises :class:`SpecProblems` once, so the
author fixes them all in one round trip instead of one per run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from kizen_builder.tools.plans import PlanError


@dataclass(frozen=True)
class Finding:
    """``step`` is the step's key and ``index`` its position in ``steps``;
    ``path`` is dotted from the step, or from the spec's root when there is
    no step. ``got`` is the value given, ``allowed`` the values it could
    be, ``suggestion`` the nearest of them, and ``hint`` a command that
    lists candidates or shows the shape."""

    message: str
    path: str = ""
    step: str | None = None
    index: int | None = None
    got: Any = None
    allowed: list[Any] | None = None
    suggestion: str | None = None
    hint: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def text(self) -> str:
        """One line: where, what, and the nearest allowed value."""
        if self.step is not None:
            where = f"step '{self.step}'"
        elif self.index is not None:
            where = f"step #{self.index}"
        else:
            where = ""
        where = " ".join(p for p in (where, self.path) if p)
        line = f"{where}: {self.message}" if where else self.message
        if self.suggestion is not None:
            line += f" (did you mean '{self.suggestion}'?)"
        return line


class SpecProblems(PlanError):
    """Every problem found in a spec. ``warnings`` don't block a plan on
    their own; they ride along so the author sees them in the same pass."""

    def __init__(
        self, errors: list[Finding], warnings: list[Finding] | None = None
    ) -> None:
        self.errors = errors
        self.warnings = warnings or []
        n = len(errors)
        super().__init__(f"{n} problem{'s' if n != 1 else ''} in the spec")

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "errors": [f.as_dict() for f in self.errors],
            "warnings": [f.as_dict() for f in self.warnings],
        }
