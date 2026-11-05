"""Fund capital-call allocation that ties to the cent.

Pro-rata by commitment or by unfunded commitment, with optional excused LPs
whose share is reallocated across the remaining LPs. Rounding residue is
distributed with the largest-remainder method so the notices always sum to
the call amount exactly (a common source of reconciliation breaks).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

CENT = Decimal("0.01")


@dataclass
class LP:
    name: str
    commitment: Decimal
    contributed: Decimal

    @property
    def unfunded(self) -> Decimal:
        return self.commitment - self.contributed


@dataclass
class Notice:
    lp: str
    share_pct: Decimal
    amount: Decimal
    contributed_after: Decimal
    unfunded_after: Decimal
    excused: bool


def load_lps(path: str | Path) -> list[LP]:
    lps = []
    for r in csv.DictReader(Path(path).open(newline="", encoding="utf-8-sig")):
        clean = lambda v: Decimal((v or "0").replace(",", "").replace("$", "").strip() or "0")
        lps.append(LP(r["lp"].strip(), clean(r["commitment"]), clean(r.get("contributed"))))
    return lps


def allocate_call(
    lps: list[LP], amount: Decimal | float | str, basis: str = "commitment", excused: set[str] | None = None
) -> list[Notice]:
    amount = Decimal(str(amount)).quantize(CENT)
    excused = excused or set()
    unknown = excused - {lp.name for lp in lps}
    if unknown:
        raise ValueError(f"excused LPs not in roster: {sorted(unknown)}")
    if basis not in ("commitment", "unfunded"):
        raise ValueError("basis must be 'commitment' or 'unfunded'")

    weight = {lp.name: (lp.commitment if basis == "commitment" else lp.unfunded) for lp in lps if lp.name not in excused}
    total_w = sum(weight.values())
    if total_w <= 0:
        raise ValueError("no participating weight to allocate against")

    raw = {n: amount * w / total_w for n, w in weight.items()}
    floored = {n: v.quantize(CENT, rounding=ROUND_DOWN) for n, v in raw.items()}
    leftover = int(((amount - sum(floored.values())) / CENT).to_integral_value())
    order = sorted(raw, key=lambda n: (-(raw[n] - floored[n]), n))
    for n in order[:leftover]:
        floored[n] += CENT

    notices = []
    for lp in lps:
        amt = floored.get(lp.name, Decimal("0.00"))
        if amt > lp.unfunded:
            raise ValueError(f"{lp.name}: call ${amt:,} exceeds unfunded commitment ${lp.unfunded:,}")
        notices.append(
            Notice(
                lp.name,
                (weight.get(lp.name, Decimal(0)) / total_w * 100).quantize(Decimal("0.0001")),
                amt,
                lp.contributed + amt,
                lp.unfunded - amt,
                lp.name in excused,
            )
        )
    assert sum(n.amount for n in notices) == amount
    return notices


def write_notices(notices: list[Notice], path: str | Path) -> None:
    with Path(path).open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["lp", "share_pct", "call_amount", "contributed_after", "unfunded_after", "excused"])
        for n in notices:
            w.writerow([n.lp, n.share_pct, n.amount, n.contributed_after, n.unfunded_after, n.excused])
