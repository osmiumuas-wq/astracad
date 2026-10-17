"""Ingestion against labelled fixtures: what was found, what was refused, what was escalated."""
import pytest

from astracad_inspect.model import CharKind, CheckOutcome, ProvenanceState
from astracad_inspect.units import UnitError

from conftest import FIXTURES, dxf, label, librecad_leader_doc, shown_text_dim_doc


def _tolerance_tag(p):
    return f"{p.tolerance.kind}"


def test_every_labelled_characteristic_is_found(plate):
    want = label("plate_synthetic")["expected_characteristics"]
    found = {c.raw_callout: c for c in plate.characteristics}
    for exp in want:
        hit = [c for raw, c in found.items() if exp["raw_contains"] in raw]
        assert hit, f"nothing on the drawing matches {exp['raw_contains']!r}"
        c = hit[0]
        assert c.kind.value == exp["kind"], exp["raw_contains"]
        assert c.nominal == exp["nominal"], exp["raw_contains"]
        assert _tolerance_tag(c).startswith(exp["tolerance"].split(":")[0])
        assert (c.limits is None) == (exp["limits"] is None)
        if exp["limits"] is not None:
            assert tuple(round(v, 6) for v in c.limits) == tuple(exp["limits"])
        if "count" in exp:
            assert c.count == exp["count"]
        assert (c.state is not ProvenanceState.UNRESOLVED) == exp["resolved"]


def test_no_characteristics_are_invented(plate):
    """Nothing may appear that the label sheet does not expect."""
    want = label("plate_synthetic")["expected_characteristics"]
    extras = [c.raw_callout for c in plate.characteristics
              if not any(w["raw_contains"] in c.raw_callout for w in want)]
    assert not extras, f"extracted characteristics the label sheet does not list: {extras}"


def test_ambiguous_free_text_is_escalated_not_extracted(plate):
    texts = [u.text for u in plate.uninterpreted]
    for exp in label("plate_synthetic")["expected_uninterpreted"]:
        assert any(exp["text_contains"] in t for t in texts), texts
    assert not any("86.98" in c.raw_callout for c in plate.characteristics)


def test_material_note_is_kept_but_not_treated_as_a_dimension(plate):
    assert any("6061" in n for n in plate.notes)
    assert not any("6061" in u.text for u in plate.uninterpreted)


def test_every_characteristic_links_to_an_entity_in_the_source_file(plate):
    for c in plate.characteristics:
        assert c.source is not None
        assert c.source.document_sha256 == plate.sha256
        assert c.source.entity_handle
        assert c.source.entity_type in ("DIMENSION", "ARC_DIMENSION")


def test_census_accounts_for_every_modelspace_entity(plate):
    import ezdxf
    total = sum(1 for _ in ezdxf.readfile(str(dxf("plate_synthetic"))).modelspace())
    assert sum(r.total for r in plate.census) == total


def test_sha256_is_the_file_that_was_read(plate):
    import hashlib
    assert plate.sha256 == hashlib.sha256(dxf("plate_synthetic").read_bytes()).hexdigest()


def _fit_class_bore(tmp_path, radius: float):
    """A bore drawn at a given radius and called out "%%c20 H7" — the written value and the drawn
    geometry are then two independent claims, which is exactly what the cross-check compares."""
    import ezdxf
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_circle((50, 50), radius)
    msp.add_diameter_dim(center=(50, 50), radius=radius, angle=90,
                         text="%%c20 H7", dimstyle="EZ_RADIUS").render()
    path = tmp_path / f"bore_{radius!s}.dxf".replace(".", "_")
    with open(path, "w", encoding="utf-8") as fh:
        doc.write(fh)
    from astracad_inspect.ingest.dxf import read_dxf
    bundle = read_dxf(path)
    return next(c for c in bundle.characteristics if "H7" in c.raw_callout)


def test_a_fit_class_dimension_is_resolved_to_published_limits(tmp_path):
    c = _fit_class_bore(tmp_path, 10.0)
    assert c.limits == (20.0, 20.021)
    assert c.state is ProvenanceState.PARSED
    assert not any(i.code == "no_limits" for i in c.issues)


def test_a_fit_class_bore_drawn_inside_its_band_is_not_a_finding(tmp_path):
    """20.01 mm lies inside the H7 hole band, so the picture and the callout agree. Judging the
    geometry against the nominal alone would report a finding that does not exist."""
    c = _fit_class_bore(tmp_path, 10.005)
    check = next(k for k in c.checks if k.name == "geometry_cross_check")
    assert check.outcome is CheckOutcome.PASSED, check.why
    assert not c.is_blocking


