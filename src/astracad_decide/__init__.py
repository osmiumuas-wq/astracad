"""Decisions that can be re-read: what was ruled, on what evidence, with what confidence.

`astracad_inspect` produces a plan and its gate. This package turns that gate into a record:
the ruling, every piece of evidence that supports or opposes it, the contradictions kept side by
side, the absences that block judgement, one bounded confidence, the rulings that were not taken,
and - once someone measures the part - whether it held. Records are appended to a hash-chained
JSONL file; editing any record breaks the chain at that record.

    py -m astracad_decide decide plan.json --actor NAME
    py -m astracad_decide verify plan.decisions.jsonl --plan plan.json
    py -m astracad_decide outcome plan.decisions.jsonl --verdict OVERTURNED --actual "..." --actor NAME
    py -m astracad_decide reliability plan.outcomes.jsonl
"""
from .engine import evaluate
from .model import (
    ACCEPT, DISPOSITIONS, EVIDENCE_WEIGHTS, HOLD, MIN_OUTCOMES, NOT_EVALUABLE, NOT_JUDGEABLE,
    REJECT, RELEASE, Confidence, Contradiction, Decision, DecisionStore, EvidenceNode, Unknown,
    canonical, decision_id, sha256_text,
)
from .outcome import CONFIRMED, OVERTURNED, dangling, record, reliability, summary
from .report import one_line, render

__all__ = [
    "evaluate", "Decision", "DecisionStore", "EvidenceNode", "Contradiction", "Unknown",
    "Confidence", "decision_id", "canonical", "sha256_text",
    "RELEASE", "HOLD", "REJECT", "NOT_EVALUABLE", "ACCEPT", "NOT_JUDGEABLE", "DISPOSITIONS",
    "EVIDENCE_WEIGHTS", "MIN_OUTCOMES",
    "record", "summary", "reliability", "dangling", "CONFIRMED", "OVERTURNED",
    "render", "one_line",
]