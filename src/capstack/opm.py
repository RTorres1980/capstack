"""Option Pricing Method (OPM) allocation, backsolve and DLOM.

The OPM treats each security as a strip of call options on total equity value
struck at the waterfall breakpoints (AICPA Accounting & Valuation Guide,
"Valuation of Privately-Held-Company Equity Securities Issued as Compensation").
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .captable import CapTable
from .waterfall import breakpoints, payoff


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(spot: float, strike: float, years: float, rate: float, vol: float) -> float:
    if strike <= 0:
        return spot
    if years <= 0 or vol <= 0:
        return max(spot - strike * math.exp(-rate * max(years, 0)), 0.0)
    sd = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / sd
    d2 = d1 - sd
    return spot * _ncdf(d1) - strike * math.exp(-rate * years) * _ncdf(d2)


@dataclass
class Tranche:
    lower: float
    upper: float | None
    call_value: float
    participation: dict[str, float]  # share of each $ in this tranche


@dataclass
class Allocation:
    equity_value: float
    years: float
    rate: float
    vol: float
    values: dict[str, float]
    per_share: dict[str, float]
    tranches: list[Tranche]


def allocate(
    table: CapTable,
    equity_value: float,
    years: float,
    rate: float,
    vol: float,
    _breakpoints: list[float] | None = None,
) -> Allocation:
    bps = [0.0] + (breakpoints(table) if _breakpoints is None else _breakpoints)
    calls = [bs_call(equity_value, b, years, rate, vol) for b in bps] + [0.0]
    values = {s.name: 0.0 for s in table.securities}
    tranches: list[Tranche] = []
    for i, lower in enumerate(bps):
        upper = bps[i + 1] if i + 1 < len(bps) else None
        width = (upper - lower) if upper is not None else max(lower, 1.0)
        a, b = lower + 0.25 * width, lower + 0.75 * width
        pa, pb = payoff(table, a), payoff(table, b)
        slope = {k: (pb[k] - pa[k]) / (b - a) for k in pa}
        strip = calls[i] - calls[i + 1]
        for k, w in slope.items():
            values[k] += w * strip
        tranches.append(Tranche(lower, upper, strip, slope))
    per_share = {s.name: values[s.name] / s.shares for s in table.securities}
    return Allocation(equity_value, years, rate, vol, values, per_share, tranches)


def backsolve(
    table: CapTable, anchor: str, price: float, years: float, rate: float, vol: float
) -> Allocation:
    """Find the total equity value at which `anchor` is worth `price` per share."""
    anchor_sec = table.get(anchor)
    bps = breakpoints(table)

    def value_at(v: float) -> float:
        return allocate(table, v, years, rate, vol, bps).per_share[anchor_sec.name]

    lo, hi = 1.0, max(table.total_liquidation_preference, price * table.fully_diluted_shares, 1.0)
    while value_at(hi) < price:
        hi *= 2
        if hi > 1e15:
            raise ValueError("backsolve did not bracket; check inputs")
    for _ in range(120):
        mid = (lo + hi) / 2
        if value_at(mid) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < 0.01:
            break
    return allocate(table, (lo + hi) / 2, years, rate, vol, bps)


def dlom_finnerty(years: float, vol: float) -> float:
    """Finnerty (2012) average-strike put DLOM, as a fraction of marketable value."""
    s2t = vol * vol * years
    if s2t <= 0:
        return 0.0
    inner = s2t + math.log(2 * (math.exp(s2t) - s2t - 1)) - 2 * math.log(math.exp(s2t) - 1)
    v_sqrt_t = math.sqrt(max(inner, 0.0))
    return _ncdf(v_sqrt_t / 2) - _ncdf(-v_sqrt_t / 2)


def dlom_chaffe(years: float, vol: float, rate: float = 0.0) -> float:
    """Chaffe (1993) at-the-money European protective put, as a fraction of value."""
    put = bs_call(1.0, 1.0, years, rate, vol) - 1.0 + math.exp(-rate * years)
    return put


@dataclass
class CommonValue:
    marketable_per_share: float
    dlom: float
    fmv_per_share: float
    method: str


def common_fmv(alloc: Allocation, table: CapTable, dlom_method: str = "finnerty") -> CommonValue:
    commons = table.by_kind("common")
    total = sum(alloc.values[c.name] for c in commons)
    shares = sum(c.shares for c in commons)
    marketable = total / shares
    if dlom_method == "finnerty":
        d = dlom_finnerty(alloc.years, alloc.vol)
    elif dlom_method == "chaffe":
        d = dlom_chaffe(alloc.years, alloc.vol, alloc.rate)
    elif dlom_method == "none":
        d = 0.0
    else:
        raise ValueError(f"unknown DLOM method {dlom_method!r}")
    return CommonValue(marketable, d, marketable * (1 - d), dlom_method)
