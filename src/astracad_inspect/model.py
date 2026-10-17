"""Canonical engineering schema: the characteristic record and its provenance.

Every important fact carries its origin. Provenance states are deliberately discrete and are
never collapsed into one confidence number: "the parser read this" and "an engineer approved
this" are different claims with different consequences.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone


class ProvenanceState(str, enum.Enum):
    USER_SPECIFIED = "user_specified"
    EXTRACTED = "extracted"          # read out of the source artifact
    PARSED = "parsed"                # callout text interpreted by deterministic rules
    CALCULATED = "calculated"        # derived by a tested numeric function
    AI_PROPOSED = "ai_proposed"      # a model suggested it; not engineering truth
    VERIFIED = "verified"            # independent check passed
    HUMAN_APPROVED = "human_approved"
    UNRESOLVED = "unresolved"        # present in source, engine could not interpret it
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class CharKind(str, enum.Enum):
    LINEAR = "linear"
    DIAMETER = "diameter"
    RADIUS = "radius"
    ANGULAR = "angular"
    GEOMETRIC = "geometric"        # a GD&T feature control frame row
    UNKNOWN = "unknown"


class ReviewStatus(str, enum.Enum):
    DETECTED = "detected"
    NEEDS_REVIEW = "needs_review"
    VERIFIED = "verified"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class CheckOutcome(str, enum.Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_VERIFIABLE = "not_verifiable"


@dataclass(frozen=True)
class Check:
    """One independent verification result. `expected` must come from somewhere other than
    the thing being checked, or the check certifies nothing."""
    name: str
    outcome: CheckOutcome
    expected: str
    actual: str
    why: str | None = None
    delta: float | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class HistoryEvent:
    at: str
    actor: str            # "extractor", "override:<file>", or a named person
    action: str
    detail: str


@dataclass
class SourceLocation:
    """Where a characteristic came from. The raw callout plus this is enough to put an
    overlay marker back on the original drawing."""
    document: str
    document_sha256: str
    revision: str
    page: int
    entity_handle: str
    entity_type: str
    layer: str
    # Anchor and definition points in drawing units (mm after normalisation).
    anchor_x: float
    anchor_y: float
    definition_points: list[tuple[float, float]] = field(default_factory=list)
    zone: str = ""          # sheet grid reference (e.g. "C4") from the sheet's own border labels


# --- tolerance representations -------------------------------------------------------------
# These are kept as distinct types because "53 +0.01/-0.02" and "53.01/52.98" and "50 H7"
# are different statements. A single (min,max) pair would erase the difference between a
# declared tolerance and one the engine invented.

@dataclass(frozen=True)
class NoTolerance:
    kind: str = "none"


@dataclass(frozen=True)
class Symmetric:
    plus_minus: float
    kind: str = "symmetric"


@dataclass(frozen=True)
class Deviations:
    upper: float      # signed; >= 0 by convention
    lower: float      # signed; <= 0 by convention
    kind: str = "deviations"


@dataclass(frozen=True)
class LimitPair:
    maximum: float
    minimum: float
    kind: str = "limit_pair"


@dataclass(frozen=True)
class FitClass:
    """A letter fit such as H7 or g6. The drawing states the code, not the numbers, so this holds
    only the designation: limits are attached by `limits.resolve_fit_limits`, which reads the
    published ISO 286-2 table and refuses a class that table does not confirm."""
    hole: str | None
    shaft: str | None
    kind: str = "fit_class"


@dataclass(frozen=True)
class GeneralTolerance:
    """± from a general-tolerance rule (ISO 2768 class or a title-block decimal-place rule), not
    stated on the dimension itself. Kept distinct from Symmetric so a derived limit is never
    reported as if the drawing had printed it."""
    plus_minus: float
    basis: str            # e.g. "ISO 2768-m", "2-place decimal rule"
    kind: str = "general"


from .gdt import GeometricTolerance  # noqa: E402  (frame rows; no model import inside gdt)

Tolerance = (NoTolerance | Symmetric | Deviations | LimitPair | FitClass | GeometricTolerance
             | GeneralTolerance)


@dataclass
class Issue:
    code: str
    severity: str          # "blocking" | "warning"
    message: str


@dataclass
class Characteristic:
    cid: str
    kind: CharKind
    raw_callout: str                 # exact source text, never rewritten
    unit: str
    nominal: float | None = None
    tolerance: Tolerance = NoTolerance()
    count: int = 1
    state: ProvenanceState = ProvenanceState.EXTRACTED
    review: ReviewStatus = ReviewStatus.DETECTED
    source: SourceLocation | None = None
    geometry_value: float | None = None      # measured from definition points, independent
    limits: tuple[float, float] | None = None  # (min, max), only when tolerance is definite
    balloon_no: int | None = None
    checks: list[Check] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    history: list[HistoryEvent] = field(default_factory=list)

    def record(self, actor: str, action: str, detail: str) -> None:
        self.history.append(HistoryEvent(_now(), actor, action, detail))

    def fail(self, code: str, message: str, severity: str = "blocking") -> None:
        if not any(i.code == code for i in self.issues):
            self.issues.append(Issue(code, severity, message))

    @property
    def is_blocking(self) -> bool:
        return any(i.severity == "blocking" for i in self.issues) or self.state in {
            ProvenanceState.UNRESOLVED,
            ProvenanceState.REJECTED,
        }


@dataclass
class UninterpretedAnnotation:
    """Something in the source that looks like an annotation but the parser did not resolve.
    These are kept so the engineer sees the gaps instead of trusting a clean-looking list."""
    document: str
    entity_handle: str
    layer: str
    text: str
    reason: str
    page: int = 1
    anchor_x: float = 0.0
    anchor_y: float = 0.0
