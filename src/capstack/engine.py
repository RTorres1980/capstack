"""Pure compute functions shared by the CLI and `audit replay`.

Each function takes (inputs: name -> file path, params: dict) and returns a
JSON-serialisable outputs dict. Keeping them pure is what makes every ledger
entry reproducible.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from . import fund, opm, portfolio, qsbs
from .captable import load_cap_table


def run_409a(inputs: dict, params: dict) -> dict:
    table = load_cap_table(inputs["cap_table"])
    alloc = opm.backsolve(table, params["anchor"], params["price"], params["years"], params["rate"], params["vol"])
    cv = opm.common_fmv(alloc, table, params.get("dlom_method", "finnerty"))
    return {
        "equity_value": round(alloc.equity_value, 2),
        "values": {k: round(v, 2) for k, v in alloc.values.items()},
        "per_share": {k: round(v, 6) for k, v in alloc.per_share.items()},
        "breakpoints": [round(t.lower, 2) for t in alloc.tranches],
        "tranches": [
            {
                "lower": round(t.lower, 2),
                "upper": None if t.upper is None else round(t.upper, 2),
                "call_value": round(t.call_value, 2),
                "participation": {k: round(v, 6) for k, v in t.participation.items() if v > 1e-9},
            }
            for t in alloc.tranches
        ],
        "common_marketable": round(cv.marketable_per_share, 6),
        "dlom": round(cv.dlom, 6),
        "dlom_method": cv.method,
        "common_fmv": round(cv.fmv_per_share, 4),
    }


def run_qsbs(inputs: dict, params: dict) -> dict:
    as_of = date.fromisoformat(params["as_of"])
    results = qsbs.screen(qsbs.load(inputs["qsbs"]), as_of)
    return {
        "holders": [
            {
                "holder": h.holder,
                "total_gain": round(h.total_gain, 2),
                "cap": round(h.cap, 2),
                "excludable_gain": round(h.excludable_gain, 2),
                "lots": [
                    {
                        "acquired": l.acquired.isoformat(),
                        "sold": l.sold.isoformat() if l.sold else None,
                        "regime": l.regime,
                        "years_held": l.years_held,
                        "exclusion_pct": l.exclusion_pct,
                        "gain": l.gain,
                        "checks": [{"check": c.name, "status": c.status, "detail": c.detail} for c in l.checks],
                    }
                    for l in h.lots
                ],
            }
            for h in results
        ]
    }


def run_capital_call(inputs: dict, params: dict) -> dict:
    notices = fund.allocate_call(
        fund.load_lps(inputs["lps"]), Decimal(params["amount"]), params["basis"], set(params.get("excused", []))
    )
    return {
        "total": str(sum(n.amount for n in notices)),
        "notices": [
            {"lp": n.lp, "share_pct": str(n.share_pct), "amount": str(n.amount),
             "unfunded_after": str(n.unfunded_after), "excused": n.excused}
            for n in notices
        ],
    }


def run_portfolio(inputs: dict, params: dict) -> dict:
    findings = portfolio.check(
        portfolio.load(inputs["kpis"]), params.get("bs_tolerance", 0.005), params.get("min_runway_months", 6.0)
    )
    return {
        "errors": sum(f.severity == "ERROR" for f in findings),
        "warnings": sum(f.severity == "WARN" for f in findings),
        "findings": [f.__dict__ for f in findings],
    }


OPS = {
    "409a.backsolve": run_409a,
    "qsbs.screen": run_qsbs,
    "fund.capital_call": run_capital_call,
    "portfolio.check": run_portfolio,
}
