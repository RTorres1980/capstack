"""Exit waterfall for a cap table at a given total equity value.

Handles stacked / pari passu liquidation preferences, rational conversion of
non-participating preferred, and in-the-money option exercise. The payoff to
each security is piecewise linear in equity value; the kinks are the OPM
breakpoints, found here exactly rather than by hand.
"""
from __future__ import annotations

from collections import defaultdict

from .captable import CapTable, Security


def _pay_preferences(prefs: list[Security], value: float) -> tuple[dict[str, float], float]:
    paid: dict[str, float] = {}
    remaining = value
    levels: dict[int, list[Security]] = defaultdict(list)
    for s in prefs:
        levels[s.seniority].append(s)
    for rank in sorted(levels, reverse=True):
        group = levels[rank]
        owed = sum(s.lp_total for s in group)
        pay = min(remaining, owed)
        for s in group:
            paid[s.name] = pay * s.lp_total / owed if owed else 0.0
        remaining -= pay
    return paid, max(remaining, 0.0)


def _common_price(residual: float, base_shares: float, options: list[Security]) -> tuple[float, list[Security]]:
    """Per-share common value, adding options to the pool while they are in the money."""
    in_money: list[Security] = []
    for opt in sorted(options, key=lambda o: o.strike):
        shares = base_shares + sum(o.shares for o in in_money)
        cash = residual + sum(o.shares * o.strike for o in in_money)
        price = cash / shares if shares else 0.0
        if price > opt.strike:
            in_money.append(opt)
        else:
            break
    shares = base_shares + sum(o.shares for o in in_money)
    cash = residual + sum(o.shares * o.strike for o in in_money)
    return (cash / shares if shares else 0.0), in_money


def solve(table: CapTable, value: float) -> tuple[dict[str, float], float, set[str]]:
    """Return (payoff by security, common value per share, set of converting preferred)."""
    value = max(value, 0.0)
    prefs = table.by_kind("preferred")
    options = table.by_kind("option")
    common_shares = sum(s.shares for s in table.by_kind("common"))
    converting: set[str] = set()

    while True:
        held = [p for p in prefs if p.name not in converting]
        paid, residual = _pay_preferences(held, value)
        base = common_shares + sum(p.as_converted for p in prefs if p.name in converting)
        price, in_money = _common_price(residual, base, options)
        # convert the cheapest-to-convert series that is better off converting
        candidates = sorted(
            (p for p in held if price > p.conversion_threshold + 1e-12),
            key=lambda p: p.conversion_threshold,
        )
        if not candidates:
            break
        converting.add(candidates[0].name)

    payoff: dict[str, float] = {}
    itm = {o.name for o in in_money}
    for s in table.securities:
        if s.kind == "common":
            payoff[s.name] = s.shares * price
        elif s.kind == "preferred":
            payoff[s.name] = s.as_converted * price if s.name in converting else paid.get(s.name, 0.0)
        else:
            payoff[s.name] = s.shares * (price - s.strike) if s.name in itm else 0.0
    return payoff, price, converting


def payoff(table: CapTable, value: float) -> dict[str, float]:
    return solve(table, value)[0]


def breakpoints(table: CapTable) -> list[float]:
    """Total-equity values where the payoff schedule changes slope (excluding 0)."""
    points: set[float] = set()
    # 1) each seniority level of the preference stack being fully covered
    levels: dict[int, float] = defaultdict(float)
    for p in table.by_kind("preferred"):
        levels[p.seniority] += p.lp_total
    running = 0.0
    for rank in sorted(levels, reverse=True):
        running += levels[rank]
        points.add(running)
    total_lp = running

    # 2) common price crossing each option strike / conversion threshold
    thresholds = sorted(
        {o.strike for o in table.by_kind("option")}
        | {p.conversion_threshold for p in table.by_kind("preferred")}
    )
    for t in thresholds:
        lo = total_lp
        hi = max(total_lp, 1.0) * 2
        while solve(table, hi)[1] <= t:
            hi *= 2
        for _ in range(200):
            mid = (lo + hi) / 2
            if solve(table, mid)[1] <= t:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-7 * max(1.0, hi):
                break
        points.add(round((lo + hi) / 2, 6))
    return sorted(p for p in points if p > 0)
