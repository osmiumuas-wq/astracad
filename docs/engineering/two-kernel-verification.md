# Two kernels: SOLIDWORKS and OpenCascade (2026-10-18)

Every part ASTraCAD builds in SOLIDWORKS is also built, independently, in OpenCascade (`astracad_geom`) from the
same typed spec (docs/engineering/part-language-v2.md). The build is accepted only when the two kernels agree.

Why: the analytic expectations in `mac` cover simple shapes only, and the STEP re-read measures volume only for
polyhedra - so a part with a round hole, a fillet or a loft used to come back "volume not verifiable". OpenCascade
measures anything it can build, from the spec alone; it never sees what SOLIDWORKS produced.

## What is compared

In every SOLIDWORKS build report (`astracad_cad/adapters/solidworks.py` `_run_spec` -> `astracad_geom/compare.py`):

| Check | SOLIDWORKS | OpenCascade | Passes when |
|---|---|---|---|
| `occ:build` | - | builds the spec | it builds; "not verifiable" (with the reason) when the second kernel cannot build that operation yet; **failed** when it refuses the spec as invalid Part Language that SOLIDWORKS ran |
| `occ:volume_mm3`, `occ:surface_area_mm2` | mass properties (`mac`) | `BRepGProp` | relative difference within the class tolerance |
| `occ:center_of_mass_mm` | mass properties | `BRepGProp` | every coordinate within tolerance x max(1, part size) |
| `occ:solid_bodies` | body count | solid count | equal |
| `occ:step_bbox_mm` | the exported STEP's box (re-read, `step_check`) | `BRepBndLib` optimal box | within tolerance, when the STEP box is exact |

Tolerances (part-language-v2.md section 6): class E (exact geometry) 1e-6 relative. **Through-all:** SOLIDWORKS resolves
a through-all end condition inside its kernel; measured on this install up to 5.6e-6 relative (area) against the exact
solid, so specs with `through_all` use 1e-5 - the same band `mac`'s own analytic check has used for the same reason.
Classes B (blends) and F (free-form) start at 1e-4 / 1e-3 and are set from measurements as those operations land.

## Evidence (2026-10-18)

- **Closed forms:** all v1 operations in OpenCascade reproduce the closed-form answers to machine precision
  (<= 3e-16 relative): prisms, blind and through holes, Qwen's L-bracket and slotted plate (arcs), revolved stepped
  shaft (Pappus), corner fillets and chamfers, end-circle fillets (Pappus spandrel) and chamfers, linear and circular
  hole patterns, the v1 symmetric pocket cut (`tests/test_geom_occ.py`, 22 tests).
- **History:** every distinct successful SOLIDWORKS build recorded under `_out/` (32 specs: blocks, plates, flanges,
  shafts, tubes, washers, pockets, slots, fillets, chamfers, patterns, parts rebuilt from drawings) agrees with
  OpenCascade on volume, area and centre of mass - most to 0.0 relative difference; the two through-all hole parts at
  2.2e-6 / 5.6e-6 (inside the documented band).
- **Live:** `_out/m0_20261018/` - Qwen's L-bracket (from the local model's plan, built from its spec) and a 100 x 40 x
  10 plate with a 4-hole row: SUCCEEDED, every `kernel:`, `step:` and `occ:` check passed. For the plate the STEP
  re-read cannot measure volume (round holes) - the second kernel confirms SOLIDWORKS's 38869.026645 mm3, area and
  centre of mass.
- Replays of real SOLIDWORKS runs in the adapter tests (`tests/test_geom_compare.py`): agreement accepted, a 0.1 %
  volume difference rejected, a disagreeing build not accepted.

## M1 - the v2 operations in OpenCascade (2026-10-18)

The second kernel now builds every Part Language v2 operation (`tests/test_geom_v2.py`, each against a closed form):
reference planes (offset and explicit frames; the `top` and `right` frames as in section 1 of the language), extrude
and cut from any sketch in any direction (one-sided, reversed, mid-plane), revolve and cut-revolve about the sketch's
u / v axes or any line, at any angle (Pappus), mirror of features and bodies, linear patterns in two directions and
circular patterns about any axis, counterbore and countersink holes (cylinders and cones, exact), shell with faces
chosen by query (exact on a box), fillets and chamfers on edges chosen by query - convex and concave told apart by
the dihedral rule, the count enforced (a 6-edge expectation against 5 convex edges stops the step), multi-body parts
and `body.boolean` (subtract, intersect), sweeps along lines and arcs (Pappus to 1e-7) and along a helix (a spring:
area x helix length to 1e-4, class F), and piecewise-ruled lofts (prismatoid and frustum, exact).

The SOLIDWORKS half of the same operations is the separate `mac` session (branch `part-language-v2`); until a v2
operation lands there, a v2 spec cannot be built in SOLIDWORKS at all - nothing is claimed for it.

## Previews

`astracad_geom/preview.py` meshes the OpenCascade build and draws a shaded isometric SVG with visible edges only
(a depth buffer hides the rest), about 0.2 s for a typical part. The workspace shows it under the 2D sketch on the
Text to CAD page (`POST /api/cad/preview`), with OpenCascade's volume and size - before anything is built.

## Limits

- The second kernel builds v1 and v2 operations; a closed hollow (shell without an opened face) is not built yet -
  "not verifiable", never passed.
- Two kernels reading the same spec can share a misreading of the language itself; the closed forms (a third,
  independent leg), the STEP re-read and the language document are the guard against that.
- Requires `cadquery-ocp` (the `geometry` extra, ~131 MB with vtk); without it nothing is claimed.
