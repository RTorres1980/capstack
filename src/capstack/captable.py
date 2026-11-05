"""Cap table model, loading and validation.

A cap table is a list of securities. Three kinds are supported:

* ``common``     - common stock
* ``preferred``  - non-participating convertible preferred with a liquidation
                   preference (``lp_per_share`` x ``lp_multiple``), a seniority
                   rank (higher = paid first, equal = pari passu) and a
                   conversion ratio into common
* ``option``     - options / warrants on common with a strike price
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("common", "preferred", "option")


@dataclass(frozen=True)
class Security:
    name: str
    kind: str
    shares: float
    lp_per_share: float = 0.0
    lp_multiple: float = 1.0
    seniority: int = 0
    conversion_ratio: float = 1.0
    strike: float = 0.0
    issue_price: float | None = None

    @property
    def lp_total(self) -> float:
        return self.shares * self.lp_per_share * self.lp_multiple if self.kind == "preferred" else 0.0

    @property
    def as_converted(self) -> float:
        return self.shares * self.conversion_ratio if self.kind == "preferred" else self.shares

    @property
    def conversion_threshold(self) -> float:
        """Common value per share above which converting beats taking the preference."""
        return self.lp_per_share * self.lp_multiple / self.conversion_ratio


@dataclass
class CapTable:
    securities: list[Security] = field(default_factory=list)

    def by_kind(self, kind: str) -> list[Security]:
        return [s for s in self.securities if s.kind == kind]

    def get(self, name: str) -> Security:
        for s in self.securities:
            if s.name == name:
                return s
        raise KeyError(f"no security named {name!r}; have {[s.name for s in self.securities]}")

    @property
    def total_liquidation_preference(self) -> float:
        return sum(s.lp_total for s in self.securities)

    @property
    def fully_diluted_shares(self) -> float:
        return sum(s.as_converted for s in self.securities)

    def summary_rows(self) -> list[dict]:
        fd = self.fully_diluted_shares
        return [
            {
                "security": s.name,
                "kind": s.kind,
                "shares": s.shares,
                "as_converted": s.as_converted,
                "fd_pct": s.as_converted / fd if fd else 0.0,
                "liq_pref": s.lp_total,
                "strike": s.strike if s.kind == "option" else None,
            }
            for s in self.securities
        ]


def _num(value: str, default: float = 0.0) -> float:
    value = (value or "").replace(",", "").replace("$", "").strip()
    return float(value) if value else default


def load_cap_table(path: str | Path) -> CapTable:
    rows = list(csv.DictReader(Path(path).open(newline="", encoding="utf-8-sig")))
    secs = []
    for i, r in enumerate(rows, start=2):
        r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        kind = r.get("type", "").lower()
        if kind not in KINDS:
            raise ValueError(f"row {i}: type must be one of {KINDS}, got {kind!r}")
        ip = r.get("issue_price", "")
        secs.append(
            Security(
                name=r["class"],
                kind=kind,
                shares=_num(r.get("shares", "")),
                lp_per_share=_num(r.get("lp_per_share", "")),
                lp_multiple=_num(r.get("lp_multiple", ""), 1.0),
                seniority=int(_num(r.get("seniority", ""))),
                conversion_ratio=_num(r.get("conversion_ratio", ""), 1.0),
                strike=_num(r.get("strike", "")),
                issue_price=_num(ip) if ip else None,
            )
        )
    table = CapTable(secs)
    errors = validate(table)
    if errors:
        raise ValueError("cap table failed validation:\n  - " + "\n  - ".join(errors))
    return table


def validate(table: CapTable) -> list[str]:
    errors: list[str] = []
    names = [s.name for s in table.securities]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        errors.append(f"duplicate class names: {sorted(dupes)}")
    if not table.by_kind("common"):
        errors.append("at least one common class is required")
    for s in table.securities:
        if s.shares <= 0:
            errors.append(f"{s.name}: shares must be positive")
        if s.kind == "preferred":
            if s.lp_per_share <= 0:
                errors.append(f"{s.name}: preferred needs a positive lp_per_share")
            if s.conversion_ratio <= 0:
                errors.append(f"{s.name}: conversion_ratio must be positive")
            if s.lp_multiple <= 0:
                errors.append(f"{s.name}: lp_multiple must be positive")
        if s.kind == "option" and s.strike <= 0:
            errors.append(f"{s.name}: options need a positive strike")
    return errors
