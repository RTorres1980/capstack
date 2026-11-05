"""Portfolio-company KPI submission checks.

Validates the quarterly packages portfolio companies send to fund managers:
balance-sheet identity, missing fields, cash runway, unit/scale slips
(reporting in $000s one quarter and $ the next) and statistically unusual
swings, using a robust z-score (median / MAD) so one bad quarter cannot hide
itself by inflating the standard deviation.
"""
from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from pathlib import Path

REQUIRED = ("company", "period", "revenue", "cash", "net_burn", "total_assets", "total_liabilities", "total_equity")
NUMERIC = ("revenue", "cash", "net_burn", "total_assets", "total_liabilities", "total_equity", "headcount")


@dataclass
class Finding:
    company: str
    period: str
    severity: str  # ERROR | WARN
    rule: str
    detail: str


def _period_key(p: str) -> tuple[int, int]:
    p = p.upper().replace("-", "").replace(" ", "")
    year, _, q = p.partition("Q")
    return int(year), int(q or 0)


def load(path: str | Path) -> list[dict]:
    rows = []
    for r in csv.DictReader(Path(path).open(newline="", encoding="utf-8-sig")):
        row = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        for k in NUMERIC:
            v = row.get(k, "").replace(",", "").replace("$", "")
            row[k] = float(v) if v not in ("", None) else None
        rows.append(row)
    return rows


def _robust_z(history: list[float], x: float) -> float | None:
    if len(history) < 3:
        return None
    med = statistics.median(history)
    mad = statistics.median(abs(h - med) for h in history)
    if mad == 0:
        return None
    return 0.6745 * (x - med) / mad


def check(rows: list[dict], bs_tolerance: float = 0.005, min_runway_months: float = 6.0) -> list[Finding]:
    out: list[Finding] = []
    by_co: dict[str, list[dict]] = {}
    for r in rows:
        missing = [k for k in REQUIRED if r.get(k) in (None, "")]
        if missing:
            out.append(Finding(r.get("company", "?"), r.get("period", "?"), "ERROR", "missing_fields", ", ".join(missing)))
        by_co.setdefault(r.get("company", "?"), []).append(r)

    for co, hist in by_co.items():
        hist.sort(key=lambda r: _period_key(r["period"]))
        growth: list[float] = []
        prev: dict | None = None  # last period that passed the scale check
        for r in hist:
            per = r["period"]
            a, l, e = r.get("total_assets"), r.get("total_liabilities"), r.get("total_equity")
            if None not in (a, l, e) and a:
                gap = abs(a - (l + e)) / abs(a)
                if gap > bs_tolerance:
                    out.append(Finding(co, per, "ERROR", "balance_sheet",
                                       f"assets {a:,.0f} vs liabilities+equity {l + e:,.0f} ({gap:.1%} off)"))
            cash, burn = r.get("cash"), r.get("net_burn")
            if cash is not None and cash < 0:
                out.append(Finding(co, per, "ERROR", "negative_cash", f"cash {cash:,.0f}"))
            if cash is not None and burn and burn > 0:
                runway = cash / (burn / 3)  # net_burn is quarterly
                if runway < min_runway_months:
                    out.append(Finding(co, per, "WARN", "runway", f"{runway:.1f} months of runway at current burn"))
            scale_bad = False
            if prev is not None:
                for k in ("revenue", "cash", "total_assets"):
                    x, p = r.get(k), prev.get(k)
                    if x and p and p > 0 and x > 0:
                        ratio = x / p
                        if ratio >= 500 or ratio <= 1 / 500:
                            scale_bad = True
                            shown = f"{ratio:,.0f}x" if ratio >= 1 else f"1/{1 / ratio:,.0f}x"
                            out.append(Finding(co, per, "ERROR", "unit_scale",
                                               f"{k} is {shown} the {prev['period']} figure - likely $ vs $000s"))
                rv, pv = r.get("revenue"), prev.get("revenue")
                if not scale_bad and rv is not None and pv:
                    g = rv / pv - 1
                    z = _robust_z(growth, g)
                    if z is not None and abs(z) > 3.5:
                        zs = f"{z:+.1f}" if abs(z) < 50 else (">+50" if z > 0 else "<-50")
                        out.append(Finding(co, per, "WARN", "revenue_outlier",
                                           f"QoQ revenue change {g:+.1%} (robust z {zs} vs company history)"))
                    growth.append(g)
            if not scale_bad:
                prev = r
    return out
