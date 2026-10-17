"""Build the ISO 286-2 table this build is allowed to resolve from, and nothing more.

Two published tables are read (_sources/roymech.org ISO 286-2 hole + shaft pages, and the
"ISO Tolerances for Shafts and Holes" PDF). A cell enters the output only when both state the
same upper and lower deviation for the same letter-grade and the same nominal size step. A cell
one source has and the other lacks is dropped, not kept on one vote: this build would rather
refuse to resolve a fit class than quote a number nobody confirmed.

Where a value belongs is itself a reading, and getting it wrong is what this builder did before
it was checked. Both tables print a finer nominal header (…30-40, 40-50, 50-65…) than ISO 286
defines steps (…30-50, 50-80…), and most rows carry one value per step, so reading a row onto the
header left to right puts every value above 30 mm a column early. Each row is therefore placed by
the width its cells declare - colspan in the HTML, the printed x centre of each value against the
header's column centres in the PDF - and a row whose cells do not cover the header exactly is
dropped rather than guessed at.

Two further checks are then made against the standard's own Table 1 (_sources/iso286-2-sample.pdf,
a preview copy that is read for checking and never shipped as a source): a cell must sit on a size
step ISO 286-2 defines, and its band width must equal the IT value that table prints for its step
and grade. Cross-source agreement alone is not proof, because one table can copy another's typo -
both published tables print E7 over 315-400 mm as +185/+125, a 60 um band, where the standard
states IT7 = 57 um, and only the third check catches it.

The ISO 286-1 formula (i = 0.45*cbrt(D) + 0.001*D) is deliberately NOT used as a gate. Checking
the loaded bands against 10i/16i/25i shows the published IT series is rounded to preferred
numbers rather than to the nearest micrometre - IT7 for >30-50 mm is published as 25 um while
16i = 24.03 um - so the formula would fail the tables it is meant to describe and would invite
"fixing" correct data. It is reported as a note instead.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "_sources"
OUT = ROOT / "src" / "astracad_inspect" / "data" / "iso286-2.json"

PDF = SOURCES / "iso_tolerances_tuke.pdf"
ROUMECH_HOLES = SOURCES / "iso286_holes.html"
ROUMECH_SHAFTS = SOURCES / "iso286_shafts.html"

_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")
_CLASS = re.compile(r"^([A-Z]{1,2})(\d{1,2})$|^([a-z]{1,2})(\d{1,2})$")


def split_deviations(text: str) -> tuple[float, float]:
    """Read one "upper lower" cell exactly as it is written, or refuse.

    A number without a sign is accepted only when it is zero; giving a bare "43" a sign would be
    inventing a deviation. Two numbers that are not in descending order are refused rather than
    silently reordered, because the order in which a table writes them is the claim being read.
    """
    t = (text or "").replace("\u2212", "-").replace("\u2013", "-").strip()
    if not t:
        raise ValueError("empty cell")
    t = re.sub(r"([+-])\s+(\d)", r"\1\2", t)   # "+ 60" is "+60" with a stray space, not two cells
    values: list[float] = []
    for m in _NUMBER.finditer(t):
        token = m.group()
        if token[0] not in "+-" and float(token) != 0.0:
            raise ValueError(f"deviation {token!r} in {text!r} states no sign")
        values.append(float(token))
    if len(values) != 2:
        raise ValueError(f"expected two deviations, read {len(values)} from {text!r}")
    upper, lower = values
    if upper < lower:
        raise ValueError(f"lower deviation {lower} is below the upper {upper} in {text!r}")
    return upper, lower


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- published tables in HTML (roymech) ------------------------------------------------------

def brackets_from_html(markup: str) -> tuple[list[tuple[int, int]], list[str]]:
    """Return the nominal-size brackets and the label row order the table's header declares."""
    rows = re.findall(r"<tr.*?</tr>", markup, re.S | re.I)
    header_over = header_inc = None
    for r in rows:
        cells = _cells(r)
        if cells and cells[0].lower() == "over":
            header_over = cells[1:]
        if cells and cells[0].lower().startswith("inc"):
            header_inc = cells[1:]
    if not header_over or not header_inc:
        raise ValueError("no nominal-size header found")
    if len(header_over) != len(header_inc):
        raise ValueError("nominal-size header is over/inc mismatched")
    return ([(int(float(a)), int(float(b))) for a, b in zip(header_over, header_inc)]), header_over


