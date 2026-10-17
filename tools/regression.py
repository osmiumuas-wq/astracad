"""Score the full inspection route against labelled drawings.

This is the reproducible test the acceptance gate asks for: every fixture runs end to end
(ingest -> parse -> balloon -> validate -> PDF/XLSX/JSON -> reopen-and-read), then the result is
compared against ground truth recorded *by construction* in the label file - not against the
engine's own output.

Every metric carries its dataset, its method and its limitations, because a number without those
three is not evidence. The fixtures are synthetic, so this measures the code path, not performance
on production engineering drawings.

Usage:  py tools/regression.py [--out DIR] [--json FILE]
Exit:   0 every declared target met, 1 a target was missed, 2 the harness could not run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astracad_inspect.model import CheckOutcome                     # noqa: E402
from astracad_inspect.pipeline import inspect_drawing               # noqa: E402
from astracad_inspect.provenance import sha256_file, tool_versions  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
NUM_TOL = 1e-6

METHOD = (
    "a label entry is matched to plan rows whose raw or normalised callout contains its "
    f"raw_contains string; 0 matches = missed, more than 1 = duplicate/ambiguous. The matched "
    f"row is then checked field by field (kind, nominal, limits, count, provenance state) with a "
    f"numeric tolerance of {NUM_TOL:g}. Expected limits are written in the label file as numbers "
    "taken from the drawing's construction, so the check does not reuse the engine's own "
    "arithmetic. Rows matching no label entry count as unsupported extractions (precision). "
    "Escalated free text is checked in both directions: a labelled escalation must not become a "
    "row, and an escalation the label did not record is a labelling gap rather than a pass. "
    "Export quality is the pipeline's reopen-and-read check count, measured by re-opening the "
    "artifacts with different libraries than wrote them."
)

LIMITATIONS = (
    "Both fixtures are SYNTHETIC and machine-generated (tools/make_fixtures.py). They cover "
    "linear, diameter, radius and angular characteristics, a limit pair, a repeated-feature "
    "count, an ISO 286-2 hole class resolved from the published deviation table, a fit pair that "
    "names two features and resolves neither, escalated free text and one deliberate "
    "callout/geometry contradiction - {n} labelled characteristics in total. Nothing here "
    "measures OCR, PDF or "
    "raster input, GD&T feature control frames, datums, thread callouts, surface symbols, "
    "multi-sheet drawings, or real production drawings. No number below may be quoted outside "
    "this dataset."
)


def limitations(per_fixture) -> str:
    """The stated limitations, carrying the count of labelled characteristics actually loaded -
    a limitation quoted with the wrong dataset size is not a limitation."""
    return LIMITATIONS.format(n=sum(b["expected_characteristics"] for b in per_fixture))


def _num_ok(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= NUM_TOL


def _tolerance_ok(sig: str, row) -> bool:
    """Interpret the label file's tolerance signature against the delivered row values."""
    parts = sig.split(":")
    if parts[0] == "none":
        return row.tolerance_text == "none stated" and row.minimum is None
    if parts[0] == "symmetric":
        pm = float(parts[1])
        return (row.nominal is not None and _num_ok(row.minimum, row.nominal - pm)
                and _num_ok(row.maximum, row.nominal + pm))
    if parts[0] == "deviations":
        upper, lower = float(parts[1]), float(parts[2])
        return (row.nominal is not None and _num_ok(row.maximum, row.nominal + upper)
                and _num_ok(row.minimum, row.nominal - lower))
    if parts[0] == "limit_pair":
        return _num_ok(row.minimum, float(parts[1])) and _num_ok(row.maximum, float(parts[2]))
    if parts[0] == "fit_class":
        # The label states the class and then either the published limits or "none", so a
        # resolution is scored against the table rather than against the engine's own output.
        if len(parts) not in (3, 4):
            raise ValueError(f"fit class label must be 'fit_class:CODE:LIMITS' or "
                             f"'fit_class:CODE:none', got {sig!r}")
        if parts[1] not in row.tolerance_text:
            return False
        if parts[2] == "none":
            return row.minimum is None and row.maximum is None
        return _num_ok(row.minimum, float(parts[2])) and _num_ok(row.maximum, float(parts[3]))
    if parts[0] == "geometric":
        # geometric:SYMBOL:ZONE:dia|-:MODIFIER|-:DATUM|DATUM...  — the frame is rebuilt from the
        # label's own fields, and the inspectable limits must be 0 .. zone.
        if len(parts) != 6:
            raise ValueError(f"geometric label must have 6 fields, got {sig!r}")
        _, symbol, zone, dia, mod, datums = parts
        cell = ("⌀" if dia == "dia" else "") + f"{float(zone):g}" + (f" ({mod})" if mod != "-" else "")
        want = " | ".join([symbol, cell, *[d for d in datums.split("|") if d]])
        return (row.tolerance_text == want and _num_ok(row.minimum, 0.0)
                and _num_ok(row.maximum, float(zone)))
    raise ValueError(f"label file uses an unknown tolerance signature {sig!r}")


