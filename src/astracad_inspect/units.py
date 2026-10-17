"""Explicit engineering units.

A DXF carries no length unit in its geometry; INSUNITS declares one and can be absent or wrong.
Every value in the engine therefore travels with a unit, and mixing units raises rather than
coercing.
"""
from __future__ import annotations

from dataclasses import dataclass

# Factors to millimetres. Only units a mechanical drawing is drawn in.
_TO_MM = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "ft": 304.8,
    "µm": 0.001,
    "um": 0.001,
}

# DXF $INSUNITS code -> canonical unit name.
INSUNITS_TO_UNIT = {
    0: None,          # unspecfied — caller must resolve it, never assume
    1: "in",
    2: "ft",
    4: "mm",
    5: "cm",
    6: "m",
    7: "km",
    8: "µm",
    9: "nm",
    14: "µm",
}

ANGULAR_UNIT = "deg"


class UnitError(ValueError):
    pass


@dataclass(frozen=True)
class Length:
    value: float
    unit: str

    def __post_init__(self) -> None:
        if self.unit not in _TO_MM:
            raise UnitError(f"unknown length unit: {self.unit!r}")

    def to_mm(self) -> "Length":
        return Length(self.value * _TO_MM[self.unit], "mm")

    @property
    def mm(self) -> float:
        if self.unit != "mm":
            raise UnitError(f"asked for .mm on a {self.unit} length; convert first")
        return self.value


def to_mm(value: float, unit: str) -> float:
    if unit not in _TO_MM:
        raise UnitError(f"unknown length unit: {unit!r}")
    return value * _TO_MM[unit]


def convert(value: float, src: str, dst: str) -> float:
    if src not in _TO_MM or dst not in _TO_MM:
        raise UnitError(f"cannot convert {src!r} -> {dst!r}")
    return value * _TO_MM[src] / _TO_MM[dst]


def assert_same_unit(a: str, b: str, what: str = "") -> None:
    if a != b:
        raise UnitError(f"unit mismatch{f' for {what}' if what else ''}: {a} vs {b}")