def test_a_fit_class_bore_drawn_outside_its_band_is_a_finding(tmp_path):
    c = _fit_class_bore(tmp_path, 10.02)
    check = next(k for k in c.checks if k.name == "geometry_cross_check")
    assert check.outcome is CheckOutcome.FAILED
    assert any(i.code == "geometry_callout_disagreement" for i in c.issues)
    assert c.is_blocking


def test_a_resolved_fit_class_is_shown_with_its_limits_in_the_table(plate_plan):
    row = next(r for r in plate_plan.rows if "H7" in r.raw_callout)
    assert row.minimum == 20.0 and row.maximum == 20.021
    assert "not resolved" not in row.tolerance_text
    assert "20.021" in row.tolerance_text


def test_the_support_matrix_names_the_loaded_iso_286_subset(plate):
    state = plate.feature_support["tolerance.iso_fit_class"]
    assert not state.startswith("NOT supported")
    assert "ISO 286-2" in state
    # the statement has to carry how the numbers were confirmed, not only that they were
    assert "two independent published tables" in state and "Table 1" in state
    assert "size steps" in state


def test_a_fit_class_row_cannot_claim_limits_without_a_nominal_size(plate):
    """A letter code is a band, so its limits depend on the nominal. A row that carries limits but
    no size cannot have derived them from anything, and validation has to say so."""
    from astracad_inspect.plan import build_plan
    target = next(c for c in plate.characteristics if "H7" in c.raw_callout)
    target.nominal = None
    plan = build_plan(plate)
    check = next(k for k in plan.checks if k.name == "limits_independently_recomputed")
    assert check.outcome is CheckOutcome.FAILED
    assert target.cid in (check.why or ""), check.why


def test_the_drawn_geometry_disagrees_with_the_stale_callout(contradiction_plan):
    """The engine's independent check: one dimension is drawn 100 and called out 90 ±0.1."""
    exp = label("contradiction_synthetic")
    bad = [c for c in contradiction_plan.characteristics
           if c.raw_callout in exp["expect_cross_check_failure_for"]]
    good = [c for c in contradiction_plan.characteristics
            if c.raw_callout in exp["expect_cross_check_pass_for"]]
    assert bad and good
    for c in bad:
        check = next(k for k in c.checks if k.name == "geometry_cross_check")
        assert check.outcome is CheckOutcome.FAILED
        assert c.is_blocking
    for c in good:
        check = next(k for k in c.checks if k.name == "geometry_cross_check")
        assert check.outcome is CheckOutcome.PASSED
        assert not c.is_blocking


def test_a_limit_pair_leaves_the_geometry_with_no_single_value_to_agree_with(plate_plan):
    """The row the cross-check cannot confirm is the limit pair, which states two sizes rather than
    a nominal — not the repeated-feature count row, whose drawn distance does agree with it."""
    pair = next(c for c in plate_plan.characteristics if "10.05/10.00" in c.raw_callout)
    check = next(k for k in pair.checks if k.name == "geometry_cross_check")
    assert check.outcome is CheckOutcome.NOT_VERIFIABLE, check.why
    assert "no single nominal" in check.why, check.why

    count = next(c for c in plate_plan.characteristics if "THRU" in c.raw_callout)
    assert next(k for k in count.checks
                if k.name == "geometry_cross_check").outcome is CheckOutcome.PASSED
    assert plate_plan.coverage.geometry_cross_checked == len(plate_plan.characteristics) - 1


def test_undeclared_units_stop_the_run(tmp_path):
    """$INSUNITS = 0 means the drawing does not say. Millimetres is a guess, not a default."""
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 0
    msp = doc.modelspace()
    msp.add_line((0, 0), (50, 0))
    msp.add_linear_dim(base=(0, 10), p1=(0, 0), p2=(50, 0), text="50").render()
    path = tmp_path / "no_units.dxf"
    with open(path, "w", encoding="utf-8") as fh:
        doc.write(fh)

    from astracad_inspect.ingest.dxf import read_dxf
    with pytest.raises(UnitError):
        read_dxf(path)
    assert read_dxf(path, declared_unit="mm").unit == "mm"


def test_empty_modelspace_is_reported(tmp_path):
    import ezdxf
    from astracad_inspect.ingest.dxf import read_dxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    path = tmp_path / "empty.dxf"
    with open(path, "w", encoding="utf-8") as fh:
        doc.write(fh)
    with pytest.raises(ValueError):
        read_dxf(path)


def test_unreadable_file_is_an_error_not_an_empty_plan(tmp_path):
    from astracad_inspect.ingest.dxf import read_dxf
    junk = tmp_path / "junk.dxf"
    junk.write_bytes(b"this is not a DXF file at all")
    with pytest.raises(ValueError):
        read_dxf(junk)


