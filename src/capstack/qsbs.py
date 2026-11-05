"""Qualified Small Business Stock (IRC Section 1202) screening.

Covers both regimes:
* stock issued on or before 4 July 2025 (pre-OBBBA): $50M gross-asset test,
  5-year hold, exclusion % set by acquisition date, $10M per-issuer cap
* stock issued after 4 July 2025 (One Big Beautiful Bill Act): $75M gross-asset
  test, tiered 50% / 75% / 100% at 3 / 4 / 5 years, $15M per-issuer cap

This is a screening aid that shows its work. It is not tax advice; items it
cannot verify from data (redemptions, working-capital assets, holder-level
facts) are surfaced as REVIEW rather than assumed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

OBBBA_DATE = date(2025, 7, 4)

EXCLUDED_FIELDS = (
    "health", "law", "legal", "engineering", "architecture", "accounting", "actuarial",
    "performing arts", "consulting", "athletics", "financial services", "brokerage",
    "banking", "insurance", "financing", "leasing", "investing", "investment management",
    "farming", "mining", "oil", "gas", "extraction", "hotel", "motel", "restaurant",
)


@dataclass
class Check:
    name: str
    status: str  # PASS | FAIL | REVIEW
    detail: str


@dataclass
class LotResult:
    holder: str
    acquired: date
    sold: date | None
    regime: str
    years_held: float
    exclusion_pct: float
    gain: float | None
    checks: list[Check] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return all(c.status != "FAIL" for c in self.checks) and self.exclusion_pct > 0


@dataclass
class HolderResult:
    holder: str
    total_gain: float
    cap: float
    excludable_gain: float
    lots: list[LotResult]


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _years_between(a: date, b: date) -> float:
    full = b.year - a.year - ((b.month, b.day) < (a.month, a.day))
    anniv = a.replace(year=a.year + full) if not (a.month == 2 and a.day == 29) else date(a.year + full, 3, 1)
    nxt = a.replace(year=a.year + full + 1) if not (a.month == 2 and a.day == 29) else date(a.year + full + 1, 3, 1)
    return full + (b - anniv).days / (nxt - anniv).days


def _exclusion(acquired: date, years: float) -> tuple[str, float]:
    if acquired > OBBBA_DATE:
        if years >= 5:
            return "post-OBBBA", 1.00
        if years >= 4:
            return "post-OBBBA", 0.75
        if years >= 3:
            return "post-OBBBA", 0.50
        return "post-OBBBA", 0.0
    if years < 5:
        return "pre-OBBBA", 0.0
    if acquired < date(1993, 8, 11):
        return "pre-1993", 0.0
    if acquired <= date(2009, 2, 17):
        return "pre-OBBBA", 0.50
    if acquired <= date(2010, 9, 27):
        return "pre-OBBBA", 0.75
    return "pre-OBBBA", 1.00


def screen(data: dict, as_of: date | None = None) -> list[HolderResult]:
    issuer = data["issuer"]
    as_of = as_of or date.today()

    issuer_checks: list[Check] = []
    et = str(issuer.get("entity_type", "")).lower().replace("-", "").replace(" ", "")
    issuer_checks.append(
        Check("C corporation", "PASS" if et in ("ccorp", "ccorporation") else "FAIL", f"entity_type={issuer.get('entity_type')}")
    )
    issuer_checks.append(
        Check("Domestic", "PASS" if issuer.get("domestic", False) else "FAIL", f"domestic={issuer.get('domestic')}")
    )
    industry = str(issuer.get("industry", "")).lower()
    hit = [f for f in EXCLUDED_FIELDS if f in industry]
    issuer_checks.append(
        Check("Qualified trade or business",
              "FAIL" if hit else "PASS",
              f"industry '{industry}' matches excluded field(s) {hit}" if hit else f"industry '{industry}' not on excluded list")
    )
    abp = issuer.get("active_business_pct")
    if abp is None:
        issuer_checks.append(Check("80% active-business test", "REVIEW", "active_business_pct not provided"))
    else:
        issuer_checks.append(Check("80% active-business test", "PASS" if abp >= 0.80 else "FAIL", f"{abp:.0%} of assets in active use"))
    issuer_checks.append(Check("Redemptions (1202(c)(3))", "REVIEW", "confirm no disqualifying redemptions around issuance"))

    holders: dict[str, list[LotResult]] = {}
    basis_by_holder: dict[str, float] = {}
    for lot in data["lots"]:
        acquired = _d(lot["acquired"])
        sold = _d(lot.get("sale_date"))
        years = _years_between(acquired, sold or as_of)
        regime, pct = _exclusion(acquired, years)
        limit = 75_000_000 if acquired > OBBBA_DATE else 50_000_000
        checks = list(issuer_checks)
        ga = lot.get("gross_assets_at_issuance", issuer.get("gross_assets_at_issuance"))
        if ga is None:
            checks.append(Check("Gross assets test", "REVIEW", f"no gross-asset figure; limit is ${limit:,.0f}"))
        else:
            checks.append(Check("Gross assets test", "PASS" if ga <= limit else "FAIL", f"${ga:,.0f} vs ${limit:,.0f} limit ({regime})"))
        checks.append(
            Check("Original issuance", "PASS" if lot.get("original_issuance", False) else "FAIL",
                  "acquired directly from issuer" if lot.get("original_issuance") else "secondary purchase is not QSBS")
        )
        min_years = 3 if acquired > OBBBA_DATE else 5
        checks.append(
            Check("Holding period", "PASS" if pct > 0 else "FAIL", f"{years:.2f} years held; minimum {min_years}")
        )
        gain = None
        if sold and "proceeds" in lot:
            gain = float(lot["proceeds"]) - float(lot["basis"])
        res = LotResult(lot["holder"], acquired, sold, regime, round(years, 4), pct if all(c.status != "FAIL" for c in checks) else 0.0, gain, checks)
        holders.setdefault(lot["holder"], []).append(res)
        basis_by_holder[lot["holder"]] = basis_by_holder.get(lot["holder"], 0.0) + float(lot["basis"])

    out: list[HolderResult] = []
    for holder, lots in holders.items():
        any_post = any(l.regime == "post-OBBBA" for l in lots)
        dollar_cap = 15_000_000 if any_post else 10_000_000
        cap = max(dollar_cap, 10 * basis_by_holder[holder])
        gains = [l for l in lots if l.gain is not None and l.gain > 0]
        total_gain = sum(l.gain for l in gains)
        weighted = sum(l.gain * l.exclusion_pct for l in gains)
        excludable = min(weighted, cap)
        if any_post and any(l.regime == "pre-OBBBA" for l in lots):
            lots[0].checks.append(Check("Mixed regimes", "REVIEW", "lots span both regimes; per-issuer cap ordering needs advisor review"))
        out.append(HolderResult(holder, total_gain, cap, excludable, lots))
    return out


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())
