import hashlib
import re
from pathlib import Path

import ezdxf

for name in ("dim_sample.dxf", "cadto.dxf"):
    p = Path("_sources/corpus") / name
    print("=" * 70)
    print(name, hashlib.sha256(p.read_bytes()).hexdigest()[:16], "bytes", p.stat().st_size)
    doc = ezdxf.readfile(str(p))
    units = doc.header.get("$INSUNITS", "unset")
    msp = doc.modelspace()
    counts = {}
    for e in msp:
        counts[e.dxftype()] = counts.get(e.dxftype(), 0) + 1
    print("version", doc.dxfversion, "| $INSUNITS", units, "| modelspace", counts)
    print("blocks:", sorted(b.name for b in doc.blocks)[:20])
    for e in msp.query("DIMENSION"):
        m = e.get_measurement()
        try:
            m = round(float(m), 4)
        except (TypeError, ValueError):
            m = tuple(round(float(v), 3) for v in m)
        print("  DIM", e.dxftype(), "| text=", repr(e.dxf.text)[:50], "| dimtype",
              e.dimtype, "| measurement", m)
    for e in msp.query("TEXT MTEXT INSERT"):
        t = (e.text if e.dxftype() == "MTEXT" else e.dxf.text) if e.dxftype() != "INSERT" else ""
        if t and re.search(r"\d", t):
            print("  TXT", repr(t)[:90])
        if e.dxftype() == "INSERT":
            print("  INS block", e.dxf.name, "| attribs",
                  [(a.dxf.tag, a.dxf.text) for a in e.attribs][:8])
            for sub in e.virtual_entities():
                st = sub.dxftype()
                if st in ("TEXT", "MTEXT"):
                    txt = sub.text if st == "MTEXT" else sub.dxf.text
                    if re.search(r"\d", txt):
                        print("      in-block", st, repr(txt)[:80])