def _cells(row: str) -> list[str]:
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)]


def _row_cells(row: str) -> list[tuple[int, str]]:
    """One table row as (colspan, text) pairs, in order.

    These tables print a coarse size step once and let the cell lie across the header columns that
    step covers, so a colspan is part of what the cell says. Reading a merged cell as one column
    puts every later value in that row a column early - which is how +66/+50 for 30-50 mm ends up
    claimed for 30-40 mm.
    """
    out = []
    for attrs, body in re.findall(r"<t[dh]([^>]*)>(.*?)</t[dh]>", row, re.S | re.I):
        m = re.search(r'colspan\s*=\s*"?(\d+)"?', attrs)
        out.append((int(m.group(1)) if m else 1,
                    html.unescape(re.sub(r"<[^>]+>", " ", body)).strip()))
    return out


def _fmt_um(value: float) -> str:
    """A deviation the way these tables write it: sign first, no trailing .0, zero unsigned."""
    if value == 0:
        return "0"
    return f"{value:+g}"


def html_cells(path: Path) -> tuple[list[dict], list[dict]]:
    markup = path.read_text(encoding="utf-8", errors="replace")
    brackets, _ = brackets_from_html(markup)
    side = "hole" if "hole" in path.name else "shaft"
    out, dropped = [], []
    for row in re.findall(r"<tr.*?</tr>", markup, re.S | re.I):
        cells = _row_cells(row)
        if len(cells) < 2:
            continue
        code = cells[0][1].replace(" ", "")
        m = _CLASS.match(code)
        if not m:
            continue
        covered = sum(width for width, _t in cells[1:])
        if covered != len(brackets):
            dropped.append({"side": side, "code": code,
                            "reason": f"the row's cells cover {covered} of the {len(brackets)} "
                                      f"header columns, so they cannot be placed on it"})
            continue
        column = 0
        for width, text in cells[1:]:
            over, _inc = brackets[column]
            _over, inc = brackets[column + width - 1]
            column += width
            where = {"side": side, "code": code, "bracket": [over, inc]}
            try:
                upper, lower = split_deviations(text)
            except ValueError as ex:
                # A cell whose two deviations are printed with the separator missing ("-120-142")
                # is not readable on its own - any split point is a guess. It is kept as raw text
                # so the other table can be asked whether it is consistent with it.
                if str(ex).startswith("expected two deviations, read 1 "):
                    out.append({**where, "raw": re.sub(r"\s+", "", text)})
                else:
                    dropped.append({**where, "cell": text, "reason": str(ex)})
                continue
            out.append({**where, "upper_um": upper, "lower_um": lower})
    return out, dropped


# --- published tables in PDF (coordinate layout) ---------------------------------------------

def _lines(words: list[tuple]) -> list[tuple[float, list[tuple[float, float, str]]]]:
    grouped: dict[float, list[tuple[float, float, str]]] = {}
    for x0, y0, x1, _y1, text, *_rest in words:
        grouped.setdefault(round(y0, 1), []).append((x0, x1, text))
    return [(y, sorted(v, key=lambda t: t[0])) for y, v in sorted(grouped.items())]


