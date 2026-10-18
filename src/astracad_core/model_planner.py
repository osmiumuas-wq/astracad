"""The user's local language model as a planner for part requests the fixed phrasings do not cover - checked,
never trusted.

The model returns one JSON object: a typed part spec (only the CAD runtime's own operations) plus, for every
number in it, where the number came from. ASTraCAD then decides what that is worth:

- the spec must use only the listed operations with their listed arguments (anything else: refused);
- a number the model says came from the request counts as stated only when its quoted words really are in
  the request and contain that number (units converted) - checked here, not taken on the model's word;
- every number in the spec must be accounted for; an unexplained number stops the plan;
- everything else the model chose (a position, a derived size) is AI_PROPOSED, and the plan does not run until
  the user accepts those values - by name, for this build only;
- the model's questions, when a size is missing, are shown and nothing runs.

The plan that is built is exactly the plan that was shown: it is kept under its own id (the SHA-256 of the
exchange) with the full prompt and answer, so what the model said is auditable afterwards. Kernel and STEP
verification then apply as to every build: the CAD runtime derives its expectations from the spec itself.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any

from .model_gateway import ModelError, Reply
from .requests import UNIT_MM, Fact, Interpretation, _norm_unit, _safe_name

INSTRUCTIONS = """You are the planning stage of ASTraCAD, a CAD automation tool that drives SOLIDWORKS.
Turn the part request in the user's message into ONE JSON object and nothing else - no prose, no markdown, no code.
Do not use any tools, files or web access.

The JSON object:
{
 "status": "ok" or "need_info",
 "questions": ["..."],                      // what must be asked; required when status is need_info
 "spec": {"name": "short_name", "units": "mm", "features": [ ...operations... ]},   // when status is ok
 "values": [ {"name": "plate width", "value": 60, "unit": "mm", "source": "request", "quote": "60 x 40 x 10 mm"} ],
 "assumptions": ["..."]                    // every choice the request does not state
}

Operations (sizes in mm, angles in degrees; the part is built in this order; "front" plane: x right, y up,
extrusions go +z). Use ONLY these, with ONLY these argument names:
- {"$op":"sketch.rectangle","id":"s1","sketched_on":"front","args":{"width":W,"height":H,"x":X0,"y":Y0}}  (corner at x,y)
- {"$op":"sketch.circle","id":"s2","sketched_on":"front","args":{"radius":R,"x":CX,"y":CY}}
- {"$op":"sketch.loops","id":"s3","sketched_on":"front","args":{"loops":[[SEG,...],...]}}  SEG: ["L",x1,y1,x2,y2] |
   ["A",cx,cy,sx,sy,ex,ey,1 or -1] (arc from s to e, 1 = counter-clockwise) | ["C",cx,cy,r]; first loop = outside,
   further loops = openings through the part
- {"$op":"sketch.profile","id":"s4","sketched_on":"front","args":{"points":[[x,y],...]}}  closed polygon, every y >= 0,
   for a turned part: it is revolved about the x axis
- {"$op":"feature.extrude","id":"f1","args":{"depth":D}}        extrudes the sketch just before it
- {"$op":"feature.cut","id":"f2","args":{"depth":D}}            cuts the rectangle sketch just before it, from the front face
- {"$op":"feature.revolve","id":"f3","args":{}}                 revolves the profile just before it, 360 degrees
- {"$op":"feature.hole","id":"h1","sketched_on":"front","args":{"diameter":D,"depth":DEPTH,"center":[X,Y]}}
- {"$op":"feature.fillet","id":"r1","args":{"radius":R,"select":"corners" or "end_circles"}}
   corners = the 4 edges along the depth of an extruded rectangle; end_circles = outer edge at both ends of a turned part
- {"$op":"feature.chamfer","id":"c1","args":{"distance":C,"select":"corners" or "end_circles"}}   (45 degrees)
- {"$op":"feature.pattern.linear","id":"p1","args":{"feature":"h1","count":N,"spacing":S,"direction":"X" or "Y"}}
- {"$op":"feature.pattern.circular","id":"p2","args":{"feature":"h1","count":N,"axis":"Z","center":[CX,CY]}}

Rules:
- If the part needs anything these operations cannot make, or a size is missing, use status "need_info" and ask.
  Never invent a size. Prefer asking over assuming.
