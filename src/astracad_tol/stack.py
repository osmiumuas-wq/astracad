"""One-dimensional tolerance stack analysis: worst case, RSS and Monte Carlo.

Inputs (per contributor): name, nominal, lower and upper limit (or deviations), unit, direction
(+1 adds to the closing gap, -1 subtracts) and — for any statistical method — an explicitly
stated distribution. Nothing defaults silently:

- A contributor without both limits is rejected (a nominal is never used as a limit).
- Mixed units are rejected (convert first; the caller states the conversion).
- RSS requires each contributor's ``sigma_multiple`` k (tolerance half-width = k·σ). A common
  choice is k = 3, but it is an assumption about the process, so it is an input, not a default.
- Monte Carlo requires a distribution per contributor: normal (with k), uniform, or triangular
  (mode at the midpoint unless ``mode`` is given), all truncated to the stated limits except normal,
  whose tails beyond the limits are kept and counted (that is the scrap the method predicts).

Equations
  closing nominal   G0  = Σ d_i n_i
  worst case        Gmax = Σ (d_i > 0 ? U_i : L_i)·d_i ;  Gmin = Σ (d_i > 0 ? L_i : U_i)·d_i
  RSS (centred)     Gμ = Σ d_i m_i  (m_i limit midpoints);  σ_G = sqrt(Σ (h_i / k_i)^2);
                    reported band Gμ ± K·σ_G with K the requested coverage multiple
  Monte Carlo       empirical distribution of Σ d_i X_i, X_i ~ declared distribution
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from statistics import NormalDist


class StackError(ValueError):
    pass


@dataclass(frozen=True)
class Contributor:
    name: str
    nominal: float
    lower: float               # lower LIMIT (absolute), not a deviation
    upper: float               # upper LIMIT
    unit: str
    direction: int = 1
    distribution: str | None = None   # "normal" | "uniform" | "triangular"
    sigma_multiple: float | None = None
    mode: float | None = None
    source: str = ""

    @classmethod
    def from_deviations(cls, name: str, nominal: float, upper_dev: float, lower_dev: float,
                        unit: str, **kw) -> "Contributor":
        return cls(name, nominal, round(nominal + lower_dev, 12), round(nominal + upper_dev, 12),
                   unit, **kw)

    @property
    def mid(self) -> float:
        return (self.lower + self.upper) / 2

    @property
    def half(self) -> float:
        return (self.upper - self.lower) / 2

    def validate(self) -> list[str]:
        e = []
        if not self.name:
            e.append("contributor without a name")
        for label, v in (("nominal", self.nominal), ("lower", self.lower), ("upper", self.upper)):
            if v is None or not isinstance(v, (int, float)) or math.isnan(v):
                e.append(f"{self.name}: {label} limit is missing — not substituted by the nominal")
        if e:
            return e
        if self.upper < self.lower:
            e.append(f"{self.name}: upper limit {self.upper} is below lower limit {self.lower}")
        if not (self.lower - 1e-12 <= self.nominal <= self.upper + 1e-12):
            # legal on some drawings (e.g. 10 +0.2/+0.1), so this is a note, not an error
            pass
        if self.direction not in (1, -1):
            e.append(f"{self.name}: direction must be +1 or -1")
        if self.distribution not in (None, "normal", "uniform", "triangular"):
            e.append(f"{self.name}: unknown distribution {self.distribution!r}")
        if self.sigma_multiple is not None and self.sigma_multiple <= 0:
            e.append(f"{self.name}: sigma_multiple must be > 0")
        if self.mode is not None and not (self.lower <= self.mode <= self.upper):
            e.append(f"{self.name}: triangular mode outside the limits")
        return e


@dataclass
class StackResult:
    method: str
    unit: str
    nominal_gap: float
    minimum: float
    maximum: float
    mean: float | None = None
    sigma: float | None = None
    assumptions: list[str] = field(default_factory=list)
    spec: tuple[float | None, float | None] | None = None
    verdict: str | None = None            # "meets" | "violates" | "not evaluated"
    fraction_outside: float | None = None
    fraction_outside_ci95: tuple[float, float] | None = None
    samples: int | None = None
    seed: int | None = None
    contributions: dict[str, float] = field(default_factory=dict)  # % of variance (RSS/MC)

    def to_dict(self) -> dict:
        return asdict(self)


def _check(contributors: list[Contributor]) -> str:
    if not contributors:
        raise StackError("a stack needs at least one contributor")
    errs = [m for c in contributors for m in c.validate()]
    units = {c.unit for c in contributors}
    if len(units) > 1:
        errs.append(f"mixed units {sorted(units)}: convert explicitly before stacking")
    names = [c.name for c in contributors]
    if len(set(names)) != len(names):
        errs.append("duplicate contributor names")
    if errs:
        raise StackError("; ".join(errs))
    return units.pop()


def _verdict(lo: float, hi: float, spec) -> str:
    if spec is None or spec == (None, None):
        return "not evaluated"
    lsl, usl = spec
    ok = (lsl is None or lo >= lsl - 1e-12) and (usl is None or hi <= usl + 1e-12)
    return "meets" if ok else "violates"


def worst_case(contributors: list[Contributor], spec=None) -> StackResult:
    unit = _check(contributors)
    g0 = sum(c.direction * c.nominal for c in contributors)
    gmax = sum(c.upper if c.direction > 0 else -c.lower for c in contributors)
    gmin = sum(c.lower if c.direction > 0 else -c.upper for c in contributors)
    return StackResult("worst_case", unit, round(g0, 12), round(gmin, 12), round(gmax, 12),
                       mean=round((gmin + gmax) / 2, 12),
                       assumptions=["every contributor may sit anywhere within its limits, "
                                    "independently; no distribution assumed"],
                       spec=spec, verdict=_verdict(gmin, gmax, spec))


def rss(contributors: list[Contributor], coverage: float = 3.0, spec=None) -> StackResult:
    unit = _check(contributors)
    missing = [c.name for c in contributors if c.sigma_multiple is None]
    if missing:
        raise StackError("RSS needs sigma_multiple (tolerance half-width = k·sigma) for: "
                         + ", ".join(missing))
    if coverage <= 0:
        raise StackError("coverage multiple must be > 0")
    g0 = sum(c.direction * c.nominal for c in contributors)
    mu = sum(c.direction * c.mid for c in contributors)
    var = {c.name: (c.half / c.sigma_multiple) ** 2 for c in contributors}
    total = sum(var.values())
    sigma = math.sqrt(total)
    lo, hi = mu - coverage * sigma, mu + coverage * sigma
    res = StackResult("rss", unit, round(g0, 12), lo, hi, mean=mu, sigma=sigma,
                      assumptions=["contributors independent and normally distributed",
                                   "each centred on its limit midpoint (mean shift from nominal "
                                   "is carried, not ignored)",
                                   "sigma_i = half-width_i / k_i with the stated k_i: "
                                   + ", ".join(f"{c.name}={c.sigma_multiple:g}"
                                               for c in contributors),
                                   f"reported band = mean ± {coverage:g} sigma"],
                      spec=spec, verdict=_verdict(lo, hi, spec),
                      contributions={k: (100 * v / total if total else 0.0)
                                     for k, v in var.items()})
    if spec is not None and sigma > 0:
        lsl, usl = spec
        nd = NormalDist(mu, sigma)
        p = (nd.cdf(lsl) if lsl is not None else 0.0) + \
            ((1 - nd.cdf(usl)) if usl is not None else 0.0)
        res.fraction_outside = p
    return res


def _sample(c: Contributor, rng: random.Random) -> float:
    if c.distribution == "uniform":
        return rng.uniform(c.lower, c.upper)
    if c.distribution == "triangular":
        return rng.triangular(c.lower, c.upper, c.mode if c.mode is not None else c.mid)
    if c.distribution == "normal":
        return rng.gauss(c.mid, c.half / c.sigma_multiple)
    raise StackError(f"{c.name}: no distribution stated")


def _wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def monte_carlo(contributors: list[Contributor], samples: int, seed: int,
                spec=None) -> StackResult:
    unit = _check(contributors)
    if not (100 <= samples <= 5_000_000):
        raise StackError("samples must be within [100, 5,000,000]")
    missing = [c.name for c in contributors if c.distribution is None]
    if missing:
        raise StackError("Monte Carlo needs a stated distribution for: " + ", ".join(missing))
    bad = [c.name for c in contributors if c.distribution == "normal" and not c.sigma_multiple]
    if bad:
        raise StackError("normal distribution needs sigma_multiple for: " + ", ".join(bad))
    rng = random.Random(seed)
    lsl, usl = spec if spec is not None else (None, None)
    s = s2 = 0.0
    lo, hi = math.inf, -math.inf
    out = 0
    for _ in range(samples):
        g = 0.0
        for c in contributors:
            g += c.direction * _sample(c, rng)
        s += g
        s2 += g * g
        lo, hi = min(lo, g), max(hi, g)
        if (lsl is not None and g < lsl) or (usl is not None and g > usl):
            out += 1
    mean = s / samples
    sd = math.sqrt(max(0.0, s2 / samples - mean * mean))
    var_i = {}
    for c in contributors:
        if c.distribution == "uniform":
            v = (c.upper - c.lower) ** 2 / 12
        elif c.distribution == "triangular":
            m = c.mode if c.mode is not None else c.mid
            a, b = c.lower, c.upper
            v = (a * a + b * b + m * m - a * b - a * m - b * m) / 18
        else:
            v = (c.half / c.sigma_multiple) ** 2
        var_i[c.name] = v
    tot = sum(var_i.values())
    g0 = sum(c.direction * c.nominal for c in contributors)
    return StackResult("monte_carlo", unit, round(g0, 12), lo, hi, mean=mean, sigma=sd,
                       assumptions=["contributors independent",
                                    "distributions as stated per contributor: " + ", ".join(
                                        f"{c.name}={c.distribution}" for c in contributors),
                                    f"pseudo-random stream: Python random.Random(seed={seed})",
                                    "min/max are the extremes of the sample, not bounds"],
                       spec=spec, verdict=("not evaluated" if spec is None else
                                           ("meets" if out == 0 else "violates")),
                       fraction_outside=out / samples if spec is not None else None,
                       fraction_outside_ci95=_wilson(out, samples) if spec is not None else None,
                       samples=samples, seed=seed,
                       contributions={k: (100 * v / tot if tot else 0.0) for k, v in var_i.items()})
