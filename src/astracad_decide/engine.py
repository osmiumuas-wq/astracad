"""Turn one inspection plan into one decision record.

The rules are the whole product, so they are written as a truth table and tested one row at a
time. Per characteristic, in this order - the first that applies wins:

    rejected by a reviewer          -> REJECT
    an independent check failed     -> HOLD      (two sources disagree; a person arbitrates)
    present but not interpreted     -> NOT_JUDGEABLE
    no definite limits              -> NOT_JUDGEABLE  (recordable, never pass/fail)
    otherwise                       -> ACCEPT

Per plan:

    a blocking contradiction        -> HOLD
    every characteristic accepted   -> RELEASE
    every characteristic unusable   -> REJECT   (the source cannot support inspection)
    nothing that could be judged    -> NOT_EVALUABLE
    otherwise                       -> HOLD

The plan's own gate is not second-guessed. Where the plan says "releasable" but an
independent check failed, the record says HOLD and names the disagreement - the gate trusts its
checks, and this layer reads the same checks as evidence rather than as a verdict.
"""
from __future__ import annotations

import re
from pathlib import Path

from .model import (
    ACCEPT, EVIDENCE_WEIGHTS, HOLD, MIN_OUTCOMES, MIN_OUTCOMES_PER_KIND, NOT_EVALUABLE,
    NOT_JUDGEABLE, REJECT, RELEASE, Confidence, Contradiction, Decision, EvidenceNode,
    Unknown, _now, decision_id,
)

CROSS_CHECK = "geometry_cross_check"


def _norm(text) -> str:
    """Compare readings as a person would: spacing and case are not disagreements."""
    return re.sub(r"\s+", "", str(text)).upper()


def _kind_of_check(name: str) -> str:
    if name.startswith(CROSS_CHECK):
        return "geometry_cross_check"
    if name.startswith(("balloon", "every_characteristic", "annotations_")):
        return "layout_check"
    return "source_parse"


def _kind_of_source(source: str) -> str:
    s = source.lower()
    if s.startswith("ocr"):
        return "ocr_engine"
    if s.startswith("title"):
        return "title_block"
    if s.startswith(("model", "ai", "llm")):
        return "model_proposal"
    return "source_parse"


def _weights(overrides: dict | None) -> dict[str, float]:
    w = dict(EVIDENCE_WEIGHTS)
    for kind, got in (overrides or {}).items():
        if kind in w and isinstance(got, (int, float)) and 0.0 <= got <= 1.0:
            w[kind] = float(got)
    return w


# --------------------------------------------------------------------------- evidence
def _char_evidence(c: dict, w: dict[str, float]) -> list[EvidenceNode]:
    nodes: list[EvidenceNode] = []
    cid = c.get("cid", "?")
    for ch in c.get("checks") or []:
        name = ch.get("name", "check")
        outcome = ch.get("outcome")
        kind = _kind_of_check(name)
        stance = {"passed": "supports", "failed": "opposes"}.get(outcome, "silent")
        shown = f"{ch.get('expected')} / {ch.get('actual')}"
        nodes.append(EvidenceNode(
            nid=f"ev:{name}:{cid}", kind=kind, origin="astracad_inspect.plan",
            claim=f"{name} on {cid}: expected {ch.get('expected')}, got {ch.get('actual')}",
            stance=stance, weight=w.get(kind, 0.5),
            value={"expected": ch.get("expected"), "actual": ch.get("actual"),
                   "delta": ch.get("delta"), "outcome": outcome, "shown": shown}))
    state = c.get("state", "extracted")
    if state in ("unresolved", "rejected"):
        nodes.append(EvidenceNode(
            nid=f"ev:state:{cid}", kind="source_parse", origin="astracad_inspect.plan",
            claim=f"{cid} is {state}: the source text was not turned into an engineering claim",
            stance="opposes", weight=w["source_parse"], value={"state": state}))
    review = c.get("review")
    if review in ("approved", "verified"):
        nodes.append(EvidenceNode(
            nid=f"ev:review:{cid}", kind="human_review", origin="astracad_inspect.plan",
            claim=f"{cid} was {review} by a named reviewer", stance="supports",
            weight=w["human_review"], value={"review": review}))
    elif review == "rejected":
        nodes.append(EvidenceNode(
            nid=f"ev:review:{cid}", kind="human_review", origin="astracad_inspect.plan",
            claim=f"{cid} was rejected by a named reviewer", stance="opposes",
            weight=w["human_review"], value={"review": review}))
    return nodes