def _joined_cells(tokens: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Reunite a sign the PDF wrote as its own word with the digits that follow it, keeping the
    width the printed value occupies on the page."""
    out: list[tuple[float, float, str]] = []
    pending: tuple[float, str] | None = None
    for x0, x1, text in tokens:
        if pending is not None:
            out.append((pending[0], x1, pending[1] + text))
            pending = None
        elif text in ("+", "-"):
            pending = (x0, text)
        else:
            out.append((x0, x1, text))
    if pending is not None:
        out.append((pending[0], pending[0] + 4.0, pending[1]))
    return out


# A printed value sits at the centre of the header columns it covers, and the residuals between
# those centres and the column means are under 0.3 pt on these pages while the tightest column
# pitch is about 26 pt. Half a pitch would let a one-column value be read as a two-column one, so
# the window is set well inside it.
_SPAN_TOL_PT = 6.0


def _spans_for_centres(centres: list[float], values: list[float],
                       tol: float = _SPAN_TOL_PT) -> list[tuple[int, int]] | None:
    """Group the header columns that each printed value lies across, by position alone.

    Columns are consumed left to right, and a value takes the run of columns whose centre mean
    falls inside its own. Where two run lengths both fit, the reading is ambiguous and the row is
    refused rather than guessed at.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for centre in values:
        fits = [w for w in range(1, len(centres) - cursor + 1)
                if abs(sum(centres[cursor:cursor + w]) / w - centre) <= tol]
        if len(fits) != 1:
            return None
        spans.append((cursor, cursor + fits[0]))
        cursor += fits[0]
    return spans


def _line_values(toks):
    """One value line as (bracket-span, deviation) pairs, or None when it cannot be read."""
    cells = _joined_cells(toks)
    if not cells:
        return None
    texts = [t for _x0, _x1, t in cells]
    if not all(_NUMBER.fullmatch(t) for t in texts):
        return None
    centres = [(x0 + x1) / 2 for x0, x1, _t in cells]
    return texts, centres


def pdf_cells(path: Path) -> tuple[list[dict], list[dict]]:
    import pymupdf
    doc = pymupdf.open(str(path))
    kept, dropped = [], []
    for page in doc:
        seq = _lines(page.get_text("words"))
        if not seq:
            continue
        head = " ".join(t for _, toks in seq for _x0, _x1, t in toks).lower()
        # The page title on the first shaft page reads "ISO Tolerances for Shafts and Holes", so
        # the size header line is the only one that says which side of the pair a page holds.
        if "nominal shaft sizes" in head:
            side = "shaft"
        elif "nominal hole sizes" in head:
            side = "hole"
        else:
            dropped.append({"page": page.number + 1, "reason": "page states neither a shaft nor "
                                                              "a hole size header"})
            continue
        over_cells: list[tuple[float, float, str]] = []
        inc_vals: list[str] = []
        for y, toks in seq:
            texts = [t for _x0, _x1, t in toks]
            if not texts:
                continue
            if texts[0] == "over":
                over_cells = list(sorted(toks[1:], key=lambda t: t[0]))
            elif texts[0].lower().startswith("inc"):
                inc_vals = [t for _x0, _x1, t in toks[1:]]
        if not over_cells or len(over_cells) != len(inc_vals):
            dropped.append({"page": page.number + 1, "reason": "no usable nominal-size header"})
            continue
        brackets = [(int(float(a)), int(float(b))) for a, b in zip(
            [t for _x0, _x1, t in over_cells], inc_vals)]
        centres = [(x0 + x1) / 2 for x0, x1, _t in over_cells]

        for k, (y, toks) in enumerate(seq):
            texts = [t for _x0, _x1, t in toks]
            if len(texts) != 1:
                continue
            code = texts[0].replace(" ", "")
            if not _CLASS.match(code):
                continue
            above = _value_line(seq, k, -1)
            below = _value_line(seq, k, +1)
            if above is None or below is None:
                dropped.append({"page": page.number + 1, "code": code,
                                "reason": "no value line directly above and below the class label"})
                continue
            upper_line = _line_values(above[1])
            lower_line = _line_values(below[1])
            if upper_line is None or lower_line is None:
                dropped.append({"page": page.number + 1, "code": code,
                                "reason": "value line is not a run of signed numbers"})
                continue
            up_text, up_centres = upper_line
            lo_text, lo_centres = lower_line
            up_spans = _spans_for_centres(centres, up_centres)
            lo_spans = _spans_for_centres(centres, lo_centres)
            if up_spans is None or lo_spans is None:
                dropped.append({"page": page.number + 1, "code": code,
                                "reason": "a printed value cannot be placed on one run of the "
                                          "header columns, or two runs fit it equally well"})
                continue
            if up_spans != lo_spans or len(up_text) != len(lo_text):
                dropped.append({"page": page.number + 1, "code": code,
                                "reason": "the upper and lower value lines do not lie across the "
                                          "same columns, so the row's layout is not understood"})
                continue
            for (first, last), (up_text_, lo_text_) in zip(
                    up_spans, zip(up_text, lo_text)):
                over, _inc = brackets[first]
                _over, inc = brackets[last - 1]
                where = {"page": page.number + 1, "code": code, "bracket": [over, inc]}
                try:
                    upper = _one_deviation(up_text_)
                    lower = _one_deviation(lo_text_)
                except ValueError as ex:
                    dropped.append({**where, "reason": str(ex)})
                    continue
                if upper < lower:
                    dropped.append({**where, "reason": f"upper {upper} is below lower {lower}"})
                    continue
                kept.append({"side": side, "code": code, "bracket": [over, inc],
                             "upper_um": upper, "lower_um": lower})
    return kept, dropped


def _one_deviation(text: str) -> float:
    """One cell of a PDF value line: a single number whose sign is stated, or zero."""
    if text[0] not in "+-" and float(text) != 0.0:
        raise ValueError(f"deviation {text!r} states no sign")
    return float(text)


def _value_line(seq, k: int, step: int):
    """The nearest line before/after the label that carries values, within one row pitch."""
    y, _ = seq[k]
    j = k + step
    while 0 <= j < len(seq):
        ny, toks = seq[j]
        if abs(ny - y) > 14:
            return None
        joined = _joined_cells(toks)
        texts = [t for _x0, _x1, t in joined]
        if len(texts) < 2 or not all(_NUMBER.fullmatch(t) for t in texts):
            j += step
            continue
        return ny, toks
    return None


# --- the standard's own size steps and IT bands (a third, independent reading) -------------------
#
# _sources/iso286-2-sample.pdf is a preview copy of ISO 286-2:2010. Its Table 1 prints the nominal
# size steps and the standard tolerance values, so the builder can ask the standard two questions
# it does not have to be trusted to answer from memory: which size steps exist, and how wide each
# IT grade's band is over each of them. The answers are used to check the published tables, never
# as a source of the shipped numbers - no deviation read from that page is written into the JSON.
ISO_SAMPLE = SOURCES / "iso286-2-sample.pdf"

STANDARD_STEPS = ((3, 6), (6, 10), (10, 18), (18, 30), (30, 50), (50, 80), (80, 120),
                  (120, 180), (180, 250), (250, 315), (315, 400))

_INT = re.compile(r"\d+")
_DECIMAL = re.compile(r"\d+(?:,\d+)?")
_GRADE_COLUMN = re.compile(r"^IT(\d{1,2})$")
# Table 1's two size columns end at x 121 and its first tolerance column starts at x 124.
_MARGIN_X = 123.0
# The two unit labels the page prints above its tolerance columns.
_MICRO = "\u00b5m"
_UNITS = (_MICRO, "mm")


def _bands(words, tol: float = 2.0) -> list[list[tuple[float, float, str]]]:
    """Page words grouped into printed lines.

    A row's size numbers and its tolerance values are written on the same line but their text
    baselines differ by a tenth of a point, so lines are collected by proximity instead of by an
    exact y match.
    """
    out: list[list[tuple[float, float, str]]] = []
    current: list[tuple[float, float, str]] = []
    top: float | None = None
    for x0, y0, x1, _y1, text, *_rest in sorted(words, key=lambda w: (w[1], w[0])):
        if top is None or y0 - top > tol:
            if current:
                out.append(current)
            current, top = [], y0
        current.append((x0, x1, text))
    if current:
        out.append(current)
    return out


def _rows_rise(rows: dict[tuple[int, int], dict[int, float]], fine: set[int]) -> bool:
    """Whether reading the `fine` columns in um and the rest in mm leaves every row increasing.

    A standard tolerance rises with its grade, so this holds only at the page's real unit change.
    It is a check on how the page was read, not on the standard.
    """
    for values in rows.values():
        last = None
        for grade in sorted(values):
            v = values[grade] * (1.0 if grade in fine else 1000.0)
            if last is not None and v < last:
                return False
            last = v
    return True


@lru_cache(maxsize=4)
def _table1(path_str: str) -> tuple[dict[tuple[int, int], float], list[float]]:
    """The Table 1 grid: {(size step, IT grade): tolerance in um}, and the steps in page order."""
    import pymupdf
    doc = pymupdf.open(path_str)
    page = next((p for p in doc if "Values of standard tolerance grades"
                 in " ".join(t[4] for t in p.get_text("words"))), None)
    if page is None:
        raise ValueError("no Table 1 page in the ISO 286-2 copy")
    bands = _bands(page.get_text("words"))
    # The tolerance-column headers sit in one line above the values; the size steps are the two
    # leftmost numbers of every row.
    grade_x: dict[int, float] = {}
    for toks in bands:
        for x0, x1, text in toks:
            m = _GRADE_COLUMN.match(text)
            if m and text != "IT01":      # IT01 and IT1 would share one grade number
                grade_x[int(m.group(1))] = (x0 + x1) / 2
    if not grade_x:
        raise ValueError("Table 1 states no IT grade columns")
    xs = sorted(grade_x.values())
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    pitch = min(gaps) if gaps else 999.0
    # The page prints its values in two units and puts one label centred above each block:
    # micrometres for the fine grades, millimetres for the coarse ones. Where the units change is
    # read off the printed rows rather than assumed, then checked against those labels.
    unit_x: dict[float, str] = {}
    for toks in bands:
        texts = [(x0, x1, t.replace("\u03bc", _MICRO)) for x0, x1, t in toks]
        if len(texts) == 2 and all(t in _UNITS for _a, _b, t in texts) \
                and all(x0 > _MARGIN_X for x0, _x1, _t in texts):
            unit_x = {(x0 + x1) / 2: t for x0, x1, t in texts}
            break
    if len(unit_x) != 2:
        raise ValueError("Table 1 prints no pair of unit labels above its tolerance columns")
    left_unit, right_unit = sorted(unit_x.items())
    if left_unit[1] != _MICRO or right_unit[1] != "mm":
        raise ValueError("Table 1 does not print its fine grades in micrometres above its coarse "
                         "millimetre ones, so no comparison unit is established for it")

    steps: list[tuple[int, int]] = []
    rows: dict[tuple[int, int], dict[int, float]] = {}
    for toks in bands:
        toks = sorted(toks, key=lambda t: t[0])
        nums = [t[2] for t in toks if t[0] < _MARGIN_X and _INT.fullmatch(t[2])]
        if len(nums) != 2:
            continue
        step = (int(nums[0]), int(nums[1]))
        if step not in STANDARD_STEPS:
            continue
        steps.append(step)
        by_grade: dict[int, list[str]] = {}
        for x0, x1, text in toks:
            if x0 < _MARGIN_X or not _DECIMAL.fullmatch(text):
                continue
            centre = (x0 + x1) / 2
            grade = min(grade_x, key=lambda g: abs(grade_x[g] - centre))
            if abs(grade_x[grade] - centre) > pitch / 2:
                continue
            by_grade.setdefault(grade, []).append(text)
        # A column carrying two numbers in one row is a layout accident, not a read value.
        rows[step] = {g: float(t[0].replace(",", ".")) for g, t in by_grade.items() if len(t) == 1}

    grades = sorted(grade_x)
    splits = [k for k in range(1, len(grades)) if _rows_rise(rows, set(grades[:k]))]
    if len(splits) != 1:
        raise ValueError(f"Table 1's unit columns cannot be placed: {len(splits)} of its "
                         f"{len(grades) - 1} column gaps leave every printed row increasing")
    k = splits[0]
    boundary = (grade_x[grades[k - 1]] + grade_x[grades[k]]) / 2
    if not left_unit[0] < boundary < right_unit[0]:
        raise ValueError("the unit change read off Table 1's rows is not between the unit labels "
                         "the page prints")
    scale = {g: (1.0 if i < k else 1000.0) for i, g in enumerate(grades)}
    grid: dict[tuple[tuple[int, int], int], float] = {
        (step, grade): round(value * scale[grade], 6)
        for step, values in rows.items() for grade, value in values.items()}
    return grid, steps


def iso_size_steps(path: Path = ISO_SAMPLE) -> tuple[tuple[int, int], ...]:
    """The nominal size steps ISO 286-2's own Table 1 prints, up to 400 mm."""
    _grid, steps = _table1(str(path))
    return tuple(s for s in sorted(set(steps), key=lambda s: STANDARD_STEPS.index(s)))


def iso_it_values(path: Path = ISO_SAMPLE) -> dict[tuple[tuple[int, int], int], float]:
    """{(size step, IT grade): standard tolerance in micrometres} read off Table 1."""
    grid, _steps = _table1(str(path))
    return grid


def keep_standard_steps(cells: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep the cells that cover one of the standard's size steps, and say why the rest are out.

    Some rows of these tables are printed once per header column instead of once per size step, so
    above 30 mm they claim a value for each of two or three steps ISO 286 does not have (30-40 and
    40-50 where the standard has one 30-50 step). Such a cell cannot be placed on a standard step
    without choosing between two printed numbers for one step, which is an engineering decision
    and not a parsing one, so the class is left unresolved over that range instead.
    """
    kept, dropped = [], []
    for cell in cells:
        bracket = tuple(cell["bracket"])
        if bracket in STANDARD_STEPS:
            kept.append(cell)
        else:
            dropped.append({**{k: cell[k] for k in ("side", "code") if k in cell},
                            "bracket": list(bracket),
                            "reason": "the class is printed once per header column, and this cell "
                                      "covers a column that is not one of ISO 286-2's nominal "
                                      "size steps, so no step carries it"})
    return kept, dropped


def _grade_of(cell: dict) -> int:
    """The IT grade a cell carries: the one combine recorded, or the digits of its own code."""
    if "grade" in cell:
        return int(cell["grade"])
    return int(_CLASS.match(cell["code"]).group(2) or _CLASS.match(cell["code"]).group(4))


def it_band_check(cells: list[dict],
                  it: dict[tuple[tuple[int, int], int], float] | None = None
                  ) -> tuple[list[dict], list[dict]]:
    """Split cells by whether the standard's IT value for their step matches their band width.

    A class's band is its grade's tolerance, so this is a check on which column the value was read
    from rather than on the deviation itself: a row placed one step early carries the next step's
    band and fails here, which is how a mis-aligned table is caught before it is shipped.
    """
    if it is None:
        it = iso_it_values()
    good, bad = [], []
    for cell in cells:
        if "upper_um" not in cell:
            cell["it_band_check"] = "not checked"
            good.append(cell)
            continue
        grade = _grade_of(cell)
        want = it.get((tuple(cell["bracket"]), grade))
        band = round(cell["upper_um"] - cell["lower_um"], 6)
        if want is None:
            cell["it_band_check"] = "no IT value read for this step and grade"
            good.append(cell)
        elif want == band:
            cell["it_band_check"] = "matched"
            good.append(cell)
        else:
            bad.append({**cell, "reason": f"the band is {band:g} um but IT{grade} over "
                                          f"{cell['bracket'][0]}-{cell['bracket'][1]} mm is "
                                          f"{want:g} um in ISO 286-2 Table 1"})
    return good, bad


# --- combine -----------------------------------------------------------------------------------

def key_of(cell: dict) -> tuple:
    return (cell["side"], cell["code"], tuple(cell["bracket"]))


def combine(pdf: list[dict], web: list[dict]) -> tuple[list[dict], list[dict], int]:
    a = {key_of(c): c for c in pdf}
    b = {key_of(c): c for c in web}
    kept, dropped, recovered = [], [], 0
    for k in sorted(set(a) & set(b), key=lambda k: (k[0], k[1], k[2][0])):
        ca, cb = a[k], b[k]
        if "raw" in cb:
            glued = _fmt_um(ca["upper_um"]) + _fmt_um(ca["lower_um"])
            if cb["raw"] != glued:
                dropped.append({"cell": list(k), "pdf": [ca["upper_um"], ca["lower_um"]],
                                "web": cb["raw"], "gluing would read it as": glued,
                                "reason": "the run-together HTML cell is not the PDF pair "
                                         "written without its separator"})
                continue
            recovered += 1
            values = (ca["upper_um"], ca["lower_um"])
        else:
            values = (ca["upper_um"], ca["lower_um"])
            if values != (cb["upper_um"], cb["lower_um"]):
                dropped.append({"cell": list(k), "pdf": [ca["upper_um"], ca["lower_um"]],
                                "web": [cb["upper_um"], cb["lower_um"]],
                                "reason": "the two published tables disagree"})
                continue
        m = _CLASS.match(k[1])
        kept.append({"side": k[0], "code": k[1], "letter": m.group(1) or m.group(3),
                     "grade": int(m.group(2) or m.group(4)),
                     "bracket": list(k[2]), "upper_um": values[0], "lower_um": values[1],
                     "confirmations": 2})
    for k in sorted(set(a) ^ set(b)):
        dropped.append({"cell": list(k), "reason": "present in one published table only"})
    return kept, dropped, recovered


def band_reconcile(kept: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop a cell whose tolerance band contradicts the band its own grade carries elsewhere.

    Two published tables can agree because one copied the other, typographical error included, so
    cross-source agreement is not enough on its own. Within one nominal bracket, a band width
    belongs to the IT grade rather than to the letter, so every class of a grade must state the
    same band. Where the cells disagree, the value several classes state is kept and the odd one
    out is dropped - not corrected, because choosing which published number is wrong is an
    engineering decision, not a parsing one.
    """
    groups: dict[tuple[int, tuple[int, int]], list[dict]] = {}
    for cell in kept:
        groups.setdefault((cell["grade"], tuple(cell["bracket"])), []).append(cell)
    good, dropped = [], []
    for (grade, bracket), cells in groups.items():
        bands: dict[float, int] = {}
        for c in cells:
            band = round(c["upper_um"] - c["lower_um"], 6)
            bands[band] = bands.get(band, 0) + 1
        if len(bands) == 1:
            good.extend(cells)
            continue
        keep_band = max(bands, key=lambda b: (bands[b], b))
        if bands[keep_band] < 2:
            dropped.append({"grade": grade, "bracket": list(bracket), "bands": bands,
                            "reason": "no band is stated by more than one class, so none is "
                                      "the reference"})
            continue
        for c in cells:
            band = round(c["upper_um"] - c["lower_um"], 6)
            if band == keep_band:
                good.append(c)
            else:
                dropped.append({"code": c["code"], "side": c["side"], "bracket": list(bracket),
                                "published": [c["upper_um"], c["lower_um"]], "band_um": band,
                                "band_the_grade_elsewhere_carries_um": keep_band,
                                "reason": "band contradicts the same grade's other classes"})
    return good, dropped


def main() -> int:
    web_holes, d1 = html_cells(ROUMECH_HOLES)
    web_shafts, d2 = html_cells(ROUMECH_SHAFTS)
    pdf_cells_, d3 = pdf_cells(PDF)
    try:
        it = iso_it_values(ISO_SAMPLE)
        steps = iso_size_steps(ISO_SAMPLE)
    except (OSError, ValueError) as ex:
        print(f"REFUSING TO WRITE: ISO 286-2 Table 1 cannot be read ({ex})", file=sys.stderr)
        return 1
    if steps != STANDARD_STEPS:
        print(f"REFUSING TO WRITE: the standard's Table 1 prints steps {steps}, not the "
              f"{STANDARD_STEPS} this builder keys on", file=sys.stderr)
        return 1
    web, off_web = keep_standard_steps(web_holes + web_shafts)
    pdf, off_pdf = keep_standard_steps(pdf_cells_)
    kept, disagree, recovered = combine(pdf, web)
    kept, band_bad = it_band_check(kept, it)
    kept, band_drop = band_reconcile(kept)
    if not kept:
        print("REFUSING TO WRITE: nothing was confirmed by both tables", file=sys.stderr)
        return 1
    brackets = sorted({tuple(c["bracket"]) for c in kept})
    document = {
        "schema": "astracad-iso286/1",
        "generated_by": "tools/iso286_extract.py",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "unit_of_deviations": "micrometres",
        "provenance": {
            "sources": [
                {"name": "roymech.org ISO 286-2 hole limits table",
                 "path": str(ROUMECH_HOLES.relative_to(ROOT)), "sha256": sha256_file(ROUMECH_HOLES)},
                {"name": "roymech.org ISO 286-2 shaft limits table",
                 "path": str(ROUMECH_SHAFTS.relative_to(ROOT)), "sha256": sha256_file(ROUMECH_SHAFTS)},
                {"name": "ISO Tolerances for Shafts and Holes (ISO 286-2) PDF",
                 "path": str(PDF.relative_to(ROOT)), "sha256": sha256_file(PDF)},
            ],
            "checked_against": {
                "name": "ISO 286-2:2010(E), Table 1 (size steps and IT values) and Table 3 "
                        "(E-class deviations)",
                "path": str(ISO_SAMPLE.relative_to(ROOT)), "sha256": sha256_file(ISO_SAMPLE),
                "size_steps": [list(s) for s in STANDARD_STEPS],
                "used_for": "checking only - no deviation read from this page is shipped here",
                "licence_note": "this is a watermarked preview copy of the standard, kept locally "
                                "for verification; no tolerance value from it is redistributed in "
                                "this file, and the shipped numbers come from the two published "
                                "tables above",
            },
            "rule": "a cell is published here only when the PDF table and the HTML table state the "
                    "same upper and lower deviation for the same class over the same nominal size "
                    "step, when that step is one ISO 286-2 defines, and when the cell's band width "
                    "equals the IT value Table 1 prints for that step and grade",
            "alignment_note": "each published table prints a finer nominal header than the standard "
                              "uses, so a row is placed by the width its cells declare (colspan in "
                              "the HTML, printed x centre in the PDF) rather than by reading order; "
                              "a row whose cells do not cover the header exactly is dropped",
            "formula_note": "the ISO 286-1 i-formula is not a gate: the published IT series is "
                            "rounded to preferred numbers (16i = 24.03 um for >30-50 mm is "
                            "published as IT7 = 25 um), so it cannot validate these tables",
            "dropped": (disagree + d1 + d2 + d3 + off_web + off_pdf + band_bad + band_drop)[:200],
            "dropped_counts": {"disagreements": len(disagree),
                               "unreadable_html_cells": len(d1) + len(d2),
                               "unreadable_pdf_lines": len(d3),
                               "not_on_a_standard_size_step_html": len(off_web),
                               "not_on_a_standard_size_step_pdf": len(off_pdf),
                               "band_not_the_standard_it_value": len(band_bad),
                               "band_contradictions": len(band_drop),
                               "glued_html_cells_confirmed_by_the_pdf": recovered},
        },
        "brackets": [list(b) for b in brackets],
        "cells": kept,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    holes = {c["code"] for c in kept if c["side"] == "hole"}
    shafts = {c["code"] for c in kept if c["side"] == "shaft"}
    print(f"cells kept        {len(kept)}  (confirmed by 2 tables, on a standard step, "
          f"and banded by Table 1)")
    print(f"brackets         {[b[0] for b in brackets]} .. {brackets[-1]}")
    print(f"hole classes ({len(holes)})   {' '.join(sorted(holes))}")
    print(f"shaft classes ({len(shafts)})  {' '.join(sorted(shafts))}")
    print(f"disagreements {len(disagree)}   off-step html {len(off_web)} pdf {len(off_pdf)}   "
          f"band vs Table 1 {len(band_bad)}   band contradictions {len(band_drop)}   "
          f"unreadable html {len(d1)+len(d2)} pdf {len(d3)}")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