- List EVERY number used in the spec in "values" (each distinct number once is enough), with
  source "request" and the exact words of the request it came from as "quote", or
  source "derived" (computed from request numbers; say how in "quote", e.g. "60 / 2"), or
  source "assumed" (your choice - also add a line to "assumptions").
- Place an extruded rectangle with its corner at x = 0, y = 0 unless the request says otherwise.
"""

_OPS: dict[str, dict[str, Any]] = {
    "sketch.rectangle": {"plane": True, "req": {"width": "pos", "height": "pos"}, "opt": {"x": "num", "y": "num"}},
    "sketch.circle": {"plane": True, "any": [["radius"], ["diameter"]], "opt": {"radius": "pos", "diameter": "pos", "x": "num", "y": "num"}},
    "sketch.loops": {"plane": True, "req": {"loops": "loops"}, "opt": {}},
    "sketch.profile": {"plane": True, "req": {"points": "points"}, "opt": {}},
    "feature.extrude": {"req": {"depth": "pos"}, "opt": {"reversed": "bool"}},
    "feature.cut": {"any": [["depth"], ["through_all"]], "opt": {"depth": "pos", "through_all": "bool", "reversed": "bool"}},
    "feature.revolve": {"req": {}, "opt": {"angle": "angle"}},
    "feature.hole": {"plane": True, "req": {"diameter": "pos"}, "any": [["depth"], ["through_all"]],
                     "opt": {"depth": "pos", "through_all": "bool", "center": "xy"}},
    "feature.fillet": {"req": {"radius": "pos"}, "opt": {"select": ("corners", "end_circles")}},
    "feature.chamfer": {"req": {"distance": "pos"}, "opt": {"select": ("corners", "end_circles")}},
    "feature.pattern.linear": {"req": {"feature": "ref", "count": "count", "spacing": "pos"}, "opt": {"direction": ("X", "Y", "Z")}},
    "feature.pattern.circular": {"req": {"feature": "ref", "count": "count"}, "opt": {"axis": ("X", "Y", "Z"), "center": "xy"}},
}
_SKETCHES = {"sketch.rectangle", "sketch.circle", "sketch.loops", "sketch.profile"}
PROPOSED = "The model proposed "      # the start of the one decision accept() settles (the workspace matches it too)
_CONSUMERS = {"feature.extrude", "feature.cut", "feature.revolve"}
MAX = 10000.0


def _number_ok(v, kind) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return False
    return {"pos": 0 < v <= MAX, "num": abs(v) <= MAX, "angle": 0 < v <= 360}.get(kind, True)


def validate_spec(spec: Any) -> list[str]:
    """Problems with a model-written spec; empty when it uses only known operations correctly."""
    errs: list[str] = []
    if not isinstance(spec, dict) or not isinstance(spec.get("features"), list):
        return ["the spec has no list of features"]
    feats = spec["features"]
    if not 1 <= len(feats) <= 60:
        errs.append(f"{len(feats)} features (1 to 60 allowed)")
    if spec.get("units", "mm") != "mm":
        errs.append("units must be mm")
    ids: set[str] = set()
    pending = None
    for n, f in enumerate(feats):
        where = f"feature {n + 1}"
        if not isinstance(f, dict):
            errs.append(f"{where} is not an object")
            continue
        op = f.get("$op")
        rule = _OPS.get(op)
        if rule is None:
            errs.append(f"{where}: unknown operation {op!r}")
            continue
        fid = f.get("id")
        if not isinstance(fid, str) or not re.fullmatch(r"[A-Za-z][\w-]{0,39}", fid) or fid in ids:
            errs.append(f"{where}: id must be a unique short name")
        ids.add(str(fid))
        if set(f) - {"$op", "id", "args", "sketched_on"}:
            errs.append(f"{where}: unknown keys {sorted(set(f) - {'$op', 'id', 'args', 'sketched_on'})}")
        if rule.get("plane") and f.get("sketched_on") not in ("front", "top", "right"):
            errs.append(f"{where}: sketched_on must be front, top or right")
        args = f.get("args") or {}
        if not isinstance(args, dict):
            errs.append(f"{where}: args must be an object")
            continue
        allowed = set(rule.get("req", {})) | set(rule.get("opt", {}))
        if set(args) - allowed:
            errs.append(f"{where} ({op}): unknown arguments {sorted(set(args) - allowed)}")
        for k, kind in rule.get("req", {}).items():
            if k not in args:
                errs.append(f"{where} ({op}): {k} is required")
        if rule.get("any") and not any(all(k in args for k in grp) for grp in rule["any"]):
            errs.append(f"{where} ({op}): needs one of {rule['any']}")
        for k, v in args.items():
            kind = {**rule.get("opt", {}), **rule.get("req", {})}.get(k)
            if kind in ("pos", "num", "angle") and not _number_ok(v, kind):
                errs.append(f"{where} ({op}): {k} = {v!r} is not a valid {'positive ' if kind == 'pos' else ''}number")
            elif kind == "bool" and not isinstance(v, bool):
                errs.append(f"{where} ({op}): {k} must be true or false")
            elif isinstance(kind, tuple) and v not in kind:
                errs.append(f"{where} ({op}): {k} must be one of {kind}")
            elif kind == "count" and not (isinstance(v, int) and not isinstance(v, bool) and 2 <= v <= 1000):
                errs.append(f"{where} ({op}): count must be a whole number 2..1000")
            elif kind == "xy" and not (isinstance(v, list) and len(v) == 2 and all(_number_ok(c, "num") for c in v)):
                errs.append(f"{where} ({op}): {k} must be [x, y]")
            elif kind == "ref" and v not in ids - {fid}:
                errs.append(f"{where} ({op}): feature {v!r} is not an earlier feature")
            elif kind == "points" and not (isinstance(v, list) and len(v) >= 3 and all(
                    isinstance(p, list) and len(p) == 2 and _number_ok(p[0], "num") and _number_ok(p[1], "num") and p[1] >= 0
                    for p in v)):
                errs.append(f"{where} ({op}): points must be 3 or more [x, y] with y >= 0")
            elif kind == "loops" and not (isinstance(v, list) and v and all(isinstance(lp, list) and lp for lp in v)):
                errs.append(f"{where} ({op}): loops must be a list of segment lists")
        if op in _SKETCHES:
            pending = op
        elif op in _CONSUMERS:
            if pending is None:
                errs.append(f"{where} ({op}) has no sketch just before it")
            elif op == "feature.revolve" and pending != "sketch.profile":
                errs.append(f"{where}: feature.revolve needs a sketch.profile just before it")
            elif op == "feature.cut" and pending != "sketch.rectangle":
                errs.append(f"{where}: feature.cut needs a sketch.rectangle just before it")
            pending = None
        elif pending is not None:
            errs.append(f"{where} ({op}) comes after a sketch nothing has used")
    if feats and isinstance(feats[0], dict) and feats[0].get("$op") not in _SKETCHES:
        errs.append("the first feature must be a sketch")
    return errs


def _numbers(spec: dict) -> set[float]:
    """Every number in the spec's arguments (geometry), rounded to cope with float noise."""
    out: set[float] = set()

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            out.add(round(float(v), 6))
        elif isinstance(v, list):
            # an arc segment's last item is its direction (1 / -1), not a size
            for x in (v[:-1] if v and v[0] == "A" and len(v) == 8 else v):
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
    for f in spec.get("features") or []:
        if isinstance(f, dict):
            walk(f.get("args") or {})
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower().replace("ø", "dia ").replace("×", "x")


