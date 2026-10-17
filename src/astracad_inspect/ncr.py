"""Nonconformance reports (NCR): from a failed measurement or checklist item to closure.

Life cycle (every step named, dated and kept in the report's own history):

    OPEN  -> CONTAINED (optional)  -> DISPOSITIONED -> CLOSED
    OPEN  -> CANCELLED (with a reason; only before a disposition)

Dispositions: REWORK, REPAIR, USE_AS_IS, SCRAP, RETURN_TO_SUPPLIER. USE_AS_IS and REPAIR accept a part
that does not meet the drawing, so they need a second person's approval (`approved_by` must differ
from the person entering the disposition) - the material-review-board rule in its smallest form.
Closing needs a root cause, a corrective action and how its effect was verified.

An NCR links to what it is about: the run (plan key), characteristic, balloon, sample/serial and,
when raised from a value, the measurement entry with its value and limits - copied in at creation
so the report stands on its own if the run changes later.

Numbers are NCR-<year>-<0001>, one sequence per store, never reused.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

DISPOSITIONS = ("REWORK", "REPAIR", "USE_AS_IS", "SCRAP", "RETURN_TO_SUPPLIER")
NEEDS_APPROVAL = ("USE_AS_IS", "REPAIR")
_NAME = re.compile(r"[\w .'-]{1,64}")
_NO = re.compile(r"NCR-\d{4}-\d{4}")
_lock = threading.Lock()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _who(v, field="by") -> str:
    s = str(v or "").strip()
    if not _NAME.fullmatch(s):
        raise ValueError(f"{field}: a name is required (letters, digits, space . ' - ; up to 64)")
    return s


def _text(v, field, need=True, n=2000) -> str:
    s = str(v or "").strip()[:n]
    if need and not s:
        raise ValueError(f"{field} is required")
    return s


class NcrStore:
    def __init__(self, root: str | Path):
        self.dir = Path(root) / "ncr"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, number: str) -> Path:
        if not _NO.fullmatch(str(number or "")):
            raise PermissionError("invalid NCR number")
        return self.dir / f"{number}.json"

    def get(self, number: str) -> dict:
        p = self._path(number)
        if not p.exists():
            raise FileNotFoundError(f"no {number}")
        return json.loads(p.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        out = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.dir.glob("NCR-*.json"))]
        return sorted(out, key=lambda n: n["number"], reverse=True)

    def for_run(self, key: str) -> list[dict]:
        return [n for n in self.list() if (n.get("link") or {}).get("key") == key]

    def _save(self, n: dict) -> dict:
        p = self._path(n["number"])
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(n, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        return n

    def create(self, body: dict, link: dict) -> dict:
        by = _who(body.get("by"))
        desc = _text(body.get("description"), "description of the nonconformance")
        with _lock:
            year = time.strftime("%Y", time.gmtime())
            seq = [int(p.stem.split("-")[2]) for p in self.dir.glob(f"NCR-{year}-*.json")]
            number = f"NCR-{year}-{(max(seq) + 1 if seq else 1):04d}"
            n = {"number": number, "status": "OPEN", "created": {"at": _now(), "by": by}, "title": _text(body.get("title"), "title", False, 200)
                 or desc[:80], "description": desc, "link": link, "containment": None, "disposition": None,
                 "closure": None, "history": [{"at": _now(), "by": by, "action": "opened", "detail": desc[:200]}]}
            return self._save(n)

    def act(self, number: str, body: dict) -> dict:
        action = body.get("action")
        by = _who(body.get("by"))
        with _lock:
            n = self.get(number)
            st = n["status"]
            if st in ("CLOSED", "CANCELLED"):
                raise ValueError(f"{number} is {st.lower()}; it cannot change")
            if action == "contain":
                if st not in ("OPEN", "CONTAINED"):
                    raise ValueError("containment is recorded before the disposition")
                n["containment"] = {"at": _now(), "by": by, "action": _text(body.get("text"), "containment action")}
                n["status"] = "CONTAINED"
                detail = n["containment"]["action"]
            elif action == "disposition":
                if st not in ("OPEN", "CONTAINED"):
                    raise ValueError(f"{number} already has a disposition")
                d = str(body.get("disposition", "")).upper()
                if d not in DISPOSITIONS:
                    raise ValueError(f"disposition must be one of {', '.join(DISPOSITIONS)}")
                just = _text(body.get("justification"), "justification")
                rec = {"at": _now(), "by": by, "disposition": d, "justification": just}
                if d in NEEDS_APPROVAL:
                    ap = _who(body.get("approved_by"), "approved_by")
                    if ap.lower() == by.lower():
                        raise ValueError(f"{d} accepts a part that does not meet the drawing: it needs a second "
                                         "person's approval (approved_by must differ from the person entering it)")
                    rec["approved_by"] = ap
                n["disposition"] = rec
                n["status"] = "DISPOSITIONED"
                detail = f"{d}: {just}" + (f" (approved by {rec['approved_by']})" if "approved_by" in rec else "")
            elif action == "close":
                if st != "DISPOSITIONED":
                    raise ValueError("an NCR is closed after its disposition")
                n["closure"] = {"at": _now(), "by": by, "root_cause": _text(body.get("root_cause"), "root cause"),
                                "corrective_action": _text(body.get("corrective_action"), "corrective action"),
                                "verification": _text(body.get("verification"), "verification of effectiveness")}
                n["status"] = "CLOSED"
                detail = n["closure"]["corrective_action"]
            elif action == "cancel":
                if st not in ("OPEN", "CONTAINED"):
                    raise ValueError("only an NCR without a disposition can be cancelled")
                n["status"] = "CANCELLED"
                detail = _text(body.get("text"), "reason for cancelling")
                n["cancelled"] = {"at": _now(), "by": by, "reason": detail}
            else:
                raise ValueError("action must be contain, disposition, close or cancel")
            n["history"].append({"at": _now(), "by": by, "action": action, "detail": detail[:300]})
            return self._save(n)


def link_from_measurement(plan: dict, key: str, cell: dict | None, cid: str, sample: str | None) -> dict:
    """What the NCR is about, copied from the plan and (if any) the measurement entry."""
    row = next((r for r in plan.get("rows") or [] if r["cid"] == cid), None)
    if row is None:
        raise ValueError(f"unknown characteristic {cid!r}")
    link = {"key": key, "document": plan.get("source_document"), "revision": plan.get("revision"),
            "cid": cid, "balloon_no": row.get("balloon_no"), "requirement": row.get("callout"),
            "minimum": row.get("minimum"), "maximum": row.get("maximum"), "unit": row.get("unit"),
            "sample": sample}
    if cell:
        link.update(measurement_id=cell.get("id"), actual=cell.get("value"), verdict=cell.get("verdict"),
                    inspector=cell.get("inspector"), instrument=cell.get("instrument"))
    return link
