"""Word -> candidate model dimensions, as a PROPOSAL only.

A request such as "change the diameter of shaft.SLDPRT to 25 mm" names a word, not a model
dimension. The part is inspected read-only and every driving dimension is listed with its current
value and the exact command that would set it. Nothing is selected: even a single candidate is
returned for the user to confirm, because a dimension name says nothing reliable about whether it
is a diameter, a length or a depth.

When ``mac inspect`` reports the kernel's dimension type (``Kind``: diameter / radius / linear /
...), each candidate gets ``kind_match`` = match / mismatch / unknown. That is evidence from the
kernel, but it still only orders the list: two diameters on one part are both "match". A diameter
requested on a radius dimension (or the reverse) gets its value converted, marked CALCULATED.

Where no type is reported, a ``hint`` is given when the feature name contains a word commonly
associated with the requested word (e.g. "Hole" for diameter). It is marked AI_PROPOSED, only
orders the list and never removes a candidate.
"""
from __future__ import annotations

import re
from typing import Any

HINT_WORDS = {
    "diameter": ("hole", "revolve", "circle", "bore", "cboreh", "cskh", "cylinder"),
    "radius": ("fillet", "revolve", "circle", "arc", "round"),
    "length": ("extrude", "boss", "base"),
    "depth": ("extrude", "cut", "hole", "boss"),
    "thickness": ("extrude", "boss", "shell", "base"),
    "height": ("extrude", "boss", "base"),
    "width": ("sketch",),
}


LINEAR_WORDS = {"length", "depth", "thickness", "height", "width"}


def kind_match(word: str, kind: str | None) -> str:
    if not kind:
        return "unknown"
    word = word.lower()
    if word in ("diameter", "radius"):
        return "match" if kind in ("diameter", "radius") else "mismatch"
    if word in LINEAR_WORDS:
        return "match" if kind == "linear" else "mismatch"
    return "unknown"


def short_name(full: str) -> str:
    """D1@Boss-Extrude1@part.Part -> D1@Boss-Extrude1 (the form `edit --set` accepts)."""
    parts = full.split("@")
    if len(parts) >= 3 and re.search(r"\.(part|sldprt)$", parts[-1], re.I):
        parts = parts[:-1]
    return "@".join(parts)


def propose_dimensions(word: str, value_mm: float | None, inspect_data: dict[str, Any],
                       source_path: str) -> dict[str, Any]:
    keys = HINT_WORDS.get(word.lower(), ())
    cands = []
    for d in inspect_data.get("dimensions") or []:
        name = short_name(str(d.get("Name", "")))
        if not name:
            continue
        feature = name.split("@", 1)[1] if "@" in name else ""
        hit = next((k for k in keys if k in feature.lower()), None)
        kind = d.get("Kind")
        c = {"name": name, "full_name": d.get("Name"), "current_mm": d.get("ValueMm"),
             "kind": kind, "kind_match": kind_match(word, kind),
             "hint": (f"feature name contains '{hit}' (AI_PROPOSED; not evidence that this is "
                      f"the {word})") if hit and not kind else None}
        if value_mm is not None:
            v = value_mm
            if word.lower() == "diameter" and kind == "radius":
                v = value_mm / 2
                c["conversion"] = f"CALCULATED: radius dimension, {value_mm:g} / 2"
            elif word.lower() == "radius" and kind == "diameter":
                v = value_mm * 2
                c["conversion"] = f"CALCULATED: diameter dimension, {value_mm:g} x 2"
            c["proposed_mm"] = v
            c["already_at_value"] = (isinstance(d.get("ValueMm"), (int, float))
                                     and abs(float(d["ValueMm"]) - v) < 1e-9)
            c["command"] = (f'py -m astracad_cad edit "{source_path}" --set {name}={v:g} '
                            f"--name <new_name> --out <dir> --authorize <you>")
        cands.append(c)
    order = {"match": 0, "unknown": 1, "mismatch": 2}
    cands.sort(key=lambda c: (order[c["kind_match"]], c["hint"] is None, c["name"]))
    return {"word": word, "value_mm": value_mm, "document": source_path,
            "auto_selected": False,
            "selection": ("NOT MADE — pick one candidate and run its command, or name the "
                          "dimension in the request" if cands else
                          "no driving dimensions were read; nothing can be proposed"),
            "candidates": cands}


def proposal_route(it) -> bool:
    """True when the only thing missing from a modify request is which named dimension."""
    return (it.intent == "modify_existing" and it.fact("document") is not None
            and it.fact("target_dimension") is not None and not it.ambiguities
            and it.missing and all("named model dimension" in m for m in it.missing))


def propose_for(it, orch) -> tuple[str, Any, dict[str, Any] | None]:
    """Inspect the named part read-only (recorded as a task) and build the proposal."""
    from .contract import Op
    from .orchestrator import CadRequest
    path = it.fact("document")
    tid, res = orch.submit(CadRequest(it.application or "solidworks", Op.INSPECT_PARAMETERS,
                                      {"path": path}, summary=f"propose dimensions: {it.request}"))
    if res is None or not res.ok:
        return tid, res, None
    return tid, res, propose_dimensions(it.fact("target_dimension"), it.expect.get("value_mm"),
                                        res.data, path)