def _plan_evidence(plan: dict, w: dict[str, float]) -> list[EvidenceNode]:
    nodes: list[EvidenceNode] = []
    for ch in plan.get("checks") or []:
        name = ch.get("name", "check")
        outcome = ch.get("outcome")
        kind = _kind_of_check(name)
        stance = {"passed": "supports", "failed": "opposes"}.get(outcome, "opposes")
        nodes.append(EvidenceNode(
            nid=f"ev:{name}:plan", kind=kind, origin="astracad_inspect.plan",
            claim=f"{name}: expected {ch.get('expected')}, got {ch.get('actual')}",
            stance=stance, weight=w.get(kind, 0.5),
            value={k: ch.get(k) for k in ("expected", "actual", "why", "delta")}
                  | {"outcome": outcome}))
    for ch in plan.get("accepted_exceptions") or []:
        name = ch.get("name", "exception")
        nodes.append(EvidenceNode(
            nid=f"ev:waiver:{name}", kind="human_waiver", origin="astracad_inspect.plan",
            claim=f"{name} was accepted as an exception by a reviewer: the disagreement stands",
            stance="waived", weight=w["human_waiver"],
            value={"expected": ch.get("expected"), "actual": ch.get("actual")}))
    return nodes


def _source_evidence(sources: dict | None, w: dict[str, float]) -> list[EvidenceNode]:
    """One node per (characteristic, source) reading, so a disagreement is attributable."""
    if not sources:
        return []
    readings = sources.get("readings", sources)
    out: list[EvidenceNode] = []
    for cid, by_source in readings.items():
        if not isinstance(by_source, dict):
            continue
        for source, text in by_source.items():
            kind = _kind_of_source(str(source))
            out.append(EvidenceNode(
                nid=f"ev:src:{source}:{cid}", kind=kind, origin=f"source:{source}",
                claim=f"{source} reads {cid} as {text!r}", stance="supports",
                weight=w.get(kind, 0.5), value={"text": str(text), "cid": cid}))
    return out


# ------------------------------------------------------------------ contradictions
def _contradictions(plan: dict, sources: dict | None) -> list[Contradiction]:
    out: list[Contradiction] = []
    for c in plan.get("characteristics") or []:
        cid = c.get("cid", "?")
        for ch in c.get("checks") or []:
            if ch.get("outcome") != "failed":
                continue
            out.append(Contradiction(
                code="VALUE_VS_GEOMETRY",
                severity="blocking",
                subject=cid,
                message=(f"{c.get('raw_callout', cid)!r} states {ch.get('expected')} but the "
                         f"geometry on the sheet measures {ch.get('actual')} "
                         f"({ch.get('name')})"),
                a=f"ev:{ch.get('name')}:{cid}", b=f"ev:state:{cid}"))
        for issue in c.get("issues") or []:
            if issue.get("code") == "possible_duplicate_callout":
                out.append(Contradiction(
                    code="DUPLICATE_CALLOUT", severity="warning", subject=cid,
                    message=issue.get("message", "the same callout appears more than once")))
    if sources:
        readings = sources.get("readings", sources)
        for cid, by_source in readings.items():
            if not isinstance(by_source, dict) or len(by_source) < 2:
                continue
            values = {_norm(v): src for src, v in by_source.items()}
            if len(values) > 1:
                shown = "; ".join(f"{src} {by_source[src]!r}" for src in by_source)
                out.append(Contradiction(
                    code="SOURCE_DISAGREEMENT", severity="blocking", subject=cid,
                    message=f"sources do not agree on {cid}: {shown}",
                    a=f"ev:src:{next(iter(by_source))}:{cid}"))
    return out