def _ratio(num: int, den: int) -> float | None:
    return None if den == 0 else round(num / den, 6)


def score_label(label: dict, plan) -> dict:
    rows = list(plan.rows)
    matched_needles = [e["raw_contains"] for e in label.get("expected_characteristics", [])]

    missed: list[str] = []
    duplicated: list[str] = []
    wrong_fields: list[str] = []
    fields_checked = fields_correct = 0

    for entry in label.get("expected_characteristics", []):
        needle = entry["raw_contains"]
        hits = [r for r in rows if needle in r.raw_callout or needle in r.callout]
        if not hits:
            missed.append(needle)
            continue
        if len(hits) > 1:
            duplicated.append(f"{needle} -> {[h.cid for h in hits]}")
        row = hits[0]
        want_limits = entry.get("limits")
        checks = {
            "kind": row.kind == entry["kind"],
            "nominal": _num_ok(row.nominal, entry.get("nominal")),
            "tolerance": _tolerance_ok(entry["tolerance"], row),
            "limits": (_num_ok(row.minimum, None if want_limits is None else want_limits[0])
                       and _num_ok(row.maximum, None if want_limits is None else want_limits[1])),
            "count": row.count == entry.get("count", 1),
            "resolved": row.provenance == ("parsed" if entry["resolved"] else "unresolved"),
        }
        if "must_block" in entry:
            checks["blocking"] = bool(row.blocking) == bool(entry["must_block"])
        fields_checked += len(checks)
        fields_correct += sum(checks.values())
        bad = [k for k, ok in checks.items() if not ok]
        if bad:
            wrong_fields.append(f"{needle} ({row.cid}): {', '.join(bad)}")

    unsupported = [f"{r.cid}:{r.callout}" for r in rows
                   if not any(n in r.raw_callout or n in r.callout for n in matched_needles)]

    balloon_ok = (len({r.balloon_no for r in rows}) == len(rows)
                  and all(r.balloon_no is not None for r in rows)
                  and not plan.layout.unplaced
                  and len(plan.layout.balloons) == len(rows))

    escalation_failures = []
    for entry in label.get("expected_uninterpreted", []):
        frag = entry["text_contains"]
        became_row = any(frag in r.raw_callout or frag in r.callout for r in rows)
        was_escalated = any(frag in u.text for u in plan.uninterpreted)
        if became_row or not was_escalated:
            escalation_failures.append(f"{frag!r}: in_rows={became_row} escalated={was_escalated}")

    # The other direction: an escalation the label file forgot to record is a labelling gap, not
    # a passing result. Checked here so a label cannot quietly understate the drawing.
    listed = [e["text_contains"] for e in label.get("expected_uninterpreted", [])]
    unlabelled_escalations = [u.text for u in plan.uninterpreted
                              if not any(f in u.text for f in listed)]

    result = {
        "fixture": label["label"],
        "rows_in_plan": len(rows),
        "expected_characteristics": len(matched_needles),
        "missed": missed,
        "duplicate_label_matches": duplicated,
        "unsupported_rows": unsupported,
        "wrong_fields": wrong_fields,
        "extraction_recall": _ratio(len(matched_needles) - len(missed), len(matched_needles)),
        "strict_field_accuracy": _ratio(fields_correct, fields_checked),
        "precision": _ratio(len(rows) - len(unsupported), len(rows)),
        "balloon_mapping_complete": balloon_ok,
        "escalation_failures": escalation_failures,
        "unlabelled_escalations": unlabelled_escalations,
        "row_warnings": {c.cid: sorted({i.code for i in c.issues if i.severity == "warning"})
                         for c in plan.characteristics
                         if any(i.severity == "warning" for i in c.issues)},
        "plan_checks_failed": [c.name for c in plan.checks if c.outcome is CheckOutcome.FAILED],
        "plan_checks_not_verifiable": [c.name for c in plan.checks
                                       if c.outcome is CheckOutcome.NOT_VERIFIABLE],
        "approvable": plan.approvable,
        "blocking_rows": [r.cid for r in rows if r.blocking],
        "coverage": vars(plan.coverage),
    }

    if "expect_cross_check_failure_for" in label:
        geo = {c.cid: next((k for k in c.checks if k.name == "geometry_cross_check"), None)
               for c in plan.characteristics}
        blocking = set(result["blocking_rows"])

        def _geo_state(needle: str, want: CheckOutcome) -> bool:
            for r in rows:
                if needle in r.raw_callout or needle in r.callout:
                    check = geo.get(r.cid)
                    return (check is not None and check.outcome is want
                            and (r.cid in blocking) == (want is CheckOutcome.FAILED))
            return False

        result["contradiction_blocked"] = _geo_state(label["expect_cross_check_failure_for"],
                                                     CheckOutcome.FAILED)
        result["neighbour_passed"] = _geo_state(label["expect_cross_check_pass_for"],
                                                CheckOutcome.PASSED)
    return result


