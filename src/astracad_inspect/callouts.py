"""Deterministic callout interpretation.

The parser is pure and table-driven so extraction accuracy is measurable and regressions are
reproducible. Two rules are non-negotiable:

* A tolerance is never inferred. An entry such as "86.98 87.05" is reported as ambiguous
  rather than read as a limit range, because that reading is an assumption about notation.
* Text the engine does not understand is retained and escalated, never silently dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import CharKind, Deviations, FitClass, LimitPair, NoTolerance, Symmetric, Tolerance

_DIAMETER_CHARS = "⌀Øø∅φΦ"
_NUM = r"(?:\d+(?:\.\d+)?|\.\d+)"

# Modifiers a dimension callout commonly carries that are not a tolerance. They are preserved
# as notes; treating them as tolerance text would fabricate a blocking error.
NON_TOLERANCE_WORDS = {
    "thru", "thru-all", "through", "deep", "depth", "typ", "type", "eq", "sp", "eqsp",
    "ref", "reference", "max", "min", "plcs", "places", "cbore", "counterbore", "c'bore",
    "csink", "countersink", "rg", "round", "break", "sharp", "edges", "all", "see", "note",
    "drill", "reamer", "tap", "finish", "chamfer", "spot", "weld", "mach", "surf", "surface",
    "grind", "mill", "turn", "dia", "diameter", "radius", "angle", "basic", "remove",
    "material", "each", "side", "both", "per", "sheet", "metal", "eq-sp", "to",
}

# One deviation letter plus an IT grade, or the standard's only two-letter code, the symmetric
# JS/js. Anything else that looks like letters-and-digits ("HG7") is not a class designation and
# stays on the unrecognised-text path.
_FIT_CODE = re.compile(r"^(?:[A-Za-z]|[Jj][Ss])\d{1,2}$")
# A thread is written with a capital M, and a pitch is the giveaway when one is given. A bare
# lowercase "m6" behind a diameter is an ISO 286 shaft class, not a thread, so it must not be
# diverted to the thread path — that would leave a resolvable tolerance unexplained.
_THREAD = re.compile(r"^M\d+(?:\.\d+)?(?:[ -]?\d+(?:\.\d+)?)?$"
                    r"|^[Mm]\d+(?:\.\d+)?[ -]\d+(?:\.\d+)?$")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'.\-]*")

_DMS = re.compile(rf"({_NUM})°\s*(?:(\d+(?:\.\d+)?)['′])?\s*(?:(\d+(?:\.\d+)?)[\x22\u2033])?")
_SYM = re.compile(rf"±\s*({_NUM})")
_DEV = re.compile(rf"\+\s*({_NUM})\s*[/,]?\s*-\s*({_NUM})")
_DEV_REV = re.compile(rf"-\s*({_NUM})\s*[/,]\s*\+\s*({_NUM})")
_LIMITS = re.compile(rf"({_NUM})\s*/\s*({_NUM})")


def normalise(raw: str) -> str:
    """Map authoring variants onto one canonical spelling without changing meaning."""
    t = raw or ""
    for code, repl in (("%%c", "⌀"), ("%%C", "⌀"), ("%%d", "°"), ("%%D", "°"),
                       ("%%p", "±"), ("%%P", "±")):
        t = t.replace(code, repl)
    t = re.sub(r"\\A\d+;", "", t)
    t = t.replace("\\P", " ").replace("\\p", " ")
    t = t.replace("{", " ").replace("}", " ")
    for ch in _DIAMETER_CHARS:
        t = t.replace(ch, "⌀")
    t = t.replace("−", "-").replace("–", "-").replace("—", "-").replace("⁄", "/")
    t = t.replace("×", "x").replace("X", "x")
    t = t.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class CalloutParse:
    raw: str
    normalised: str
    kind: CharKind = CharKind.UNKNOWN
    nominal: float | None = None
    unit: str | None = None
    tolerance: Tolerance = NoTolerance()
    count: int = 1
    fit: FitClass | None = None
    measured_placeholder: bool = False
    modifiers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unresolved_reason: str | None = None

    @property
    def resolved(self) -> bool:
        return self.unresolved_reason is None and self.kind is not CharKind.UNKNOWN


def _extract_count(t: str) -> tuple[str, int]:
    m = re.match(rf"^(\d+)\s*x\s*(?=[⌀R]|{_NUM})", t, re.I)
    if m:
        return t[m.end():].strip(), int(m.group(1))
    m = re.search(rf"\(\s*(\d+)\s*x?\s*\)$|[, ](\d+)\s*x$", t, re.I)
    if m:
        return t[: m.start()].strip(), int(m.group(1) or m.group(2))
    return t, 1


def _split_words(t: str) -> tuple[str, list[str]]:
    """Peel alphabetic tokens off so they cannot contaminate the numeric grammar. Fit codes
    and thread designations keep their digits because they are matched letter-first."""
    words = _WORD.findall(t)
    return re.sub(r"\s+", " ", _WORD.sub(" ", t)).strip(), words


def _resolve_fit_class(p: CalloutParse) -> None:
    """Look an ISO 286-2 class up in the published deviation table and record what it said.

    A class the loaded table confirms is resolved. Everything else stays unresolved with the
    refusal reason, and an earlier reason (a thread, say) is never overwritten."""
    if p.fit is None:
        return
    from .limits import resolve_fit_limits
    limits, reason = resolve_fit_limits(p.nominal, p.fit, p.unit or "mm")
    if limits is not None:
        code = p.fit.hole or p.fit.shaft
        from . import iso286
        step = iso286.size_bracket(p.nominal)
        p.notes.append(f"ISO 286-2 {code} over {step[0]}-{step[1]} mm: limits {limits[0]:g} to "
                       f"{limits[1]:g} mm, read from the published deviation table")
        return
    why = reason or "the loaded ISO 286-2 table states no value for this class"
    p.unresolved_reason = p.unresolved_reason or why
    p.notes.append("fit class left unresolved: " + why)


def _classify_words(words: list[str], p: CalloutParse) -> None:
    unknown: list[str] = []
    fit_codes: list[str] = []
    for w in words:
        key = w.lower().strip("'.-")
        # A spelled-out kind word decides the kind before it is filed as a modifier: "10 DIA"
        # states a diameter, and measuring a bore is not the same activity as measuring a width.
        if key in {"dia", "diam", "diameter"}:
            p.kind = CharKind.DIAMETER
            p.modifiers.append(w)
            p.notes.append(repr(w) + " read as a diameter dimension")
            continue
        if key in {"rad", "radius"}:
            p.kind = CharKind.RADIUS
            p.modifiers.append(w)
            p.notes.append(repr(w) + " read as a radius dimension")
            continue
        if key in NON_TOLERANCE_WORDS:
            p.modifiers.append(w)
            continue
        if _THREAD.match(w):
            p.unresolved_reason = ("thread designation " + repr(w) + " is not supported by this "
                                   "build; a thread is not a size dimension and no limits are claimed")
            p.notes.append("thread callout preserved for engineering review rather than converted")
            continue
        if _FIT_CODE.match(w):
            fit_codes.append(w)
            continue
        if key in {"r", "sr"}:
            p.kind = CharKind.RADIUS
            p.notes.append("trailing " + repr(w) + " read as a radius dimension")
            continue
        unknown.append(w)

    if fit_codes:
        hole = next((c for c in fit_codes if c[:1].isupper()), None)
        shaft = next((c for c in fit_codes if c[:1].islower()), None)
        code = "/".join(fit_codes)
        p.fit = FitClass(hole=hole, shaft=shaft)
        p.tolerance = p.fit
        p.notes.append("fit class " + repr(code) + " recognised, its numbers come from ISO 286-2")

    if unknown:
        reason = "unrecognised annotation text: " + repr(" ".join(unknown))
        p.unresolved_reason = p.unresolved_reason or reason
        p.notes.append("unrecognised words were preserved on the characteristic, not discarded")


def _pull_tolerance(t: str, p: CalloutParse) -> str:
    """Match a signed or symmetric tolerance out of the numeric text; return what remains."""
    if m := _DEV.search(t):
        p.tolerance = Deviations(upper=float(m.group(1)), lower=-float(m.group(2)))
        p.notes.append("deviations read as +" + m.group(1) + " / -" + m.group(2))
        return (t[: m.start()] + " " + t[m.end():]).strip()
    if m := _DEV_REV.search(t):
        p.tolerance = Deviations(upper=float(m.group(2)), lower=-float(m.group(1)))
        p.notes.append("deviations read as +" + m.group(2) + " / -" + m.group(1) + ", written low first")
        return (t[: m.start()] + " " + t[m.end():]).strip()
    if m := _SYM.search(t):
        p.tolerance = Symmetric(plus_minus=float(m.group(1)))
        return (t[: m.start()] + " " + t[m.end():]).strip()
    return t


def _finish_size(p: CalloutParse, numeric: str) -> None:
    plain = re.findall(_NUM, numeric)

    if not plain:
        if p.measured_placeholder:
            p.notes.append("tolerance read from override text; nominal must come from the geometry")
        elif p.fit is None and p.unresolved_reason is None:
            p.unresolved_reason = "no numeric value found in the callout"
        return

    if len(plain) == 1:
        rest = numeric.replace(plain[0], " ", 1).strip()
        if rest and isinstance(p.tolerance, NoTolerance):
            if m := _LIMITS.search(rest):
                hi, lo = sorted((float(m.group(1)), float(m.group(2))), reverse=True)
                p.tolerance = LimitPair(maximum=hi, minimum=lo)
                p.notes.append("limit-pair callout: nominal not stated, limits as written")
                return
            p.unresolved_reason = p.unresolved_reason or (
                "numeric tolerance text not covered by the rules: " + repr(rest))
            return
        p.nominal = float(plain[0])
        return

    if len(plain) == 2 and "/" in numeric:
        hi, lo = sorted((float(plain[0]), float(plain[1])), reverse=True)
        p.tolerance = LimitPair(maximum=hi, minimum=lo)
        p.notes.append("limit-pair callout: nominal not stated, limits as written")
        return

    p.nominal = float(plain[0])
    if len(plain) == 2:
        p.unresolved_reason = p.unresolved_reason or (
            "two numeric tokens (" + plain[0] + ", " + plain[1] + ") with no separating notation — "
            "reading them as a tolerance range would be an assumption, so this needs review")
    else:
        p.unresolved_reason = p.unresolved_reason or (
            str(len(plain)) + " numeric tokens in the callout; not interpretable as one dimension")


def parse_callout(raw: str, default_unit: str = "mm") -> CalloutParse:
    t = normalise(raw)
    p = CalloutParse(raw=raw, normalised=t)
    if not t:
        p.unresolved_reason = "empty callout text"
        return p

    if "<>" in t:
        p.measured_placeholder = True
        t = t.replace("<>", " ").strip()
        p.kind, p.unit = CharKind.LINEAR, default_unit
        p.notes.append("'<>': the callout displays the measured value, so the text states no nominal")
        if not t:
            p.notes.append("nominal and tolerance must come from the dimension's own geometry")
            return p

    t, p.count = _extract_count(t)

    if m := _DMS.search(t):
        deg = float(m.group(1))
        if m.group(2):
            deg += float(m.group(2)) / 60.0
        if m.group(3):
            deg += float(m.group(3)) / 3600.0
        p.kind, p.nominal, p.unit = CharKind.ANGULAR, deg, "deg"
        numeric, words = _split_words((t[: m.start()] + " " + t[m.end():]).strip())
        _classify_words(words, p)
        _pull_tolerance(numeric, p)
        _resolve_fit_class(p)
        return p

    radius_prefix = re.match(r"^(S?R)\s*(?=[+-]?\.?\d)", t, re.I)
    if "⌀" in t:
        p.kind = CharKind.DIAMETER
        if t.lower().startswith("s⌀"):
            p.notes.append("spherical diameter: treated as a diameter dimension")
    elif radius_prefix:
        p.kind = CharKind.RADIUS
        if radius_prefix.group(1).upper() == "SR":
            p.notes.append("spherical radius: treated as a radius dimension")
    elif re.match(r"^(CH|C)\s*(?=[+-]?\.?\d)", t, re.I):
        p.kind = CharKind.LINEAR
        p.notes.append("chamfer length: interpreted as a linear dimension")
    else:
        p.kind = CharKind.LINEAR

    t = t.replace("⌀", " ").replace("s⌀", " ")
    if radius_prefix:
        t = t[radius_prefix.end():]
    elif p.kind is CharKind.LINEAR and re.match(r"^(CH|C)\s*", t, re.I):
        t = re.sub(r"^(CH|C)\s*", "", t, count=1, flags=re.I)
    p.unit = default_unit

    numeric, words = _split_words(t.strip())
    _classify_words(words, p)
    _finish_size(p, _pull_tolerance(numeric, p))
    _resolve_fit_class(p)
    return p
