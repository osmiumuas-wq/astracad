"""Decision records: what was ruled, on what evidence, with what confidence, and later
whether it held.

The inspection plan already computes a release gate and a list of checks. This package does
not recompute either of them; it captures the ruling as a first-class object so that

  * the reason a drawing was released can be re-read months later, by someone who was not
    there, from the record alone;
  * evidence that disagrees is refused rather than averaged away;
  * what is *missing* is written down next to what is present, because an inspection plan
    that hides its gaps looks exactly like a complete one;
  * the confidence stated at decision time can be compared with what actually happened, and
    the sources that turned out to be wrong lose weight for the next decision.

Every record is hash-chained to the one before it. Editing any field of any record breaks the
chain at that record, which is what makes the file a record rather than a log.

Two levels of uncertainty are kept apart on purpose, following the rule already stated in
`astracad_inspect.model`: provenance states are discrete claims and are never collapsed into
one number. The *fact* keeps its state (extracted, parsed, verified, human-approved). The
*decision* carries one number, it is named, it is bounded by the weakest supporting evidence,
and it is withheld entirely until enough outcomes exist to mean anything.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# The four rulings a record can carry. They are about a *plan*, not about a part:
#   RELEASE        every characteristic resolved, cross-checked and ballooned; nothing open
#   HOLD           something a person can fix. Release is withheld, not refused
#   REJECT         the source cannot support inspection at all: it goes back to its author
#   NOT_EVALUABLE  there is nothing here to rule on - the file was read, but it carries no
#                  characteristic that could be judged
RELEASE = "RELEASE"
HOLD = "HOLD"
REJECT = "REJECT"
NOT_EVALUABLE = "NOT_EVALUABLE"
DISPOSITIONS = (RELEASE, HOLD, REJECT, NOT_EVALUABLE)

# Per-characteristic dispositions (the trace is per callout, not only per sheet).
ACCEPT = "ACCEPT"
NOT_JUDGEABLE = "NOT_JUDGEABLE"

# Evidence priors. Each is "how much this kind of evidence has earned the right to be
# believed, before this record has any outcomes of its own". They are stated, not fitted, and
# they are stored in every record so a record can be re-read with the weights it was decided
# under. `outcome.reliability` replaces them with what the ledger actually observed.
EVIDENCE_WEIGHTS = {
    "geometry_cross_check": 0.90,   # arithmetic on definition points, not parsing of text
    "limits_derived": 0.85,         # ISO 286 / stated limits re-derived by a second route
    "human_review": 0.95,           # a named person read this row
    "layout_check": 0.80,           # balloons placed, unique, non-overlapping
    "title_block": 0.75,            # revision, unit, part number as printed
    "source_parse": 0.70,           # the callout text, read by deterministic rules
    "ocr_engine": 0.60,             # a scan read by OCR; two engines must agree
    "human_waiver": 0.50,           # a person accepted a known disagreement - not a pass
    "model_proposal": 0.40,         # a local model suggested an interpretation
}

# How many outcomes a decision class needs before a frequency is called a confidence. Below
# it the record states the floor and says what is missing, in the same way `measure` labels
# Cp/Cpk with its sample count instead of calling two parts a capability study.
MIN_OUTCOMES = 5
# Same idea per evidence kind: three outcomes before a source's weight is moved.
MIN_OUTCOMES_PER_KIND = 3

GENESIS = "0" * 64


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical(obj) -> str:
    """One byte sequence per meaning: sorted keys, no incidental whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceNode:
    """One thing that argues for or against a ruling.

    `stance` is deliberately not a score: "supports", "opposes", "silent" (the check could
    not be performed) and "waived" (a person took responsibility for a disagreement) are
    different claims. A waiver is not a pass and never counts as one.
    """

    nid: str
    kind: str
    origin: str
    claim: str
    stance: str
    weight: float
    value: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Contradiction:
    """Two pieces of evidence that cannot both be true, kept side by side."""

    code: str
    severity: str          # "blocking" | "warning"
    subject: str
    message: str
    a: str | None = None   # evidence node ids, when both sides are attributable
    b: str | None = None


@dataclass(frozen=True)
class Unknown:
    """Something the source does not say, and the ruling it therefore cannot support."""

    code: str
    subject: str
    message: str
    blocks: str
    severity: str = "warning"