# ----------------------------------------------------------------------- absences
def _unknowns(plan: dict) -> list[Unknown]:
    out: list[Unknown] = []
    for c in plan.get("characteristics") or []:
        cid = c.get("cid", "?")
        if c.get("limits") is None:
            out.append(Unknown(
                code="no_limits", subject=cid,
                message=(f"{c.get('raw_callout', cid)!r} carries no definite limit: the value "
                         "can be recorded but never passed or failed"),
                blocks="PASS/FAIL", severity="blocking"))
        if c.get("state") == "unresolved":
            out.append(Unknown(
                code="uninterpreted_characteristic", subject=cid,
                message="the callout text is on the sheet but was not turned into a limit pair",
                blocks="inspection row", severity="blocking"))
    for u in plan.get("uninterpreted") or []:
        out.append(Unknown(
            code="uninterpreted_annotation", subject=str(u.get("entity_handle", "")) or "sheet",
            message=(f"annotation {u.get('text', '')!r} was escalated, not interpreted "
                     f"({u.get('reason', 'no reason recorded')})"),
            blocks="coverage", severity="warning"))
    unplaced = (plan.get("layout") or {}).get("unplaced") or []
    if unplaced:
        out.append(Unknown(
            code="unplaced_balloon", subject="sheet",
            message=f"{len(unplaced)} balloon(s) could not be placed inside the drawing content",
            blocks="release", severity="blocking"))
    rev_source = str(plan.get("revision_source", ""))
    if rev_source.lower().startswith("default"):
        out.append(Unknown(
            code="revision_not_stated", subject="title block",
            message="the drawing states no revision; the record cannot tie results to one",
            blocks="traceability", severity="warning"))
    if not plan.get("characteristics"):
        out.append(Unknown(
            code="no_characteristics", subject="document",
            message="no dimension was extracted from this file", blocks="any ruling",
            severity="blocking"))
    return out


# ------------------------------------------------------------------------- rulings
def _item_ruling(c: dict) -> tuple[str, str]:
    cid = c.get("cid", "?")
    checks = c.get("checks") or []
    failed = [ch for ch in checks if ch.get("outcome") == "failed"]
    if c.get("review") == "rejected" or c.get("state") == "rejected":
        return REJECT, f"a reviewer rejected {cid}: it is not part of the released plan"
    if failed:
        ch = failed[0]
        return HOLD, (f"{ch.get('name')} disagrees with the callout "
                      f"({ch.get('expected')} vs {ch.get('actual')}): a person must arbitrate")
    if c.get("state") == "unresolved":
        return NOT_JUDGEABLE, "the callout was not interpreted, so there is nothing to inspect"
    if c.get("limits") is None:
        return NOT_JUDGEABLE, ("no definite limits, so a measurement here could be recorded "
                               "but never judged")
    if c.get("review") in ("approved", "verified"):
        return ACCEPT, f"{cid} was {c.get('review')} by a named reviewer"
    return ACCEPT, "resolved, cross-checked and ballooned; not yet human-verified"


def _plan_ruling(plan: dict, items: list[dict], contradictions: list[Contradiction],
                 unknowns: list[Unknown]) -> tuple[str, str]:
    blocking = [c for c in contradictions if c.severity == "blocking"]
    chars = plan.get("characteristics") or []
    if not chars:
        return NOT_EVALUABLE, ("the file was read but carries no characteristic: there is no "
                               "ruling to make")
    if all(i["disposition"] in (REJECT, NOT_JUDGEABLE) for i in items):
        return REJECT, ("every characteristic is unusable: the source cannot support an "
                        "inspection and goes back to its author")
    if blocking:
        return HOLD, (f"{len(blocking)} disagreement(s) between independent sources; release "
                      "is withheld until a person arbitrates")
    if plan.get("approvable"):
        silent = [i for i in items if i["disposition"] == NOT_JUDGEABLE]
        note = ("the plan gate is open: nothing blocked, nothing unplaced, no open check")
        if silent:
            note += (f"; {len(silent)} callout(s) cannot be judged and are listed as such "
                     "rather than passed")
        return RELEASE, note
    reasons = []
    gate = plan.get("gate")
    if gate:
        reasons.append(str(gate))
    if not reasons:
        reasons.append("the plan gate is not open")
    return HOLD, "release withheld - " + "; ".join(reasons)


