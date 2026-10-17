"""Explainable machining cost estimate from sourced inputs.

Nothing is defaulted: every price, rate, time and percentage must be supplied with its source and
the date it was valid for. A missing input is a refusal that lists what is missing, never a typical
value. Part geometry comes from CAD evidence — the kernel's measured volume and the independent
STEP re-read's bounding box — and both files are identified by SHA-256.

Model (one machined part from a rectangular block, stated plainly so it can be disputed):

    stock volume     = prod(size_i + 2 * allowance)                     [mm^3]
    stock mass       = stock volume * 1e-3 * density / 1000             [kg]   (mm^3 -> cm^3, g -> kg)
    material         = stock mass * price per kg
    removed volume   = stock volume - part volume
    cycle time       = given, or removed volume [cm^3] / MRR [cm^3/min] + handling [min]
    machine          = cycle [h] * machine rate
    labour           = cycle [h] * labour rate * attendance (0..1)
    setup per part   = setup [h] * (machine rate + labour rate) / quantity
    direct           = material + machine + labour + setup per part
    after scrap      = direct / (1 - scrap fraction)      (good parts carry the cost of scrapped ones)
    overhead         = after scrap * overhead fraction
    cost per part    = after scrap + overhead
    price per part   = cost per part * (1 + margin)        (only if a margin is supplied)

This is an estimate of the stated model, not a quotation: fixturing, tool wear, finishing,
inspection, heat treatment and logistics are outside it unless added as explicit extra lines.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

STALE_DAYS = 90


class CostingError(ValueError):
    """Inputs are missing, contradictory or physically impossible."""


@dataclass(frozen=True)
class Sourced:
    value: float
    unit: str
    source: str
    as_of: str          # ISO date the value was valid for

    @staticmethod
    def parse(name: str, raw: Any, unit: str, missing: list[str]) -> "Sourced | None":
        if not isinstance(raw, dict) or "value" not in raw:
            missing.append(f"{name} ({unit}) with value, source and as_of")
            return None
        for k in ("source", "as_of"):
            if not str(raw.get(k, "")).strip():
                missing.append(f"{name}: '{k}' is required (no unsourced or undated numbers)")
                return None
        try:
            date.fromisoformat(str(raw["as_of"]))
            v = float(raw["value"])
        except (TypeError, ValueError):
            missing.append(f"{name}: value must be a number and as_of an ISO date")
            return None
        if not math.isfinite(v) or v < 0:
            missing.append(f"{name}: value must be a finite number >= 0, got {raw['value']!r}")
            return None
        return Sourced(v, str(raw.get("unit", unit)), str(raw["source"]), str(raw["as_of"]))


@dataclass
class Geometry:
    volume_mm3: float
    size_mm: tuple[float, float, float]
    volume_source: str
    size_source: str


@dataclass
class Line:
    name: str
    value: float
    unit: str
    formula: str
    inputs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Estimate:
    currency: str
    quantity: int
    lines: list[Line]
    cost_per_part: float
    price_per_part: float | None
    batch_cost: float
    warnings: list[str]
    geometry: Geometry
    inputs: dict[str, Any]
    scope_note: str = ("estimate of the stated model, not a quotation; fixturing, tool wear, "
                       "finishing, inspection, heat treatment and logistics are excluded unless "
                       "added as explicit lines")

    def to_dict(self) -> dict:
        return asdict(self)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def geometry_from_evidence(kernel_report: str | Path, step_file: str | Path) -> Geometry:
    """Volume from the CAD kernel's own measurement; size from the independent STEP re-read."""
    from astracad_cad.step_check import read_step
    kr, sp = Path(kernel_report), Path(step_file)
    rep = json.loads(kr.read_text(encoding="utf-8"))
    vol = (rep.get("measured") or {}).get("volume_mm3")
    if not isinstance(vol, (int, float)) or vol <= 0:
        raise CostingError(f"{kr.name}: no positive measured volume_mm3 in the kernel report")
    st = read_step(sp)
    if st.errors or st.size_mm is None or st.length_unit != "mm" or st.solids != 1:
        raise CostingError(f"{sp.name}: STEP re-read gave no single-solid mm bounding box "
                           f"({st.errors or st.length_unit or 'no bbox'})")
    if st.bbox_approximate:
        raise CostingError(f"{sp.name}: bounding box is approximate; stock size would be a guess")
    return Geometry(float(vol), tuple(float(x) for x in st.size_mm),
                    f"kernel measured volume, {kr.name} sha256:{_sha(kr)[:16]}",
                    f"STEP re-read bounding box, {sp.name} sha256:{_sha(sp)[:16]}")