TARGETS = {"extraction_recall": 1.0, "strict_field_accuracy": 1.0, "precision": 1.0}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "_out_regression"))
    ap.add_argument("--json", default=str(ROOT / "_out" / "regression_report.json"))
    args = ap.parse_args()

    labels = sorted(FIXTURES.glob("*.expected.json"))
    if not labels:
        print("NO FIXTURES - nothing measured", file=sys.stderr)
        return 2

    per_fixture = []
    for lp in labels:
        label = json.loads(lp.read_text(encoding="utf-8"))
        dxf = lp.with_name(lp.name[: -len(".expected.json")] + ".dxf")
        if not dxf.exists():
            print(f"MISSING DRAWING for {lp.name}", file=sys.stderr)
            return 2
        run = inspect_drawing(dxf, args.out)
        block = score_label(label, run.plan)
        block.update(
            source_document=str(dxf),
            source_sha256=sha256_file(dxf),
            label_sha256=sha256_file(lp),
            synthetic=bool(label.get("synthetic")),
            export_checks_passed=run.verification_passed,
            export_checks_total=len(run.verification),
            artifacts=sorted(run.artifacts),
            delivered=run.delivered,
        )
        per_fixture.append(block)

    targets_met = True
    for block in per_fixture:
        for name, want in TARGETS.items():
            if block[name] != want:
                targets_met = False
        if not (block["balloon_mapping_complete"] and not block["escalation_failures"]
                and not block["unlabelled_escalations"]
                and not block["duplicate_label_matches"]
                and block["export_checks_passed"] == block["export_checks_total"]):
            targets_met = False
        if "contradiction_blocked" in block and not (block["contradiction_blocked"]
                                                     and block["neighbour_passed"]):
            targets_met = False

    report = {
        "metrics": per_fixture,
        "aggregate": {
            "fixtures": len(per_fixture),
            "expected_characteristics": sum(b["expected_characteristics"] for b in per_fixture),
            "rows": sum(b["rows_in_plan"] for b in per_fixture),
            "missed": sum(len(b["missed"]) for b in per_fixture),
            "unsupported_rows": sum(len(b["unsupported_rows"]) for b in per_fixture),
            "wrong_fields": sum(len(b["wrong_fields"]) for b in per_fixture),
            "export_checks_passed": sum(b["export_checks_passed"] for b in per_fixture),
            "export_checks_total": sum(b["export_checks_total"] for b in per_fixture),
            "targets_met": targets_met,
        },
        "dataset": {
            "fixtures": [Path(b["source_document"]).name for b in per_fixture],
            "sha256": {Path(b["source_document"]).name: b["source_sha256"] for b in per_fixture},
            "synthetic": all(b["synthetic"] for b in per_fixture),
        },
        "method": METHOD,
        "limitations": limitations(per_fixture),
        "tool_versions": tool_versions(),
        "exit_code_meaning": "0 = every declared target met on this dataset; 1 = a target missed; "
                             "2 = harness could not run",
    }
    out_json = Path(args.json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
                        encoding="utf-8")

    print(f"DATASET    {len(per_fixture)} labelled fixture(s): "
          + ", ".join(Path(b["source_document"]).name for b in per_fixture))
    print("           all synthetic - machine-generated, not real parts")
    print(f"METHOD     {METHOD}")
    print(f"LIMITS     {limitations(per_fixture)}")
    print("-" * 62)
    for b in per_fixture:
        print(f"{b['fixture']} ({b['rows_in_plan']} rows, sha256:{b['source_sha256'][:12]})")
        print(f"  extraction_recall     {b['extraction_recall']}  missed={b['missed']}")
        print(f"  strict_field_accuracy {b['strict_field_accuracy']}  wrong={b['wrong_fields']}")
        print(f"  precision             {b['precision']}  unsupported={b['unsupported_rows']}")
        print(f"  duplicate_label_matches {b['duplicate_label_matches']}")
        print(f"  balloon_mapping_complete {b['balloon_mapping_complete']}  "
              f"escalation_failures={b['escalation_failures']}  "
              f"unlabelled_escalations={b['unlabelled_escalations']}")
        print(f"  row_warnings          {b['row_warnings']}")
        print(f"  plan_checks_failed    {b['plan_checks_failed']}  "
              f"not_verifiable={b['plan_checks_not_verifiable']}")
        print(f"  approvable            {b['approvable']}  blocking_rows={b['blocking_rows']}")
        print(f"  export reopen checks  {b['export_checks_passed']}/"
              f"{b['export_checks_total']}  delivered={b['delivered']}")
        if "contradiction_blocked" in b:
            print(f"  contradiction_blocked {b['contradiction_blocked']}  "
                  f"neighbour_passed={b['neighbour_passed']}")
    print("-" * 62)
    print(f"AGGREGATE  {json.dumps(report['aggregate'], ensure_ascii=False)}")
    print(f"REPORT     {out_json}")
    print(f"RESULT     {'targets met' if targets_met else 'TARGETS NOT MET'} on a synthetic "
          "dataset - see LIMITATIONS above")
    return 0 if targets_met else 1


if __name__ == "__main__":
    raise SystemExit(main())