def _confidence(nodes: list[EvidenceNode], disposition: str,
                items: list[dict], unknowns: list[Unknown],
                summary: dict | None) -> Confidence:
    """The floor over the evidence the ruling rests on, and the calibrated number when earned."""
    decisive = [n for n in nodes if n.stance == ("supports" if disposition == RELEASE
                                                 else "opposes")]
    if disposition in (HOLD, REJECT, NOT_EVALUABLE) and unknowns:
        # An absence is evidence too; it has no weight, so it can only set the note.
        pass
    floor, bounded_by = None, None
    if decisive:
        weakest = min(decisive, key=lambda n: (n.weight, n.nid))
        floor, bounded_by = weakest.weight, weakest.nid
    decision_hist = (summary or {}).get("decision", {})
    confirmed = int(decision_hist.get("confirmed", 0))
    overturned = int(decision_hist.get("overturned", 0))
    n = confirmed + overturned
    calibrated = None
    if n >= MIN_OUTCOMES:
        calibrated = round((confirmed + 1) / (n + 2), 4)
    if decisive:
        note = (f"bounded by {bounded_by}, the weakest of {len(decisive)} decisive evidence "
                f"node(s)")
    elif unknowns:
        note = ("rests on absence only: " + ", ".join(sorted({u.code for u in unknowns})))
    else:
        note = "no decisive evidence recorded"
    if calibrated is None:
        note += (f"; calibrated value withheld - {n} outcome(s) recorded, {MIN_OUTCOMES} "
                 "needed before a frequency is called a confidence")
    else:
        note += f"; calibrated from {n} recorded outcome(s)"
    return Confidence(method="calibrated" if calibrated is not None else "weakest_link",
                      floor=None if floor is None else round(floor, 4),
                      calibrated=calibrated, n=n, bounded_by=bounded_by, note=note)


def _alternatives(plan: dict, disposition: str, contradictions: list[Contradiction],
                  items: list[dict]) -> list[dict]:
    out = []
    blocking = [c for c in contradictions if c.severity == "blocking"]
    if disposition != RELEASE:
        why = []
        bad = [i for i in items if i["disposition"] != ACCEPT]
        if bad:
            why.append(f"{len(bad)} characteristic(s) not accepted")
        if blocking:
            why.append(f"{len(blocking)} blocking contradiction(s)")
        if not plan.get("approvable"):
            why.append("the plan gate is not open")
        out.append({"disposition": RELEASE,
                    "why_not": "; ".join(why) or "nothing to release"})
    else:
        out.append({"disposition": HOLD,
                    "why_not": "holding would delay a plan whose every row passed its checks"})
    if disposition != REJECT:
        out.append({"disposition": REJECT,
                    "why_not": ("the findings are fixable by a named person; rejecting would "
                                "discard a usable source")})
    return out


def _subjects(values: list[str], limit: int = 6) -> str:
    shown = ", ".join(values[:limit])
    return shown + (f" and {len(values) - limit} more" if len(values) > limit else "")