def estimate(geometry: Geometry, rates: dict, job: dict, *, today: date | None = None) -> Estimate:
    """Compute the estimate. `rates` is the user's rate book, `job` the per-quote choices."""
    today = today or date.today()
    missing: list[str] = []
    currency = str(rates.get("currency", "")).strip()
    if not currency:
        missing.append("rate book 'currency'")
    mat_key, mach_key = job.get("material"), job.get("machine")
    mat = (rates.get("materials") or {}).get(mat_key)
    mach = (rates.get("machines") or {}).get(mach_key)
    if mat is None:
        missing.append(f"material {mat_key!r} in the rate book "
                       f"(have: {sorted((rates.get('materials') or {}))})")
    if mach is None:
        missing.append(f"machine {mach_key!r} in the rate book "
                       f"(have: {sorted((rates.get('machines') or {}))})")
    density = Sourced.parse("material density", (mat or {}).get("density_g_cm3"), "g/cm^3", missing) if mat else None
    price_kg = Sourced.parse("material price", (mat or {}).get("price_per_kg"), f"{currency}/kg", missing) if mat else None
    m_rate = Sourced.parse("machine rate", (mach or {}).get("rate_per_hour"), f"{currency}/h", missing) if mach else None
    l_rate = Sourced.parse("labour rate", rates.get("labour_rate_per_hour"), f"{currency}/h", missing)
    overhead = Sourced.parse("overhead fraction", rates.get("overhead_fraction"), "1", missing)
    scrap = Sourced.parse("scrap fraction", rates.get("scrap_fraction"), "1", missing)
    margin = (Sourced.parse("margin fraction", rates["margin_fraction"], "1", missing)
              if "margin_fraction" in rates else None)
    allowance = Sourced.parse("stock allowance per side", job.get("stock_allowance_mm"), "mm", missing)
    setup = Sourced.parse("setup time", job.get("setup_time_min"), "min", missing)
    attendance = Sourced.parse("operator attendance", job.get("operator_attendance"), "1", missing)
    qty = job.get("quantity")
    if not isinstance(qty, int) or qty < 1:
        missing.append("quantity (integer >= 1)")
    cycle = mrr = handling = None
    if "cycle_time_min" in job:
        cycle = Sourced.parse("cycle time", job["cycle_time_min"], "min", missing)
    elif "mrr_cm3_per_min" in job:
        mrr = Sourced.parse("material removal rate", job["mrr_cm3_per_min"], "cm^3/min", missing)
        handling = Sourced.parse("handling time", job.get("handling_time_min"), "min", missing)
    else:
        missing.append("either cycle_time_min, or mrr_cm3_per_min + handling_time_min")
    if missing:
        raise CostingError("cannot estimate without: " + "; ".join(missing))

    for nm, f in (("scrap fraction", scrap), ("overhead fraction", overhead),
                  ("operator attendance", attendance)):
        if f.value > 1 or (nm == "scrap fraction" and f.value >= 1):
            raise CostingError(f"{nm} must be a fraction (0..1), got {f.value}")
    if mrr is not None and mrr.value <= 0:
        raise CostingError("material removal rate must be > 0")

    warnings = []
    used = {"density": density, "price_per_kg": price_kg, "machine_rate": m_rate,
            "labour_rate": l_rate, "overhead_fraction": overhead, "scrap_fraction": scrap,
            "stock_allowance": allowance, "setup_time": setup, "operator_attendance": attendance}
    for k, v in (("cycle_time", cycle), ("mrr", mrr), ("handling_time", handling),
                 ("margin_fraction", margin)):
        if v is not None:
            used[k] = v
    for k, v in used.items():
        age = (today - date.fromisoformat(v.as_of)).days
        if age > STALE_DAYS:
            warnings.append(f"{k} is {age} days old (as of {v.as_of}, source: {v.source}); "
                            f"refresh before quoting")
        if age < 0:
            warnings.append(f"{k} is dated in the future ({v.as_of})")
    for nm, s in (("machine rate", m_rate), ("labour rate", l_rate), ("material price", price_kg)):
        if currency not in s.unit:
            warnings.append(f"{nm} unit {s.unit!r} does not name the rate-book currency {currency}")

    g = geometry
    lines: list[Line] = []

    def add(name, value, unit, formula, **inputs):
        lines.append(Line(name, value, unit, formula,
                          {k: (asdict(v) if isinstance(v, Sourced) else v) for k, v in inputs.items()}))
        return value

    a = allowance.value
    stock = add("stock volume", math.prod(s + 2 * a for s in g.size_mm), "mm^3",
                "prod(size_i + 2*allowance)", size_mm=list(g.size_mm), size_source=g.size_source,
                allowance=allowance)
    if g.volume_mm3 > stock * (1 + 1e-9):
        raise CostingError(f"part volume {g.volume_mm3:.3f} mm^3 exceeds its stock {stock:.3f} mm^3")
    mass = add("stock mass", stock * 1e-3 * density.value / 1000.0, "kg",
               "stock_mm3 * 1e-3 * density / 1000", density=density)
    material = add("material", mass * price_kg.value, currency, "stock_mass * price_per_kg",
                   price_per_kg=price_kg)
    removed = add("removed volume", stock - g.volume_mm3, "mm^3", "stock - part volume",
                  part_volume_mm3=g.volume_mm3, volume_source=g.volume_source)
    if cycle is not None:
        cyc = add("cycle time", cycle.value, "min", "given", cycle_time=cycle)
    else:
        cyc = add("cycle time", removed * 1e-3 / mrr.value + handling.value, "min",
                  "removed_cm3 / MRR + handling (CALCULATED from the stated MRR)",
                  mrr=mrr, handling=handling)
    machine = add("machine", cyc / 60.0 * m_rate.value, currency, "cycle_h * machine_rate",
                  machine=mach_key, machine_rate=m_rate)
    labour = add("labour", cyc / 60.0 * l_rate.value * attendance.value, currency,
                 "cycle_h * labour_rate * attendance", labour_rate=l_rate, attendance=attendance)
    setup_pp = add("setup per part", setup.value / 60.0 * (m_rate.value + l_rate.value) / qty,
                   currency, "setup_h * (machine_rate + labour_rate) / quantity",
                   setup_time=setup, quantity=qty)
    direct = add("direct cost", material + machine + labour + setup_pp, currency,
                 "material + machine + labour + setup per part")
    after_scrap = add("after scrap", direct / (1.0 - scrap.value), currency,
                      "direct / (1 - scrap_fraction)", scrap_fraction=scrap)
    oh = add("overhead", after_scrap * overhead.value, currency,
             "after_scrap * overhead_fraction", overhead_fraction=overhead)
    per_part = add("cost per part", after_scrap + oh, currency, "after_scrap + overhead")
    price = None
    if margin is not None:
        price = add("price per part", per_part * (1.0 + margin.value), currency,
                    "cost_per_part * (1 + margin)", margin_fraction=margin)
    batch = add("batch cost", per_part * qty, currency, "cost_per_part * quantity", quantity=qty)
    return Estimate(currency, qty, lines, per_part, price, batch, warnings, g,
                    {k: asdict(v) for k, v in used.items()} | {"material": mat_key,
                                                               "machine": mach_key,
                                                               "quantity": qty})
