# Part Language v2 - the contract between planners and both CAD kernels (2026-10-18)

ASTraCAD builds every part twice: natively in SOLIDWORKS (the `mac` runtime, C#) and in OpenCascade
(`astracad_geom`, Python). A part is verified only when both kernels agree. That works only if both read the same
spec the same way, so this document is the single definition. Anything not written here is not part of the
language; an implementation that needs a choice this document does not make must stop and get the document changed
first.

- JSON, never code. Each operation has a fixed argument list; an unknown operation, an unknown argument or a value
  out of range is a validation error, never ignored.
- Units: `mm` and degrees. `"language": 2` at the top of a spec. Every v1 spec stays valid with its v1 meaning
  (v1 = the operations of 2026-10-17: see "v1 compatibility").
- Geometry is defined in model coordinates or in declared frames; edges and faces are chosen by geometric queries
  with an expected count, never by index or by the order a kernel happens to list them.

## 1. Coordinates and frames

Model axes X, Y, Z are right-handed. A **frame** is an origin O and two unit axes x, y in a plane; its normal is
n = x cross y. A 2D sketch point (u, v) in a frame is the model point O + u x + v y.

| Name | O | x | y | n | SOLIDWORKS |
|---|---|---|---|---|---|
| `front` | 0,0,0 | +X | +Y | +Z | Front Plane |
| `top` | 0,0,0 | +X | -Z | +Y | Top Plane |
| `right` | 0,0,0 | -Z | +Y | +X | Right Plane |

The `top` and `right` rows are SOLIDWORKS's normal-to view orientations; `mac` measures the actual sketch transform
of each named plane once (`ISketch.ModelToSketchTransform`) and must report a mismatch rather than work around it.

**`ref.plane`** - a named frame for later sketches, two forms:

    {"$op": "ref.plane", "id": "deck_a", "args": {"origin": [0, 0, 200], "normal": [0, 0.5, 0.8660254], "x_dir": [1, 0, 0]}}
    {"$op": "ref.plane", "id": "p20",    "args": {"base": "front", "offset": 20}}

- Explicit form: `normal` and `x_dir` are normalised by the reader; they must be perpendicular (|n . x| <= 1e-9
  after normalising), else a validation error. y = n cross x.
- Offset form: `base` is a named plane or an earlier `ref.plane`; the new frame is the base frame moved by `offset`
  along the base normal (axes unchanged).
- SOLIDWORKS note (`mac`): a reference plane through O normal to n; sketch geometry is mapped frame -> model ->
  sketch coordinates with `ModelToSketchTransform`, so the plane's own internal axes never matter.

## 2. Sketches

`sketched_on` is a named plane or a `ref.plane` id. All sketch coordinates are in that frame (u, v).

- `sketch.rectangle` {width, height, x, y} - corner at (x, y), sides along u and v (v1).
- `sketch.circle` {radius | diameter, x, y} (v1).
- `sketch.loops` {loops: [[seg, ...], ...]} with seg = `["L", u1, v1, u2, v2]` | `["A", cu, cv, su, sv, eu, ev, dir]`
  (dir +1 counter-clockwise about n, -1 clockwise) | `["C", cu, cv, r]` (a loop on its own). The largest loop is
  the outside, every other loop an opening (v1).
- `sketch.profile` {points} - closed polygon for a v1 revolve about the frame's u axis (v1).

**Consumption.** A sketch is used by exactly one feature: the next sketch-consuming feature (v1 rule), or a later
feature that names it (`"sketch": "s3"`, sweep `"profile"`, loft `"sections"`). A sketch never used is an error.

## 3. Features

Every solid feature acts on a **body**: `"main"` unless it says `"new_body": "<id>"` (creates a separate body) or
`"body": "<id>"` (acts on that body). A part may end with several bodies only when the spec says
`"expect": {"bodies": N}`.

### 3.1 `feature.extrude` / `feature.cut`
Args: `depth` > 0 | `through_all: true` (cut only); `direction`: `"normal"` (along n) | `"reverse"` | `"both"`
(symmetric: `depth` on each side); `sketch` (optional id); `new_body` / `body`. Any closed sketch (rectangle,
circle, loops) may be extruded or cut.

Defaults keep v1: `feature.extrude` direction `"normal"`; `feature.cut` direction `"both"` (v1 cut removes material
within `depth` of the sketch plane on both sides; `mac` has always run cuts symmetrically).

Expectation: area x depth (loops by Green's theorem, arcs exact); a cut subtracts its intersection with the
material, which `mac` derives only where the intersection is known (as in v1); otherwise the other kernel decides.

### 3.2 `feature.revolve` / `feature.cut_revolve`
Args: `angle` in (0, 360] (default 360); `axis`: `"u"` (the frame's u axis through O - the v1 axis) | `"v"` |
`{"line": [[u1, v1], [u2, v2]]}` in the sketch frame; `direction` for angle < 360: `"normal"` (right-hand rule about
the axis direction, from the sketch plane) | `"reverse"` | `"both"` (angle split evenly); `sketch` (optional).
The profile (loops, rectangle, circle or v1 profile) must lie entirely on one side of the axis (touching allowed);
checked exactly. Expectation: Pappus (area x centroid distance x angle) for any loops.

### 3.3 `feature.sweep`
Args: `profile` (sketch id, closed); `path`:
- `{"segments": [["L", [x1,y1,z1], [x2,y2,z2]], ["A", [x1,y1,z1], [xm,ym,zm], [x2,y2,z2]], ...]}` - lines and arcs
  through three points, connected end to start; or
- `{"helix": {"origin": [..], "axis": [..], "radius": r, "pitch": p, "turns": t, "start_dir": [..]}}` -
  right-handed, starting at origin + r * start_dir;

`operation`: `"add"` | `"cut"`; `new_body` / `body`. The profile plane must contain the path's start point and be
perpendicular to its start tangent (within 1e-9). Profile orientation: along lines and arcs the profile keeps its
attitude to the path (no twist); along a helix it follows the helix's Frenet frame (it turns with the helix, as a
spring's wire does).
Expectation: lines and arcs in one plane - Pappus per segment (exact); helix - no closed form (the other kernel).

### 3.4 `feature.loft` (piecewise ruled)
Args: `sections`: two or more sketch ids, each one closed loop with the **same number of segments**, all lines
(polygons) or each a single circle; vertex i of one section joins vertex i of the next, starting at the first
segment's start point; `operation` add | cut; `new_body` / `body`. The solid is the union of the ruled lofts between
consecutive sections - both kernels build exactly that (no smooth lofts in v2).
Expectation: between parallel section planes, the prismatoid formula h/6 (A1 + 4 Am + A2) per pair (exact).

### 3.5 `feature.shell`
Args: `thickness` > 0 (inward); `remove`: `{"faces": <face query>}` (section 4), the faces left open; `body`.
Inner walls meet at sharp edges (the offset faces are intersected, not rounded).
Expectation: no closed form in general (the other kernel decides).

### 3.6 `feature.fillet` / `feature.chamfer`
Args: `radius` (fillet) | `distance` (+ optional `angle`, chamfer, default 45); `select`: v1 `"corners"` /
`"end_circles"`, or `{"edges": <edge query>}`. v1 closed forms stay; others: the other kernel decides.

### 3.7 `feature.hole`
Args: `diameter`; `depth` | `through_all`; `centers`: `[[u, v], ...]` in the `sketched_on` frame (or v1 `center`);
`type`: `"simple"` | `"counterbore"` (`cbore_diameter`, `cbore_depth`) | `"countersink"` (`csk_diameter`,
`csk_angle`, default 90); `direction` `"normal"` | `"reverse"` | `"both"` (default `"both"` = v1; a counterbore or
countersink needs `"normal"` or `"reverse"`: its entry is the sketch plane, its drill goes that way); `thread`:
optional label such as `"M8x1.25"` (a cosmetic thread in SOLIDWORKS, an attribute in OpenCascade - the geometry
is the drilled `diameter`). Expectation: cylinders and cones, exact.

### 3.8 `feature.mirror`
Args: `plane` (named or `ref.plane` id); `features`: [ids] (each mirrored feature is applied again, mirrored, to the
body it acted on) | `body: true` (the body and its mirror image are joined). Expectation: the other kernel.

### 3.9 `feature.pattern.linear` / `feature.pattern.circular`
Linear: `feature` (or `features: [...]`), `count` 2..1000, `spacing`, `direction`: `"X"|"Y"|"Z"` (v1) or a vector;
optional `count2`, `spacing2`, `direction2`. Circular: `feature(s)`, `count`, `angle` total (default 360, equal
spacing), `axis`: v1 `"X"|"Y"|"Z"` with v1 `center`, or `{"origin": [x,y,z], "direction": [x,y,z]}`.
Instances are copies of the seed's tool body applied with the seed's operation. Expectation: v1 rule (seed delta x
count) where instances neither overlap nor leave the body; otherwise the other kernel.

### 3.10 `body.boolean`
Args: `op`: `"add"` | `"subtract"` | `"intersect"`; `target` (body id); `tools`: [body ids] (consumed).

## 4. Queries (edges and faces)

    {"edges": {"kind": "line", "parallel_to": "Z", "at_extreme": {"axis": "Z", "which": "max"}, "expect_count": 4}}
    {"faces": {"kind": "plane", "normal": "+Z", "at_extreme": {"axis": "Z", "which": "max"}, "expect_count": 1}}

| Field | Edges | Faces | Meaning |
|---|---|---|---|
| `kind` | `line` / `circle` / `any` | `plane` / `cylinder` / `any` | geometry type |
| `parallel_to` | line direction or circle axis | cylinder axis | `"X"`, `"Y"`, `"Z"` or a vector, sign ignored |
| `normal` | - | outward normal of a plane face | `"+Z"`, `"-X"` or a vector |
| `at` | every point of the edge | every point of the face | `{"axis": "Z", "value": 20}` (tolerance 1e-6 mm) |
| `at_extreme` | | | `{"axis": "Z", "which": "max" or "min"}` of the current body's bounding box |
| `radius` | circles | cylinders | 1e-6 mm |
| `within_box` | | | `[xmin, ymin, zmin, xmax, ymax, zmax]`, entity's bounding box inside |
| `convex` | `true` / `false` | - | dihedral angle at the edge, measured inside the material |
| `expect_count` | required | required | both kernels must find exactly this many, else the step fails |

All fields given must hold (AND). Each kernel evaluates the query on its own B-rep of the body as it is when the
feature runs.

## 5. Interfaces (for assemblies)

A part may declare named frames in its own coordinates, used only by assemblies (not built):

    "interfaces": {"pin_axis": {"origin": [0, 0, 32], "z": [1, 0, 0], "x": [0, 1, 0]}}

## 6. Verification classes

A part's class is the loosest class of its operations. Two-kernel tolerances are measured per class and recorded
in docs/engineering/two-kernel-verification.md; until measured, the starting values are:

| Class | Operations | Volume, area (relative) | Bounding box, centre of mass |
|---|---|---|---|
| E exact | planes, extrude, cut, revolve, holes, patterns, mirror, ruled lofts, line/arc sweeps, booleans | 1e-6 | 1e-6 mm x max(1, size) |
| B blends | query fillets / chamfers, shell | 1e-4 | 1e-4 x size |
| F free-form | helix sweeps | 1e-3 | 1e-3 x size |

## 7. v1 compatibility

v1 operations (`sketch.rectangle`, `sketch.circle`, `sketch.profile`, `sketch.loops`, `feature.extrude`,
`feature.cut`, `feature.revolve`, `feature.hole`, `feature.fillet`, `feature.chamfer`, `feature.pattern.linear`,
`feature.pattern.circular`, `feature.parameter`, `doc.note`) keep their meaning; a spec without `"language": 2`
may use only them, on the named planes.

## 8. Assemblies (preview of M2; defined in docs/engineering/assembly-spec.md when built)

Components reference parts (a spec or a generator call) and are placed by 4x4 transforms computed by the template
from interfaces; mates are recorded so SOLIDWORKS can confirm them; allowed contacts are declared, every other
overlap is an interference.