def _next_actions(plan: dict, contradictions: list[Contradiction],
                  unknowns: list[Unknown], items: list[dict]) -> list[str]:
    """The smallest interventions that would change the ruling, most decisive first.

    Grouped by cause, not by row: eleven characteristics with no tolerance is one change to make
    on the drawing, not eleven, and a list of seventeen steps is not a next step.
    """
    acts: list[str] = []
    for c in contradictions:
        if c.severity == "blocking":
            acts.append(f"arbitrate {c.subject}: {c.message}")
    by_code: dict[str, list[str]] = {}
    for u in unknowns:
        by_code.setdefault(u.code, []).append(u.subject)
    if "unplaced_balloon" in by_code:
        acts.append("place the outstanding balloon(s) inside the drawing content")
    if "revision_not_stated" in by_code:
        acts.append("print the revision in the title block so results can be tied to it")
    if "no_characteristics" in by_code:
        acts.append("check the file: no dimension was extracted from it at all")
    if "no_limits" in by_code:
        subs = by_code["no_limits"]
        acts.append(f"state a tolerance on {len(subs)} callout(s) ({_subjects(subs)}), "
                    "or mark them basic / reference")
    if "uninterpreted_characteristic" in by_code:
        subs = by_code["uninterpreted_characteristic"]
        acts.append(f"re-enter {_subjects(subs)} by hand: the parser could not read it")
    if "uninterpreted_annotation" in by_code:
        acts.append(f"escalate {len(by_code['uninterpreted_annotation'])} uninterpreted "
                    "annotation(s) to a person")
    for ch in plan.get("checks") or []:
        if ch.get("outcome") != "passed":
            acts.append(f"close {ch.get('name')} (expected {ch.get('expected')}, "
                        f"got {ch.get('actual')})")
    if not acts:
        acts.append("none: the ruling would not change without new evidence")
    return acts


def evaluate(plan: dict, *, actor: str, sources: dict | None = None,
             summary: dict | None = None, store_reliability: dict | None = None,
             now: str | None = None) -> Decision:
    """Rule on one plan and return the record. Raises ValueError on something it cannot read."""
    if not isinstance(plan, dict):
        raise ValueError("plan must be the JSON object written by astracad_inspect")
    if not actor or not str(actor).strip():
        raise ValueError("a decision needs a named actor: an unattributed ruling is not a record")
    sha = str(plan.get("source_sha256") or "")
    if not sha:
        raise ValueError("plan has no source_sha256: the ruling could not be tied to a file")

    w = _weights(store_reliability)
    nodes = _plan_evidence(plan, w)
    for c in plan.get("characteristics") or []:
        nodes.extend(_char_evidence(c, w))
    nodes.extend(_source_evidence(sources, w))

    items = []
    for c in plan.get("characteristics") or []:
        ruling, because = _item_ruling(c)
        # A row can be judged and still carry a caveat from the plan (a value exactly on its
        # limit, a projection flag that disagrees with the printed number). Those notes belong on
        # the row: a record that shows only the verdict teaches the reader to trust it blindly.
        notes = [str(i.get("message")) for i in (c.get("issues") or [])
                 if str(i.get("severity")) == "warning" and i.get("message")
                 and i.get("code") != "no_limits"][:2]
        items.append({"cid": c.get("cid", "?"), "kind": c.get("kind", "unknown"),
                      "callout": c.get("raw_callout", ""), "disposition": ruling,
                      "because": because, "notes": notes})

    contradictions = _contradictions(plan, sources)
    unknowns = _unknowns(plan)
    disposition, rationale = _plan_ruling(plan, items, contradictions, unknowns)
    confidence = _confidence(nodes, disposition, items, unknowns, summary)

    return Decision(
        decision_id=decision_id(sha, disposition, items, contradictions, unknowns),
        created_utc=now or _now(),
        actor=str(actor).strip(),
        question="May this inspection plan be released, and what is each callout's ruling?",
        subject={"document": Path(str(plan.get("source_document", ""))).name,
                 "sha256": sha, "revision": plan.get("revision", ""),
                 "revision_stated": not str(plan.get("revision_source", ""))
                 .lower().startswith("default"),
                 "unit": plan.get("unit", ""),
                 "approvable": bool(plan.get("approvable")),
                 "characteristics": len(plan.get("characteristics") or [])},
        disposition=disposition,
        items=items,
        evidence=nodes,
        contradictions=contradictions,
        unknowns=unknowns,
        confidence=confidence,
        alternatives=_alternatives(plan, disposition, contradictions, items),
        next_actions=_next_actions(plan, contradictions, unknowns, items),
        rationale=rationale,
        policy={"evidence_weights": w, "min_outcomes": MIN_OUTCOMES,
                "min_outcomes_per_kind": MIN_OUTCOMES_PER_KIND},
    )