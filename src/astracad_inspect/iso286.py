"""ISO 286-2 fit classes, resolved only where a cross-checked published table says so.

A callout such as "⌀20 H7" states a tolerance by letter code, and the numbers behind that code
come from a table, not from the drawing. This module holds such a table - the cells that two
independent published ISO 286-2 tables state identically, over the nominal size steps ISO 286-2's
own Table 1 defines, each with the band width that table prints for its IT grade, built by
`tools/iso286_extract.py` - and refuses anything it does not hold:

* a nominal size outside the loaded size steps;
* a class, or a class-at-step combination, no source confirmed;
* a designation that is not a letter code plus an IT grade.

A refusal returns no limits and a reason that says why. The caller then blocks approval; the
nominal is never used as if it were a tolerance zone, and no deviation is extrapolated from the
neighbouring column. Deviations are stored in micrometres exactly as published and converted
here, so the table's own unit is preserved rather than re-derived.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

_CODE = re.compile(r"^([A-Za-z]{1,2})(\d{1,2})$")
_UM_PER_MM = 1000.0


@dataclass(frozen=True)
class FitResolution:
    """The outcome of one lookup, resolved or refused, with the bracket it was read from."""
    code: str
    nominal: float
    limits: tuple[float, float] | None = None
    bracket: tuple[int, int] | None = None
    upper_um: float | None = None
    lower_um: float | None = None
    side: str | None = None
    reason: str | None = None

    @property
    def resolved(self) -> bool:
        return self.limits is not None

    def describe(self) -> str:
        if self.limits is None:
            return self.reason or "not resolved"
        return (f"ISO 286-2 {self.code} over {self.bracket[0]}-{self.bracket[1]} mm: "
                f"{_um(self.upper_um)}/{_um(self.lower_um)} um -> "
                f"{self.limits[0]:g} to {self.limits[1]:g} mm")


def _um(value: float | None) -> str:
    return "" if value is None else f"{value:+g}"


@dataclass(frozen=True)
class FitTable:
    brackets: tuple[tuple[int, int], ...]
    cells: tuple[dict, ...]
    provenance: dict
    _index: dict = None  # type: ignore[assignment]

    def cell(self, side: str, code: str, bracket: tuple[int, int]) -> tuple[float, float] | None:
        found = self._index.get((side, code, bracket))
        return None if found is None else (found[0], found[1])

    def classes(self, side: str) -> set[str]:
        return {c["code"] for c in self.cells if c["side"] == side}


@lru_cache(maxsize=1)
def load_table() -> FitTable:
    raw = json.loads(files("astracad_inspect").joinpath("data/iso286-2.json").read_text("utf-8"))
    cells = tuple(raw["cells"])
    return FitTable(
        brackets=tuple((int(a), int(b)) for a, b in raw["brackets"]),
        cells=cells,
        provenance=raw["provenance"],
        _index={(c["side"], c["code"], tuple(c["bracket"])): (c["upper_um"], c["lower_um"])
                for c in cells},
    )


def support_statement() -> str:
    """The capability sentence for this table, worded from the cells actually loaded, so the plan's
    support matrix and the `doctor` route list cannot end up stating different scopes."""
    t = load_table()
    return (f"partial - resolved from the loaded ISO 286-2 subset: {len(t.classes('hole'))} hole and "
            f"{len(t.classes('shaft'))} shaft classes over {t.brackets[0][0]}-{t.brackets[-1][1]} mm "
            f"in {len(t.brackets)} standard size steps; every cell is stated identically by two "
            f"independent published tables and its band equals the IT value ISO 286-2 Table 1 prints "
            f"for that step, and an unconfirmed class stays unresolved")


def size_bracket(nominal_mm: float) -> tuple[int, int] | None:
    """The loaded nominal bracket holding this size, or None when no loaded column covers it.

    The tables label a column "over 18, including 30", so the lower bound is exclusive and the
    upper one inclusive; a size exactly on a boundary belongs to the smaller column.
    """
    for over, inc in load_table().brackets:
        if over < nominal_mm <= inc:
            return over, inc
    return None


def _split(code: str) -> tuple[str, str, int] | None:
    m = _CODE.match((code or "").strip())
    if not m:
        return None
    letters, grade = m.group(1), int(m.group(2))
    if not (letters.isupper() or letters.islower()):
        return None
    # ISO 286 writes a deviation letter as one symbol; the only two-letter code in the standard
    # is the symmetric JS/js. Anything else, "HG7" for instance, is not a class designation.
    if len(letters) == 2 and letters.upper() != "JS":
        return None
    return ("hole" if letters.isupper() else "shaft"), letters, grade


def resolve_fit(nominal_mm: float, code: str) -> FitResolution:
    """Look up one ISO 286-2 class, or explain why this build will not state its limits."""
    table = load_table()
    parsed = _split(code)
    if parsed is None:
        return FitResolution(code=code, nominal=nominal_mm,
                             reason=f"{code!r} is not a recognised ISO 286-2 class designation "
                                    f"(a one- or two-letter code plus an IT grade number)")
    side, _letters, _grade = parsed
    if nominal_mm <= 0:
        return FitResolution(code=code, nominal=nominal_mm, side=side,
                             reason=f"nominal size {nominal_mm:g} mm cannot be placed in an "
                                    f"ISO 286-2 nominal bracket")
    bracket = size_bracket(nominal_mm)
    if bracket is None:
        return FitResolution(code=code, nominal=nominal_mm, side=side,
                             reason=f"size {nominal_mm:g} mm is outside the nominal brackets of "
                                    f"the loaded ISO 286-2 subset")
    cell = table.cell(side, code, bracket)
    if cell is None:
        if code in table.classes(side):
            why = (f"ISO 286-2 class {code} is not loaded for the {bracket[0]}-{bracket[1]} mm "
                   f"bracket - the published tables do not confirm a value there")
        else:
            why = (f"ISO 286-2 class {code} is not in the loaded subset - no published value was "
                   f"confirmed by both tables")
        return FitResolution(code=code, nominal=nominal_mm, side=side, bracket=bracket,
                             reason=why + "; limits are left unresolved rather than estimated")
    upper_um, lower_um = cell
    limits = (round(nominal_mm + lower_um / _UM_PER_MM, 9),
              round(nominal_mm + upper_um / _UM_PER_MM, 9))
    return FitResolution(code=code, nominal=nominal_mm, limits=limits, bracket=bracket,
                         upper_um=upper_um, lower_um=lower_um, side=side)
