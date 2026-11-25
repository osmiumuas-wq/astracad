"""What actually happened, attached to the decision that predicted it.

A decision record on its own is an opinion with a hash. It becomes a record of engineering when
the measured outcome is written next to it and the two are compared: CONFIRMED (the ruling held)
or OVERTURNED (it did not), with the evidence node that was wrong named where it can be named.

Weight learning is deliberately asymmetric and stated:

  * a CONFIRMED ruling credits every evidence kind that supported it - the ruling held, and the
    things it stood on did their job;
  * an OVERTURNED ruling takes credit away from exactly one kind - the node that was named, or,
    when nobody named one, the weakest decisive node. A wrong call proves one witness wrong; it
    does not prove the others right.

Each kind is a Beta posterior with its prior worth four observations, so a single bad day moves
a weight by about 0.1, not by half. Below MIN_OUTCOMES_PER_KIND the learned mean is reported but
not used for ruling.
"""
from __future__ import annotations

import json
from pathlib import Path

from .model import (MIN_OUTCOMES_PER_KIND, RELEASE, _now, canonical, sha256_text)

CONFIRMED = "CONFIRMED"
OVERTURNED = "OVERTURNED"
VERDICTS = (CONFIRMED, OVERTURNED)
PRIOR_STRENGTH = 4.0   # observations a prior weight is worth


def _append(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical(entry) + "\n")


def entries(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as ex:
            raise ValueError(f"{p.name} line {n}: not JSON ({ex.msg})") from ex
    return out


def _decisive(decision: dict) -> list[dict]:
    want = "supports" if decision.get("disposition") == RELEASE else "opposes"
    return [n for n in decision.get("evidence") or [] if n.get("stance") == want]


def attribute(decision: dict, blame: str | None = None) -> dict:
    """Which evidence node the overturn is charged to, and on what basis."""
    nodes = decision.get("evidence") or []
    if blame:
        hit = next((n for n in nodes if n.get("nid") == blame), None)
        if hit is None:
            raise ValueError(f"{blame!r} is not an evidence node of this decision")
        return {"nid": hit["nid"], "kind": hit["kind"], "how": "named"}
    decisive = _decisive(decision)
    if not decisive:
        return {"nid": None, "kind": None, "how": "none"}
    weakest = min(decisive, key=lambda n: (n.get("weight", 1.0), n.get("nid", "")))
    return {"nid": weakest["nid"], "kind": weakest["kind"], "how": "inferred"}


def record(path: str | Path, *, decision: dict, verdict: str, actual: str,
           actor: str, blame: str | None = None, note: str = "") -> dict:
    """Append one outcome. Refuses anything it cannot tie to a decision or a person."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    if not actor or not str(actor).strip():
        raise ValueError("an outcome needs a named actor")
    if not str(actual).strip():
        raise ValueError("an outcome needs the evidence of what actually happened")
    if not decision.get("hash"):
        raise ValueError("the decision has no hash: it was not appended to a store")
    if verdict == CONFIRMED:
        credited = sorted({n["kind"] for n in _decisive(decision)})
        attributed = {"nid": None, "kind": None, "how": "credits every decisive kind"}
    else:
        credited = []
        attributed = attribute(decision, blame)
    entry = {
        "recorded_utc": _now(),
        "actor": str(actor).strip(),
        "decision_id": decision.get("decision_id"),
        "decision_hash": decision.get("hash"),
        "disposition": decision.get("disposition"),
        "verdict": verdict,
        "actual": str(actual).strip(),
        "note": str(note).strip(),
        "attributed": attributed,
        "credited": credited,
    }
    entry["entry_hash"] = sha256_text(canonical(entry))
    _append(Path(path), entry)
    return entry


def summary(path: str | Path, disposition: str | None = None) -> dict:
    """Counts for the decision class, and a Beta posterior per evidence kind."""
    rows = entries(path)
    if disposition is not None:
        rows = [r for r in rows if r.get("disposition") == disposition]
    confirmed = sum(1 for r in rows if r.get("verdict") == CONFIRMED)
    overturned = sum(1 for r in rows if r.get("verdict") == OVERTURNED)
    kinds: dict[str, dict] = {}
    for r in rows:
        slot = None
        if r.get("verdict") == OVERTURNED:
            kind = (r.get("attributed") or {}).get("kind")
            if kind:
                slot = kinds.setdefault(kind, {"passes": 0, "failures": 0})
                slot["failures"] += 1
        else:
            for kind in r.get("credited") or []:
                slot = kinds.setdefault(kind, {"passes": 0, "failures": 0})
                slot["passes"] += 1
    return {"decision": {"confirmed": confirmed, "overturned": overturned,
                         "n": confirmed + overturned},
            "kinds": kinds, "entries": len(rows)}


def reliability(path: str | Path, priors: dict[str, float]) -> dict[str, dict]:
    """Learned weight per evidence kind: prior shrunk toward what the ledger observed."""
    s = summary(path)
    out: dict[str, dict] = {}
    for kind, prior in priors.items():
        got = s["kinds"].get(kind, {"passes": 0, "failures": 0})
        a = prior * PRIOR_STRENGTH
        b = (1.0 - prior) * PRIOR_STRENGTH
        n = got["passes"] + got["failures"]
        mean = (got["passes"] + a) / (n + a + b)
        out[kind] = {"prior": prior, "mean": round(mean, 4), "n": n,
                     "passes": got["passes"], "failures": got["failures"],
                     "used": n >= MIN_OUTCOMES_PER_KIND}
    return out


def overrides(reliability_table: dict[str, dict]) -> dict[str, float]:
    """Only kinds with enough outcomes are allowed to move a ruling."""
    return {k: v["mean"] for k, v in reliability_table.items() if v.get("used")}


def dangling(outcomes_path: str | Path, store_path: str | Path) -> list[str]:
    """Outcomes that point at a decision the store does not contain."""
    from .model import DecisionStore

    known = {r.get("hash") for r in DecisionStore(store_path).records()}
    bad = []
    for i, r in enumerate(entries(outcomes_path), 1):
        if r.get("decision_hash") not in known:
            bad.append(f"outcome {i} ({r.get('decision_id')}) refers to a decision that is "
                       "not in the store")
    return bad