# Real SOLIDWORKS drawing output (not synthetic)

Produced 2026-10-17 on SOLIDWORKS 2026 SP04.1 (Educational licence — the sheet carries
"SOLIDWORKS Educational Product. For Instructional Use Only.") by:

    mac drawing _win/sw3/block_60x40x20_hole10.SLDPRT --out _win/drw2 --name block_hole_drw

The part was itself built by `astracad_cad build "60 x 40 x 20 mm block with a 10 mm hole at centre"`.
Files are byte-for-byte what SOLIDWORKS wrote (git stores them with no line-ending conversion).
`block_hole_drw.drawing.json` is mac's own report of that run. Used by tests to exercise the
drawing route's independent DXF check on genuine CAD output without needing SOLIDWORKS.

`block_hole_drw_inch_template.dxf` is the FIRST run of the same command, before the drawing was
switched to MMGS: the installed default template is in inches, so SOLIDWORKS printed 2.36 / 1.57 /
0.79 / ⌀0.39 (inches, 2 decimals) for a 60 x 40 x 20 mm part with a Ø10 hole, and duplicated three
dimensions across views. mac's own report said "ok"; it is kept as a real negative case that the
independent DXF check must reject.
