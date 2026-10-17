"""The table builder's number reading: it accepts what two published tables agree on and
drops anything whose sign or spelling it cannot read."""
import json
from pathlib import Path

import pytest

from conftest import ROOT
from iso286_extract import OUT, STANDARD_STEPS, html_cells, pdf_cells, split_deviations

SOURCES = ROOT / "_sources"
pytestmark = pytest.mark.skipif(
    not all((SOURCES / name).exists() for name in ("iso286_holes.html", "iso286_shafts.html",
                                                   "iso_tolerances_tuke.pdf",
                                                   "iso286-2-sample.pdf")),
    reason="the published tables this builder reads are not on this machine")

# Left column of the roymech pages is the upper deviation, right the lower one. The pages write
# them with inconsistent spacing and with the sign sometimes glued to the next number.
CELLS = [
    ("+12 0", (12.0, 0.0)),
    ("+8 0", (8.0, 0.0)),
    ("0 -12", (0.0, -12.0)),
    ("-4 -12", (-4.0, -12.0)),
    ("+4-4", (4.0, -4.0)),
    ("-120-142", (-120.0, -142.0)),
    ("0-15", (0.0, -15.0)),
    ("+94+72", (94.0, 72.0)),
    ("+4.5 -4.5", (4.5, -4.5)),
    ("-0 -8", (0.0, -8.0)),      # the shaft tables write a zero upper deviation as "-0"
    ("+15  +2", (15.0, 2.0)),
]


@pytest.mark.parametrize("text,want", CELLS)
def test_a_paired_cell_is_read_exactly_as_written(text, want):
    assert split_deviations(text) == want


@pytest.mark.parametrize("text", [
    "+68 43",        # a bare unsigned 43: its sign is not stated, so it is not guessed
    "+20 +7 +1",     # three numbers cannot be one upper/lower pair
    "+20",           # one number is half a pair
    "",
    "+1 015 +125",   # a space inside a number: rejoining it would be a reading, not a reading of
    "twenty twelve",
])
def test_an_ambiguous_or_incomplete_cell_is_refused(text):
    with pytest.raises(ValueError):
        split_deviations(text)


def test_a_pair_whose_lower_deviation_is_above_the_upper_is_refused():
    with pytest.raises(ValueError):
        split_deviations("+0 +12")


# --- which column a published value belongs to -------------------------------------------------
#
# Both published tables print a twenty-column nominal header (…30-40, 40-50, 50-65, 65-80,
# 80-100…) but give only eleven values for most rows, because their values belong to the size
# steps ISO 286 itself defines (…18-30, 30-50, 50-80, 80-120, 120-180…). The HTML marks this with
# colspan on the merged cells and the PDF centres each merged value over the columns it covers.
# Reading a row straight left-to-right onto the header columns puts every value above 30 mm one
# column early: it would report E6 over 30-40 as +66/+50, where ISO 286-2:2010 Table 3 states
# +66/+50 for 30-50 mm and +53/+40 for 18-30 mm.
E6_OVER_THE_STANDARD_STEPS = {(3, 6): (28.0, 20.0), (6, 10): (34.0, 25.0), (10, 18): (43.0, 32.0),
                              (18, 30): (53.0, 40.0), (30, 50): (66.0, 50.0), (50, 80): (79.0, 60.0),
                              (80, 120): (94.0, 72.0), (120, 180): (110.0, 85.0),
                              (180, 250): (129.0, 100.0), (250, 315): (142.0, 110.0),
                              (315, 400): (161.0, 125.0)}


def _cells_by_bracket(cells, code):
    return {tuple(c["bracket"]): (c["upper_um"], c["lower_um"])
            for c in cells if c["code"] == code and "upper_um" in c}


def test_the_html_row_is_keyed_by_the_colspan_each_cell_declares():
    cells, _dropped = html_cells(SOURCES / "iso286_holes.html")
    assert _cells_by_bracket(cells, "E6") == E6_OVER_THE_STANDARD_STEPS


def test_the_pdf_row_is_keyed_by_where_its_values_are_printed():
    """An independent route to the same brackets: the PDF has no colspan to read, so each value's
    x position is measured against the header's column centres and the covered columns grouped."""
    cells, _dropped = pdf_cells(SOURCES / "iso_tolerances_tuke.pdf")
    assert _cells_by_bracket(cells, "E6") == E6_OVER_THE_STANDARD_STEPS


# --- what the standard itself says about size steps and band widths ------------------------------
#
# The published tables print a finer header than ISO 286 uses (…30-40, 40-50… where the standard
# has one 30-50 step), so a cell that covers only one of those columns cannot be placed on a
# standard step and is not loaded. ISO 286-2:2010 (the preview copy in _sources/) states the steps
# and the IT grade values, and the builder reads them from that page rather than from memory.
def test_the_steps_the_builder_loads_only_are_the_ones_the_standard_defines():
    from iso286_extract import STANDARD_STEPS, iso_size_steps
    assert iso_size_steps(SOURCES / "iso286-2-sample.pdf") == STANDARD_STEPS