def _quote_holds(value: float, quote: str, request: str) -> bool:
    """The quote is in the request and contains this number (with a unit conversion when written)."""
    q = _norm(quote)
    if not q or q not in _norm(request):
        return False
    found = [(float(m.group(1)), _norm_unit(m.group(2)) if m.group(2) else None)
             for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(mm|millimet(?:re|er)s?|cm|m\b|inch(?:es)?|in\b|\")?", q)]
    units = {u for _, u in found if u}
    shared = next(iter(units)) if len(units) == 1 else None    # "4 x 2 x 0.5 in": one unit for all three
    for n, u in found:
        unit = u or shared
        for cand in (n, n * UNIT_MM.get(unit, 1.0) if unit else n):
            if abs(cand - value) <= 1e-6 * max(1.0, abs(value)):
                return True
    return False


def _json_object(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    if "{" not in t:
        raise ValueError("no JSON object in the answer")
    return json.loads(t[t.index("{"): t.rindex("}") + 1])


def plan(request: str, prov, *, store: Path | None = None) -> tuple[Interpretation, dict]:
    """Ask the provider; return (interpretation, record). Nothing here builds anything."""
    text = str(request or "").strip()
    if not text or len(text) > 2000:
        raise ValueError("a request of 1 to 2000 characters is needed")
    user = "Request: " + text
    reply: Reply = prov.complete(INSTRUCTIONS, user)
    exchange = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "provider": reply.provider,
                "model": reply.model, "request": text, "system": INSTRUCTIONS, "user": user,
                "response": reply.text, "meta": reply.meta}
    pid = hashlib.sha256(json.dumps(exchange, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    exchange["plan_id"] = pid
    it = interpret_reply(text, reply)
    it.spec = it.spec and {**it.spec, "notes": {**(it.spec.get("notes") or {}), "planner": reply.provider,
                                                "model": reply.model, "plan_id": pid}}
    record = {"plan_id": pid, "exchange": exchange, "interpretation": it.to_dict()}
    if store is not None:
        store.mkdir(parents=True, exist_ok=True)
        (store / f"{pid}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    return it, record


def interpret_reply(request: str, reply: Reply) -> Interpretation:
    it = Interpretation(request, "create_part", "solidworks",
                        exports=["step"] if re.search(r"\bstep\b|\bstp\b", request, re.I) else [])
    where = (reply.meta or {}).get("where") or "the configured model server"
    it.facts.append(Fact("planner", f"{reply.provider} ({reply.model})", None, "CALCULATED",
                         f"the request text was sent to the language model at {where} at the user's request; "
                         "its answer is checked below"))
    try:
        doc = _json_object(reply.text)
    except ValueError as ex:
        it.ambiguities.append(f"The model's answer was not a usable JSON plan ({ex})")
        return it
    if doc.get("status") == "need_info" or not doc.get("spec"):
        qs = [str(q) for q in (doc.get("questions") or []) if str(q).strip()]
        it.missing.extend(qs or ["The model could not plan this part and asked nothing specific"])
        return it
    spec = doc["spec"]
    errs = validate_spec(spec)
    if errs:
        it.ambiguities.append("The model's plan is not one ASTraCAD can run: " + "; ".join(errs[:8]))
        return it
    proposed: list[str] = []
    explained: set[float] = set()
    for v in doc.get("values") or []:
        if not isinstance(v, dict) or not _number_ok(v.get("value"), "num"):
            continue
        val, name = float(v["value"]), str(v.get("name") or "value")[:60]
        src, quote = str(v.get("source") or ""), str(v.get("quote") or "")[:200]
        explained.add(round(val, 6))
        if src == "request" and _quote_holds(val, quote, request):
            it.facts.append(Fact(name, val, v.get("unit"), "USER_SPECIFIED", quote))
        else:
            why = (f"derived by the model: {quote}" if src == "derived" else
                   "the model quoted words that are not in the request (or do not contain this number)"
                   if src == "request" else f"assumed by the model{': ' + quote if quote else ''}")
            it.facts.append(Fact(name, val, v.get("unit"), "AI_PROPOSED", why))
            proposed.append(f"{name} = {val:g}")
    unexplained = sorted(n for n in _numbers(spec) if n not in explained and n != 0.0)   # 0: the origin
    if unexplained:
        it.ambiguities.append("The model's plan uses numbers it does not account for: "
                              + ", ".join(f"{n:g}" for n in unexplained[:10]))
    for a in (doc.get("assumptions") or [])[:20]:
        it.facts.append(Fact("assumption", str(a)[:200], None, "AI_PROPOSED", "stated by the model"))
    if proposed:
        it.ambiguities.append(f"{PROPOSED}{len(proposed)} value(s) the request does not state: "
                              + "; ".join(proposed[:12]) + " - accept them to build")
    name = _safe_name(str(spec.get("name") or "model_part"))
    it.spec = {"name": f"lm_{name}"[:40], "units": "mm", "features": spec["features"],
               "exports": [{"format": e} for e in it.exports], "notes": {"request": request}}
    return it


def accept(it: Interpretation, who: str) -> Interpretation:
    """The user accepts the values the model proposed, for this build. Questions and refusals still stop it."""
    keep = [a for a in it.ambiguities if not a.startswith(PROPOSED)]
    if len(keep) != len(it.ambiguities):
        it.ambiguities = keep
        it.facts.append(Fact("proposals_accepted", who, None, "USER_SPECIFIED",
                             "the values the model proposed were accepted for this build by the person named"))
    return it
