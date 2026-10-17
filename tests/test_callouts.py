"""The parser must read what a callout says and stop where it stops saying anything."""
import pytest

from astracad_inspect.callouts import normalise, parse_callout
from astracad_inspect.model import CharKind, Deviations, FitClass, LimitPair, NoTolerance, Symmetric


@pytest.mark.parametrize("raw,kind,nominal,tol", [
    ("100 ±0.1", CharKind.LINEAR, 100.0, Symmetric(0.1)),
    ("50 +0.2/-0", CharKind.LINEAR, 50.0, Deviations(0.2, 0.0)),
    ("53 +0.01/-0.02", CharKind.LINEAR, 53.0, Deviations(0.01, -0.02)),
    ("⌀10 +0.05/-0.02", CharKind.DIAMETER, 10.0, Deviations(0.05, -0.02)),
    ("⌀10.05/10.00", CharKind.DIAMETER, None, LimitPair(10.05, 10.0)),
    ("R10", CharKind.RADIUS, 10.0, NoTolerance()),
    ("SR12", CharKind.RADIUS, 12.0, NoTolerance()),
    ("45°", CharKind.ANGULAR, 45.0, NoTolerance()),
    ("45°30'", CharKind.ANGULAR, 45.5, NoTolerance()),
    ("2X ⌀20 THRU", CharKind.DIAMETER, 20.0, NoTolerance()),
    ("CH1.5", CharKind.LINEAR, 1.5, NoTolerance()),
    ("10 DIA", CharKind.DIAMETER, 10.0, NoTolerance()),
])
def test_supported_notation(raw, kind, nominal, tol):
    p = parse_callout(raw)
    assert p.kind is kind, p.notes
    assert p.nominal == nominal
    assert p.tolerance == tol
    assert p.resolved, p.unresolved_reason


@pytest.mark.parametrize("raw,why", [
    ("86.98 87.05", "would be an assumption"),
    ("⌀450 H7", "ISO 286"),
    ("M8-1.25", "thread"),
    ("⌀", "no numeric value"),
    ("GAP", "unrecognised"),
])
def test_ambiguous_or_unsupported_stays_unresolved(raw, why):
    p = parse_callout(raw)
    assert not p.resolved
    assert why in (p.unresolved_reason or ""), p.unresolved_reason


def test_a_fit_class_is_recorded_as_a_class_not_as_invented_numbers():
    p = parse_callout("⌀20 H7")
    assert isinstance(p.tolerance, FitClass)
    assert p.tolerance.hole == "H7" and p.tolerance.shaft is None
    assert p.nominal == 20.0
    # the parser keeps the designation as written; the numbers belong to the table lookup
    assert vars(p.tolerance) == {"hole": "H7", "shaft": None, "kind": "fit_class"}


def test_a_fit_class_the_iso_286_table_confirms_is_resolved():
    """"⌀20 H7" does state a tolerance; the numbers come from the published table, so the "not
    implemented in this build" refusal is no longer true and must not be reported."""
    p = parse_callout("⌀20 H7")
    assert p.resolved, p.unresolved_reason
    assert any("ISO 286-2" in n for n in p.notes), p.notes


def test_a_two_letter_symmetric_class_is_recognised():
    p = parse_callout("⌀20 JS6")
    assert p.resolved, p.unresolved_reason
    assert p.tolerance.hole == "JS6"


def test_a_fit_pair_is_two_features_and_resolves_neither():
    p = parse_callout("⌀25 H7/g6")
    assert isinstance(p.tolerance, FitClass)
    assert (p.tolerance.hole, p.tolerance.shaft) == ("H7", "g6")
    assert not p.resolved
    assert "one characteristic" in p.unresolved_reason


def test_a_class_over_a_size_step_names_that_step_in_the_note():
    """"⌀45 H7" sits on ISO 286's 30-50 mm step, not on the 30-40 column both published tables
    print above it. The note therefore states which step the limits were read from, so a row
    placed on the wrong column cannot survive as a plausible-looking number."""
    p = parse_callout("⌀45 H7")
    assert p.resolved, p.unresolved_reason
    assert any("30-50 mm" in n and "45.025" in n for n in p.notes), p.notes


def test_a_class_the_loaded_table_does_not_confirm_stays_unresolved():
    p = parse_callout("⌀35 F6")
    assert not p.resolved
    assert "not loaded" in p.unresolved_reason
    assert "30-50" in p.unresolved_reason, p.unresolved_reason


def test_a_size_beyond_the_loaded_steps_stays_unresolved():
    p = parse_callout("⌀450 H7")
    assert not p.resolved
    assert "outside the nominal brackets" in p.unresolved_reason, p.unresolved_reason


def test_an_inch_callout_is_not_resolved_from_the_metric_table():
    p = parse_callout("0.7874 H7", default_unit="in")
    assert not p.resolved
    assert "millimetres" in p.unresolved_reason


def test_a_lowercase_size_class_is_not_mistaken_for_a_thread():
    """"⌀20 m6" is a shaft tolerance class. An M-thread is written with a capital M and, when a
    pitch is given, that pitch is the giveaway; reading a bare "m6" as a thread would leave a
    resolvable tolerance unresolved."""
    p = parse_callout("⌀20 m6")
    assert p.resolved, p.unresolved_reason
    assert p.tolerance.shaft == "m6" and p.tolerance.hole is None


@pytest.mark.parametrize("raw", ["M8-1.25", "m8-1.25", "M8"])
def test_a_thread_designation_is_still_a_thread(raw):
    p = parse_callout(raw)
    assert not p.resolved
    assert "thread" in p.unresolved_reason, p.unresolved_reason



def test_count_is_not_the_nominal():
    p = parse_callout("4X ⌀10 THRU")
    assert (p.count, p.nominal) == (4, 10.0)


def test_diameter_spellings_converge():
    for raw in ("⌀10", "Ø10", "%%c10", "φ10", "10 DIA"):
        p = parse_callout(raw)
        assert p.kind is CharKind.DIAMETER and p.nominal == 10.0, raw


def test_measured_placeholder_states_no_nominal():
    p = parse_callout("<>")
    assert p.measured_placeholder and p.nominal is None


def test_normalise_does_not_invent_meaning():
    # codes map to glyphs, but no spacing or wording the drawing did not write is introduced
    assert normalise("%%c20%%p0.1") == "⌀20±0.1"
    assert normalise("\\A1;20\\P10") == "20 10"
    assert normalise("20—30") == "20-30"
    assert normalise("20 H7") == "20 H7"
    assert normalise("⌀  20") == "⌀ 20"


def test_raw_text_is_never_rewritten_by_the_parser():
    raw = "%%c10 +0.05/-0.02"
    assert parse_callout(raw).raw == raw