def test_fixture_directory_is_labelled_synthetic():
    assert label("plate_synthetic")["synthetic"] is True
    assert "not a real" in label("plate_synthetic")["notice"]
    assert FIXTURES.exists()


def test_kinds_present_on_the_fixture(plate):
    assert {c.kind for c in plate.characteristics} >= {
        CharKind.LINEAR, CharKind.DIAMETER, CharKind.RADIUS, CharKind.ANGULAR}


def _minimal_doc(tmp_path, extra_text=(), insunits=4):
    import ezdxf
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    msp.add_line((0, 0), (40, 0))
    msp.add_linear_dim(base=(20, 8), p1=(0, 0), p2=(40, 0), text="40").render()
    for i, t in enumerate(extra_text):
        msp.add_text(t, dxfattribs={"height": 3}).set_placement((100, -5 * i))
    path = tmp_path / f"rev_{abs(hash(tuple(extra_text)))}.dxf"
    with open(path, "w", encoding="utf-8") as fh:
        doc.write(fh)
    return path


def test_revision_is_taken_from_the_drawing_when_the_drawing_states_it(tmp_path):
    from astracad_inspect.ingest.dxf import read_dxf
    bundle = read_dxf(_minimal_doc(tmp_path, ["REV", "C"]))
    assert bundle.revision == "C"
    assert bundle.revision_source == "title block"


def test_a_fallback_revision_is_labelled_as_a_default_not_as_read_content(tmp_path):
    from astracad_inspect.ingest.dxf import read_dxf
    bundle = read_dxf(_minimal_doc(tmp_path, ["nothing labelled"]))
    assert bundle.revision == "A"
    assert bundle.revision_source.startswith("default")
    assert any("revision defaults to A" in w for w in bundle.warnings)


def test_a_caller_supplied_revision_is_attributed_to_the_caller(tmp_path):
    from astracad_inspect.ingest.dxf import read_dxf
    bundle = read_dxf(_minimal_doc(tmp_path, ["REV", "C"]), revision="D")
    assert bundle.revision == "D"
    assert bundle.revision_source == "user-specified"


def test_one_unmeasurable_entity_does_not_cost_the_drawing_its_layout(tmp_path):
    """Balloon placement needs a bounding box, and ezdxf raises instead of returning one when an
    entity cannot be disassembled — a LEADER with no vertex coordinates is what LibreCAD's sample
    drawing holds. A drawing whose dimensions all read must still open, with the unmeasurable
    entity named rather than the run ending in a ValueError."""
    from astracad_inspect.ingest.dxf import read_dxf
    bundle = read_dxf(librecad_leader_doc(tmp_path))
    assert len(bundle.characteristics) == 1
    lo_x, lo_y, hi_x, hi_y = bundle.extents
    assert lo_x <= 0.0 and hi_x >= 40.0, bundle.extents
    assert hi_y > 0.0, bundle.extents
    skip = [w for w in bundle.warnings if "LEADER" in w]
    assert skip, bundle.warnings


def test_the_stated_size_is_read_from_the_dimension_s_own_block(tmp_path):
    """A CAD author who lets the dimension display its measured value writes that number into the
    dimension block, not onto the modelspace entity - which is where all 20 dimensions of the
    LibreCAD sample carry their sizes. Reading only `dxf.text` sees an empty string and reports a
    drawing with no stated values at all."""
    from astracad_inspect.ingest.dxf import read_dxf
    bundle = read_dxf(shown_text_dim_doc(tmp_path, shown="42.43"))
    c = bundle.characteristics[0]
    assert c.raw_callout == "42.43", c.raw_callout
    assert c.nominal == 42.43
    assert c.state is not ProvenanceState.UNRESOLVED, c.unresolved_reason
    assert any("block" in h.detail for h in c.history), [h.detail for h in c.history]


def test_a_dimension_that_states_no_value_anywhere_is_not_counted_as_resolved(tmp_path):
    """An empty override used to be read as '<>' and counted as resolved with no nominal: the report
    then said 20 of 20 characteristics resolved while every row was blank. A dimension with neither an
    override nor rendered text in its block states nothing, and that is a gap to name."""
    from astracad_inspect.ingest.dxf import read_dxf
    from astracad_inspect.plan import build_plan
    bundle = read_dxf(shown_text_dim_doc(tmp_path, shown=None, name="states_nothing"))
    c = bundle.characteristics[0]
    assert c.nominal is None
    assert c.state is ProvenanceState.UNRESOLVED
    assert c.is_blocking
    plan = build_plan(bundle)
    assert plan.coverage.resolved == 0, plan.coverage
    assert not plan.approvable