def test_a_cell_covering_columns_the_standard_does_not_define_is_not_loaded():
    """R6 is printed once per header column, so above 30 mm it claims values for steps ISO 286
    does not have. Those cells are dropped instead of being snapped to a neighbouring step."""
    from iso286_extract import keep_standard_steps
    cells, _dropped = html_cells(SOURCES / "iso286_holes.html")
    kept, dropped = keep_standard_steps(cells)
    r6 = {tuple(c["bracket"]) for c in kept if c["code"] == "R6"}
    assert (18, 30) in r6
    assert not ({(30, 40), (40, 50), (50, 65)} & r6), sorted(r6)
    assert any(d["code"] == "R6" for d in dropped)


def test_the_it_grid_is_read_in_one_unit_however_the_page_prints_it():
    """Table 1 states its values in micrometres up to IT11 and in millimetres from IT12 on, with
    the two unit labels printed above the blocks. A cell is compared in micrometres, so a column
    read at its printed face value would refuse every coarse grade."""
    from iso286_extract import iso_it_values
    it = iso_it_values(SOURCES / "iso286-2-sample.pdf")
    assert it[((3, 6), 6)] == 8.0        # the page prints "8"
    assert it[((30, 50), 7)] == 25.0     # the page prints "25"
    assert it[((3, 6), 12)] == 120.0     # the page prints "0,12"
    assert it[((315, 400), 18)] == 8900.0     # the page prints "8,9"


def test_every_band_the_gate_keeps_is_one_the_standard_states():
    from iso286_extract import it_band_check, iso_it_values, keep_standard_steps
    it = iso_it_values(SOURCES / "iso286-2-sample.pdf")
    holes, _ = html_cells(SOURCES / "iso286_holes.html")
    shafts, _ = html_cells(SOURCES / "iso286_shafts.html")
    cells = [c for c in keep_standard_steps(holes + shafts)[0] if "upper_um" in c]
    good, _bad = it_band_check(cells, it)
    assert len(good) > 700, f"the gate refused the published tables: {len(good)} of {len(cells)}"
    unread = [c for c in good if c["it_band_check"] != "matched"]
    assert not unread, f"{len(unread)} cells carry a band the standard cannot confirm: " \
                       f"{[(c['code'], c['bracket']) for c in unread][:5]}"


def test_the_gate_drops_a_published_band_the_standard_contradicts():
    """Both published tables print E7 over 315-400 mm as +185/+125, a 60 um band, while ISO 286-2
    Table 1 states IT7 there as 57 um. Two tables agreeing because one copied the other is not
    proof, so the standard's own value decides and this cell is not loaded."""
    from iso286_extract import it_band_check, iso_it_values, keep_standard_steps
    it = iso_it_values(SOURCES / "iso286-2-sample.pdf")
    holes, _ = html_cells(SOURCES / "iso286_holes.html")
    shafts, _ = html_cells(SOURCES / "iso286_shafts.html")
    _good, bad = it_band_check(keep_standard_steps(holes + shafts)[0], it)
    assert [(c["code"], tuple(c["bracket"])) for c in bad] == [("E7", (315, 400))]
    assert "60" in bad[0]["reason"] and "57" in bad[0]["reason"]


def test_a_cell_whose_band_is_not_the_standard_it_value_for_its_step_is_dropped():
    """The gate that catches a value read from the wrong column: the band belongs to the IT grade,
    so a cell carrying the neighbouring step's band is refused by the standard's own Table 1."""
    from iso286_extract import it_band_check
    shifted = {"side": "hole", "code": "H7", "letter": "H", "grade": 7, "bracket": [50, 80],
               "upper_um": 35.0, "lower_um": 0.0}      # 35 um is IT7 over 80-120, not 50-80
    published = {"side": "hole", "code": "H7", "letter": "H", "grade": 7, "bracket": [50, 80],
                 "upper_um": 30.0, "lower_um": 0.0}
    good, bad = it_band_check([shifted, published])
    assert [c["bracket"] for c in good] == [[50, 80]] and good[0]["upper_um"] == 30.0
    assert bad[0]["upper_um"] == 35.0
    assert "IT7" in bad[0]["reason"] and "30" in bad[0]["reason"]


# --- what the builder actually ships -------------------------------------------------------------

def test_the_shipped_table_is_keyed_on_the_steps_the_standard_defines():
    doc = json.loads(OUT.read_text(encoding="utf-8"))
    assert {tuple(b) for b in doc["brackets"]} == set(STANDARD_STEPS)


def test_every_shipped_cell_came_through_the_standard_band_check():
    doc = json.loads(OUT.read_text(encoding="utf-8"))
    unchecked = [c for c in doc["cells"] if c.get("it_band_check") != "matched"]
    assert not unchecked, f"{len(unchecked)} shipped cells were never banded against Table 1"
    assert not [c for c in doc["cells"] if (c["code"], c["bracket"]) == ("E7", [315, 400])]


def test_the_shipped_table_records_the_standard_it_checked_against():
    """The engine quotes numbers whose authority rests on this third reading, so the JSON has to
    say which copy of the standard was read and under what licence note."""
    doc = json.loads(OUT.read_text(encoding="utf-8"))
    checked = doc["provenance"]["checked_against"]
    assert checked["path"].endswith("iso286-2-sample.pdf") and len(checked["sha256"]) == 64
    assert "preview" in checked["licence_note"].lower()
    assert {tuple(b) for b in checked["size_steps"]} == set(STANDARD_STEPS)
