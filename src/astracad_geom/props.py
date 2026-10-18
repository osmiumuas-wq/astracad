"""Measurements of an OpenCascade shape, in the units SOLIDWORKS reports (mm, mm^2, mm^3)."""
from __future__ import annotations

from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp
from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedMapOfShape

from .occ_build import bbox


def _count(shape, kind) -> int:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, m)
    return m.Extent()


def measure(shape) -> dict:
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    volume, c = g.Mass(), g.CentreOfMass()
    s = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, s)
    box = bbox(shape)
    return {"volume_mm3": volume, "surface_area_mm2": s.Mass(), "center_of_mass_mm": [c.X(), c.Y(), c.Z()],
            "bbox_mm": box, "size_mm": [box[3] - box[0], box[4] - box[1], box[5] - box[2]],
            "solids": _count(shape, TopAbs_SOLID), "faces": _count(shape, TopAbs_FACE),
            "edges": _count(shape, TopAbs_EDGE)}