def test_a_decimal_comma_callout_is_read_as_one_size_when_the_geometry_names_it(tmp_path):
    """A European CAD author writes 42.43 mm as '42,43'. Splitting that token and keeping the first
    half reports a 42 mm size that the drawing never stated, and leaving every row unresolved makes
    a legible drawing unusable. The drawing itself decides which reading is its own: 42.43 is the
    distance between the dimension's definition points to the two decimals it prints, while 4243 is
    a distance nothing on the sheet measures."""
    from astracad_inspect.ingest.dxf import read_dxf
    bundle = read_dxf(shown_text_dim_doc(tmp_path, shown="42,43", length=42.4264,
                                         points=((0, 0), (42.4264, 0)), name="comma_decimal"))
    c = bundle.characteristics[0]
    assert c.raw_callout == "42,43", c.raw_callout
    assert c.nominal == 42.43, c.nominal
    assert c.state is not ProvenanceState.UNRESOLVED, c.unresolved_reason
    check = next(k for k in c.checks if k.name == "geometry_cross_check")
    assert check.outcome is CheckOutcome.PASSED, check.why
    assert any("decimal comma" in h.detail for h in c.history), [h.detail for h in c.history]


def test_a_comma_callout_no_measured_geometry_supports_stays_ambiguous(tmp_path):
    """Confirming a reading needs the drawing to name it. '1,500' on a 42.43 mm span matches neither
    1.5 nor 1500, so both stay candidates and the row goes to an engineer instead of the build
    picking the likelier one."""
    from astracad_inspect.ingest.dxf import read_dxf
    bundle = read_dxf(shown_text_dim_doc(tmp_path, shown="1,500", name="comma_unsupported"))
    c = bundle.characteristics[0]
    assert c.state is ProvenanceState.UNRESOLVED
    assert c.is_blocking
    assert any("1.5" in h.detail and "1500" in h.detail for h in c.history), \
        [h.detail for h in c.history]


def test_a_callout_measured_on_another_projection_of_the_same_points_is_confirmed_and_flagged(
        tmp_path):
    """Two of the LibreCAD sample's dimensions are declared horizontal yet print the vertical distance
    between their own definition points (30 printed, 0 as declared). The picture does carry 30, so
    calling that a size disagreement would invent a defect - but the file's declared projection and
    its printed number are two different claims, and the row has to say which one was used."""
    from astracad_inspect.ingest.dxf import read_dxf
    bundle = read_dxf(shown_text_dim_doc(tmp_path, shown="30", name="other_projection",
                                         points=((0, 0), (0, 30)),
                                         measurement="horizontal"))
    c = bundle.characteristics[0]
    assert c.nominal == 30.0, c.nominal
    check = next(k for k in c.checks if k.name == "geometry_cross_check")
    assert check.outcome is CheckOutcome.PASSED, check.why
    assert any("projection" in h.detail for h in c.history), [h.detail for h in c.history]
    assert any(i.code == "geometry_projection_undocumented" and i.severity == "warning"
               for i in c.issues), [ (i.code, i.severity) for i in c.issues]
    assert not c.is_blocking


def test_an_untoleranced_callout_agrees_with_geometry_to_its_printed_resolution(tmp_path):
    """A dimension printed '42.43' states its size to two decimals and nothing else; the geometry it
    stands on measures 42.4264. Demanding agreement to a micron declares the drawing self-contradictory
    when its own displayed resolution is the only precision the author offered."""
    from astracad_inspect.ingest.dxf import read_dxf
    bundle = read_dxf(shown_text_dim_doc(tmp_path, shown="42.43", name="printed_resolution"))
    c = bundle.characteristics[0]
    check = next(k for k in c.checks if k.name == "geometry_cross_check")
    assert check.outcome is CheckOutcome.PASSED, check.why
    assert not c.is_blocking, [ (i.code, i.severity) for i in c.issues]


def test_an_untoleranced_callout_still_disagrees_beyond_its_printed_resolution(tmp_path):
    """The displayed precision bounds the confirmation, it does not cancel it: a sheet printing 42.50
    over a 42.4264 mm span contradicts itself by seven hundredths, which is fifteen times the half-unit
    it can show."""
    from astracad_inspect.ingest.dxf import read_dxf
    bundle = read_dxf(shown_text_dim_doc(tmp_path, shown="42.50", name="printed_resolution_breach"))
    c = bundle.characteristics[0]
    assert c.nominal == 42.5, c.nominal
    check = next(k for k in c.checks if k.name == "geometry_cross_check")
    assert check.outcome is CheckOutcome.FAILED, check.why
    assert any(i.code == "geometry_callout_disagreement" for i in c.issues), c.issues
    assert c.is_blocking
