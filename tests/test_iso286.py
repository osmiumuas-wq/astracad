"""ISO 286-2 fit classes resolve only inside a cross-checked table, and refuse outside it.

The numbers asserted here are read off two published tables (roymech.org ISO 286-2 hole and shaft
pages and the ISO-tolerances PDF in _sources/), agree between them, and each one's band width is
the IT value ISO 286-2's own Table 1 prints for its size step; none of them is computed by the
code under test. Everything this build does not hold verified data for must come back with a
reason, never with a plausible-looking limit.
"""
import pytest

from astracad_inspect.iso286 import FitResolution, load_table, resolve_fit, size_bracket

# (nominal mm, class, expected (min, max) mm) — hole classes are uppercase, shafts lowercase.
# The size steps are ISO 286's own (…18-30, 30-50, 50-80, 80-120, 120-180, 180-250…), which are
# coarser than the headers both published tables print, so a case at 45 mm belongs to the 30-50
# step rather than to a 30-40 column.
LOADED = [
    (20.0, "H7", (20.000, 20.021)),    # ES +21 / EI 0 over 18-30
    (14.0, "H7", (14.000, 14.018)),    # ES +18 over 10-18
    (40.0, "H7", (40.000, 40.025)),    # ES +25 over 30-50
    (45.0, "H7", (45.000, 45.025)),    # the same step, not the 40-50 column the header prints
    (60.0, "H7", (60.000, 60.030)),    # ES +30 over 50-80
    (100.0, "H7", (100.000, 100.035)),  # ES +35 over 80-120
    (200.0, "H7", (200.000, 200.046)),  # ES +46 over 180-250
    (40.0, "E6", (40.050, 40.066)),     # ES +66 / EI +50 over 30-50, per ISO 286-2 Table 3
    (20.0, "H6", (20.000, 20.013)),
    (20.0, "G6", (20.007, 20.020)),    # ES +20 / EI +7
    (20.0, "P7", (19.965, 19.986)),    # ES -14 / EI -35, both sides negative
    (20.0, "JS6", (19.9935, 20.0065)),  # ±6.5 µm
    (20.0, "h7", (19.979, 20.000)),    # es 0 / ei -21
    (200.0, "h7", (199.954, 200.000)),  # es 0 / ei -46 over 180-250
    (20.0, "g6", (19.980, 19.993)),    # es -7 / ei -20
    (20.0, "k6", (20.002, 20.015)),    # es +15 / ei +2
    (20.0, "d6", (19.922, 19.935)),    # es -65 / ei -78
]


@pytest.mark.parametrize("nominal,code,want", LOADED)
def test_a_loaded_class_resolves_to_its_published_limits(nominal, code, want):
    got = resolve_fit(nominal, code)
    assert got.reason is None, got.reason
    assert tuple(round(v, 6) for v in got.limits) == pytest.approx(want, abs=1e-9)
    assert round(want[1] - want[0], 6) == pytest.approx(
        (got.upper_um - got.lower_um) / 1000.0, abs=1e-9)


def test_the_bracket_is_the_one_containing_the_nominal_size():
    assert size_bracket(20.0) == (18, 30)
    assert size_bracket(6.0) == (3, 6)          # "inc." is inclusive
    assert size_bracket(18.0) == (10, 18)
    assert size_bracket(18.001) == (18, 30)
    assert size_bracket(45.0) == (30, 50)       # one step, where the header prints two columns
    assert size_bracket(400.0) == (315, 400)    # the last step the loaded tables reach


@pytest.mark.parametrize("nominal,code", [
    (450.0, "H7"),      # above the last loaded size step
    (2.0, "H7"),        # below the first loaded step
    (20.0, "H12"),      # grade not carried by the loaded hole rows
    (20.0, "s6"),       # shaft letter not in the loaded subset
    (20.0, "Q5"),
    (35.0, "F6"),       # the two published tables disagree here, so nothing is loaded for it
])
def test_nothing_outside_the_loaded_table_is_invented(nominal, code):
    got = resolve_fit(nominal, code)
    assert got.limits is None
    assert got.reason is not None
    assert "loaded" in got.reason and "ISO 286-2" in got.reason


def test_a_cell_the_two_published_tables_disagree_about_stays_unresolved():
    """The PDF prints the F6 lower deviation for the 30-50 mm step as +2 um where the HTML table
    prints +25 um. Neither is chosen: the class is simply not loaded for that size."""
    got = resolve_fit(35.0, "F6")
    assert got.limits is None
    assert "F6" in got.reason
    assert resolve_fit(20.0, "G6").limits is not None   # its neighbours are loaded


def test_a_published_value_the_standard_contradicts_stays_unresolved():
    """Both published tables print E7 over 315-400 mm as +185/+125, a 60 um band, where ISO 286-2
    Table 1 states IT7 = 57 um for that step. Copying each other's mistake is not confirmation, so
    the class resolves over its neighbours and refuses here."""
    assert resolve_fit(330.0, "E7").limits is None
    assert resolve_fit(200.0, "E7").limits is not None


@pytest.mark.parametrize("nominal,code", [(20.0, "H"), (20.0, "7"), (20.0, ""), (20.0, "HG7")])
def test_a_class_that_is_not_a_letter_and_a_grade_is_refused(nominal, code):
    got = resolve_fit(nominal, code)
    assert got.limits is None
    assert "not a recognised ISO 286" in got.reason


def test_a_size_that_is_not_positive_cannot_be_in_a_nominal_bracket():
    for nominal in (0.0, -20.0):
        got = resolve_fit(nominal, "H7")
        assert got.limits is None and got.reason


def test_every_loaded_cell_was_confirmed_by_both_published_tables():
    table = load_table()
    unconfirmed = [c for c in table.cells if c["confirmations"] < 2]
    assert not unconfirmed, f"cells resting on one table only: {unconfirmed[:3]}"
    assert table.provenance["sources"], "the table records no source for its numbers"


def test_a_grade_carries_the_same_band_on_both_sides_of_the_zero_line():
    """An independent check on the loaded data itself: for one nominal bracket and one IT grade
    the hole and shaft tables must state the same band width, because the band is a property of
    the grade rather than of the letter."""
    table = load_table()
    by_grade_bracket: dict[tuple[int, tuple], set[float]] = {}
    for cell in table.cells:
        key = (cell["grade"], tuple(cell["bracket"]))
        by_grade_bracket.setdefault(key, set()).add(
            round(cell["upper_um"] - cell["lower_um"], 6))
    wide = {k: v for k, v in by_grade_bracket.items() if len(v) > 1}
    assert not wide, f"one grade/bracket carries several band widths: {list(wide.items())[:3]}"


def test_a_resolution_always_names_the_bracket_it_came_from():
    got = resolve_fit(20.0, "H7")
    assert isinstance(got, FitResolution)
    assert got.bracket == (18, 30)
    assert (got.upper_um, got.lower_um) == (21.0, 0.0)


def test_the_table_ships_with_the_package():
    """`load_table()` reads through `importlib.resources`, so an installed wheel must contain the
    JSON beside the code. Without the declaration below a wheel installs an engine that resolves
    no fit class at all, and every fit callout on a real drawing would block."""
    from conftest import ROOT
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'astracad_inspect = ["data/*.json"]' in pyproject