@dataclass(frozen=True)
class Confidence:
    """The number, or the honest absence of one.

    `floor` is not a probability of being right. It is the reliability of the weakest piece of
    evidence the ruling stands on - a ruling is only as strong as its weakest link, and
    averaging would hide exactly the node nobody checked. `calibrated` is the observed
    frequency of this ruling holding up, and stays None until MIN_OUTCOMES outcomes exist.
    """

    method: str                    # "weakest_link" | "calibrated"
    floor: float | None
    calibrated: float | None
    n: int
    bounded_by: str | None         # evidence node that sets the floor
    note: str


@dataclass
class Decision:
    """A ruling on one plan, with everything needed to re-read it."""

    decision_id: str
    created_utc: str
    actor: str
    question: str
    subject: dict                  # document, sha256, revision, unit
    disposition: str
    items: list[dict]              # one row per characteristic: cid, callout, ruling, because
    evidence: list[EvidenceNode]
    contradictions: list[Contradiction]
    unknowns: list[Unknown]
    confidence: Confidence
    alternatives: list[dict]       # the rulings that were not taken, and why
    next_actions: list[str]        # the smallest changes that would change the ruling
    rationale: str
    policy: dict                   # weights and thresholds this ruling was made under
    prev_hash: str = GENESIS
    hash: str = ""

    def payload(self) -> dict:
        d = asdict(self)
        d.pop("hash", None)
        return d

    def to_dict(self) -> dict:
        return asdict(self)

    def digest(self) -> str:
        return sha256_text(canonical(self.payload()))


def decision_id(subject_sha: str, disposition: str, items: list[dict],
                contradictions: list[Contradiction], unknowns: list[Unknown]) -> str:
    """Deterministic: the same plan, ruled the same way, gets the same id.

    Created time and chain position are excluded on purpose - re-running the engine on an
    unchanged plan must recognise the ruling, not mint a second one that looks like a
    revision.
    """
    seed = canonical({
        "subject": subject_sha,
        "disposition": disposition,
        "items": [[i["cid"], i["disposition"]] for i in items],
        "contradictions": [[c.code, c.subject] for c in contradictions],
        "unknowns": [[u.code, u.subject] for u in unknowns],
    })
    return "d-" + sha256_text(seed)[:12]


class DecisionStore:
    """Append-only JSONL of decisions, each hashed over the one before it.

    The file is the record. Nothing is rewritten in place: a correction is a new decision that
    says what it supersedes.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    # -- reading ---------------------------------------------------------
    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as ex:
                raise ValueError(f"{self.path.name} line {n}: not JSON ({ex.msg})") from ex
        return out

    def tail_hash(self) -> str:
        recs = self.records()
        return recs[-1]["hash"] if recs else GENESIS

    # -- writing ---------------------------------------------------------
    def append(self, decision: Decision) -> Decision:
        decision.prev_hash = self.tail_hash()
        decision.hash = decision.digest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(canonical(decision.to_dict()) + "\n")
        return decision

    # -- verification ----------------------------------------------------
    def verify(self, plan_sha256: str | None = None) -> list[str]:
        """Return every defect found; empty means the chain holds.

        Checked: each record re-hashes to its stored hash, each link names its predecessor,
        ids are reproducible from their own content, and - when a plan is supplied - at least
        one record was decided on exactly this file.
        """
        defects: list[str] = []
        recs = self.records()
        if not recs:
            return [f"{self.path.name}: no records"]
        prev = GENESIS
        for i, rec in enumerate(recs):
            where = f"record {i + 1} ({rec.get('decision_id', '?')})"
            body = {k: v for k, v in rec.items() if k != "hash"}
            want = sha256_text(canonical(body))
            if rec.get("hash") != want:
                defects.append(f"{where}: hash does not recompute - the record was edited")
            if rec.get("prev_hash") != prev:
                defects.append(f"{where}: does not chain to the record before it")
            items = rec.get("items") or []
            contras = rec.get("contradictions") or []
            unknows = rec.get("unknowns") or []
            got = decision_id(rec.get("subject", {}).get("sha256", ""),
                              rec.get("disposition", ""), items,
                              [Contradiction(**c) for c in contras],
                              [Unknown(**u) for u in unknows])
            if rec.get("decision_id") != got:
                defects.append(f"{where}: id does not derive from its own content")
            prev = rec.get("hash", "")
        if plan_sha256 is not None:
            if not any(r.get("subject", {}).get("sha256") == plan_sha256 for r in recs):
                defects.append("no record in this file was decided on the supplied plan")
        return defects