# Drawing → CAD (2026-10-18)

`astracad_core.drawing_to_cad` rebuilds a part from its 2D drawing (DXF) as a real SOLIDWORKS feature model
(a sketch of the drawing's own lines and arcs, extruded), then the part goes through the same permission gate and
the same verification as every other build: SOLIDWORKS kernel volume and area against the prediction, and an
independent re-read of the exported STEP.

    py -m astracad_cad from-drawing <drawing.dxf> --out OUT --authorize KM [--through] [--thickness MM] [--scale K]
    workspace: Projects -> a DXF drawing -> 3D

## What this version rebuilds, and how it proves it

1. A part that is **one extrusion**: a constant section through a constant thickness, with openings that go all the
way through (plates, brackets cut from plate, flanges, gaskets, spacers with holes).

| Step | Rule |
|---|---|
| Views | Edges grouped by clear space; a sheet frame (and the title block standing on it) is set aside only when no dimension measures it and it has a title block or a standard sheet size - a single view's own outline is never dropped |
| Line types | visible / hidden (HIDDEN, DASHED ...) / centre lines, from the entity or its layer |
| Scale | each view's own dimensions (DXF measurement x DIMLFAC); else one factor all the sheet's dimensions share; else the sheet's written scale ("SCALE: 1:2") when no dimension contradicts it; else the user's `--scale`. A dimension whose text contradicts its geometry stops the rebuild |
| Section | the one view whose visible edges close into an outline and inner loops (lines, arcs, circles), with no hidden edge and no loose edge; copied exactly, arcs as arcs |
| Thickness | every other view must be aligned with it (same x-range: above/below; same y-range: beside), a plain rectangle of one common depth, and every line across it must run the full depth at a place the section explains (a hole's edge, a tangent edge). A single-view drawing takes the thickness from its "THK" note or `--thickness` |
| Through | an opening is through only when hidden lines show it through the whole depth in the aligned views; a drawing without hidden lines needs the user's `--through` (recorded as stated by the user) |
| Every view | every view on the sheet must be the section or an aligned view; a detail, section or auxiliary view stops the rebuild |

2. A **turned part (revolve)**: an axisymmetric part seen from the side (stepped shaft, cylinder with chamfered ends, hollow tube or bushing with an inner bore).

| Step | Rule |
|---|---|
| Axis | Centre lines in the view or the bounding-box midline; must be strictly horizontal or vertical |
| Symmetry | Upper and lower halves must match symmetrically across the axis within drafting tolerance; asymmetric views are refused |
| Proof of roundness | A single view must carry diameter dimensions ('Ø', '%%c', 'DIA') or a round part note; bare unannotated rectangles are refused to prevent guessing |
| Aligned end view | If present, must consist of concentric circles centered on the shaft axis; outer circles match step radii, smaller circle confirms bore radius |
| Inner bore / tube | Detected from full-span longitudinal hidden lines or concentric inner circles in the end view; through bore confirmed by hidden lines or explicit `--through` |
| Profile & Revolve | Half-profile closed along the axis (or inner bore radius $r_{\text{inner}}$), scaled by view dimensions, and revolved 360° (`sketch.profile` + `feature.revolve`) |
| Volume verification | Verified in closed form using Pappus's centroid theorem ($V = 2\pi \cdot \bar{y} \cdot A$) against analytic cylinder and step volumes |

3. **Feature recognition on rebuilt parts** (`--features`, or standalone `recognize_features(it)`):
Circular inner loops from the 2D section are lifted into parametric CAD features instead of bare sketch cutout loops:
- Circular loops are identified by exact arc-sweep closure and uniform radius.
- Standalone through cutouts become `feature.hole` with exact diameter, depth (equal to part thickness), and center coordinates.
- Blind holes: when aligned views show partial-depth hidden lines touching a boundary face at coordinates matching an inner circular loop, the loop is kept out of `sketch.loops` and emitted as `feature.hole` with explicit depth $d < T$ (and patterned with matching-depth holes). Without `--features`, blind features continue to be refused to prevent through-extrusion errors.
- Rows of collinear, equal-pitch identical holes are decomposed into a seed `feature.hole` and a `feature.pattern.linear`.
- Concentric pitch-circle hole clusters are decomposed into a seed `feature.hole` and a `feature.pattern.circular` (axis: Z, center, count).
- Non-circular cutouts (e.g. rectangular cutouts, slots) remain preserved in `sketch.loops` inner loops.
- Reconstructed part volume and outer bounds are analytically invariant to feature recognition.

Refused, with the reason: steps, bosses, splines and ellipses,
unaligned views, a view without a scale, contradicting dimensions, more than one thickness or scale stated,
turned views lacking proof of roundness, asymmetric profiles across the axis, or blind holes without `--features`.

## Evidence

Round trips on SOLIDWORKS 2026 SP04.1: a part built by ASTraCAD -> `mac drawing` (with `--hidden-lines`, except
where noted) -> `from-drawing` -> SOLIDWORKS again. The rebuilt part's kernel volume equals the original's:

| Part | Original | Rebuilt from the drawing | STEP re-read |
|---|---|---|---|
| plate 100x60x10, R5 corners, 4 x Ø6 | 58654.424808 | 58654.424808 (also once from the workspace, task T-b5cdd4cf96fa) | 10x60x100 |
| block 60x40x20, Ø10 hole (drawing without hidden lines, `--through`) | 46429.203673 | 46429.203673 | 20x40x60 |
| flange Ø120 x 12, 6 x Ø10 on Ø90 PCD | 130061.935859 | 130061.935859 | 12x120x120 |
| block 60x40x20, 5 mm chamfered corners | 47000.000000 | 47000.000000 | 20x40x60 |
| block 60x40x20 with a blind 20x10x5 pocket | 47000.000000 | refused: "the view with the outline also shows hidden edges" | - |

SOLIDWORKS's 27 sample drawings (other designers' parts and assemblies): 0 rebuilt, 0 wrongly; every refusal
names its reason (11 splines / ellipses, edges that do not close into loops, unaligned views, no scale, no
thickness).

## Next

Several extrusions from three views (steps, bosses, blind features read from hidden-line depths),
and vector-PDF drawings (the geometry is in the PDF).
