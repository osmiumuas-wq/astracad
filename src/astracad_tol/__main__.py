"""python -m astracad_tol <stack|fit> input.json  — prints the result as JSON.

stack input: {"contributors":[{name,nominal,lower,upper,unit,direction,distribution,
              sigma_multiple}], "spec":[lsl,usl], "methods":["worst_case","rss","monte_carlo"],
              "coverage":3, "samples":200000, "seed":1}
fit input:   {"nominal":20, "hole":"H7", "shaft":"g6"}  or  {"hole":[lo,hi], "shaft":[lo,hi]}
"""
import json
import sys

from . import fits, stack


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2 or argv[0] not in ("stack", "fit"):
        print(__doc__)
        return 2
    data = json.load(open(argv[1], encoding="utf-8"))
    try:
        if argv[0] == "fit":
            if isinstance(data["hole"], str):
                r = fits.iso_fit(data["nominal"], data["hole"], data["shaft"])
            else:
                r = fits.evaluate(tuple(data["hole"]), tuple(data["shaft"]), data.get("unit", "mm"))
            print(json.dumps(r.to_dict(), indent=2))
            return 0
        cs = [stack.Contributor(**c) for c in data["contributors"]]
        spec = tuple(data["spec"]) if data.get("spec") else None
        out = {}
        for m in data.get("methods", ["worst_case"]):
            if m == "worst_case":
                out[m] = stack.worst_case(cs, spec).to_dict()
            elif m == "rss":
                out[m] = stack.rss(cs, data.get("coverage", 3.0), spec).to_dict()
            elif m == "monte_carlo":
                out[m] = stack.monte_carlo(cs, data["samples"], data["seed"], spec).to_dict()
        print(json.dumps(out, indent=2))
        return 0
    except (stack.StackError, fits.FitError, KeyError, TypeError) as ex:
        print(f"REFUSED: {ex}")
        return 1


raise SystemExit(main())
