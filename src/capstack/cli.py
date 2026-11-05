"""capstack command-line interface."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import __version__, audit, memo
from .captable import load_cap_table
from .engine import OPS

DEFAULT_LEDGER = "capstack-ledger.jsonl"


def _money(x: float, places: int = 2) -> str:
    return f"${x:,.{places}f}"


def _table(rows: list[list], headers: list[str]) -> str:
    cols = [headers] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cols) for i in range(len(headers))]
    line = lambda r: "  ".join(c.rjust(w) if i else c.ljust(w) for i, (c, w) in enumerate(zip(r, widths)))
    return "\n".join([line(headers), line(["-" * w for w in widths])] + [line(r) for r in cols[1:]])


def _record(args, op: str, inputs: dict, params: dict, outputs: dict) -> None:
    if args.no_ledger:
        return
    e = audit.append(args.ledger, op, inputs, params, outputs)
    print(f"\n[ledger] entry #{e['seq']} {e['hash'][:12]} -> {args.ledger}", file=sys.stderr)


def _emit(args, outputs: dict, text: str) -> None:
    print(json.dumps(outputs, indent=2) if args.json else text)


# ---------------------------------------------------------------- commands
def cmd_captable(args) -> int:
    t = load_cap_table(args.cap_table)
    rows = [
        [r["security"], r["kind"], f"{r['shares']:,.0f}", f"{r['fd_pct']:.2%}",
         _money(r["liq_pref"], 0), "" if r["strike"] is None else _money(r["strike"], 4)]
        for r in t.summary_rows()
    ]
    print(_table(rows, ["Security", "Kind", "Shares", "FD %", "Liq pref", "Strike"]))
    print(f"\nFully diluted: {t.fully_diluted_shares:,.0f}   Total liquidation preference: {_money(t.total_liquidation_preference, 0)}")
    return 0


def cmd_409a(args) -> int:
    inputs = {"cap_table": args.cap_table}
    params = {"anchor": args.anchor, "price": args.price, "years": args.years, "rate": args.rate,
              "vol": args.vol, "dlom_method": args.dlom}
    out = OPS["409a.backsolve"](inputs, params)
    lines = [
        f"OPM backsolve to {args.anchor} @ {_money(args.price, 4)}",
        f"Implied total equity value: {_money(out['equity_value'], 0)}\n",
        _table([[k, _money(v, 0), _money(out['per_share'][k], 4)] for k, v in out["values"].items()],
               ["Security", "Value", "Per share"]),
        "\nBreakpoint tranches",
        _table(
            [[f"{_money(t['lower'], 0)} - {'∞' if t['upper'] is None else _money(t['upper'], 0)}",
              _money(t["call_value"], 0),
              ", ".join(f"{k} {v:.1%}" for k, v in t["participation"].items())]
             for t in out["tranches"]],
            ["Equity value range", "Tranche value", "Participation"]),
        f"\nCommon (marketable): {_money(out['common_marketable'], 4)}/sh",
        f"DLOM ({out['dlom_method']}): {out['dlom']:.2%}",
        f"Concluded common FMV: {_money(out['common_fmv'], 4)}/sh",
    ]
    _emit(args, out, "\n".join(lines))
    _record(args, "409a.backsolve", inputs, params, out)
    return 0


def cmd_qsbs(args) -> int:
    inputs = {"qsbs": args.file}
    params = {"as_of": args.as_of or date.today().isoformat()}
    out = OPS["qsbs.screen"](inputs, params)
    lines = []
    for h in out["holders"]:
        lines.append(f"\n{h['holder']}: gain {_money(h['total_gain'], 0)}, cap {_money(h['cap'], 0)}, "
                     f"excludable {_money(h['excludable_gain'], 0)}")
        for l in h["lots"]:
            lines.append(f"  lot acquired {l['acquired']} ({l['regime']}, {l['years_held']:.2f} yrs) -> "
                         f"{l['exclusion_pct']:.0%} exclusion")
            for c in l["checks"]:
                lines.append(f"    [{c['status']:<6}] {c['check']}: {c['detail']}")
    lines.append("\nScreening aid only - not tax advice. REVIEW items need confirmation.")
    _emit(args, out, "\n".join(lines).lstrip())
    _record(args, "qsbs.screen", inputs, params, out)
    return 0


def cmd_call(args) -> int:
    inputs = {"lps": args.lps}
    params = {"amount": str(args.amount), "basis": args.basis,
              "excused": sorted(x.strip() for x in args.excuse.split(",") if x.strip()) if args.excuse else []}
    out = OPS["fund.capital_call"](inputs, params)
    rows = [[n["lp"], f"{n['share_pct']}%", _money(float(n["amount"])), _money(float(n["unfunded_after"])),
             "excused" if n["excused"] else ""] for n in out["notices"]]
    text = _table(rows, ["LP", "Share", "Call", "Unfunded after", ""]) + f"\n\nTotal called: {_money(float(out['total']))} (ties to the cent)"
    if args.out:
        from .fund import allocate_call, load_lps, write_notices
        from decimal import Decimal
        write_notices(allocate_call(load_lps(args.lps), Decimal(params["amount"]), args.basis, set(params["excused"])), args.out)
        text += f"\nNotices written to {args.out}"
    _emit(args, out, text)
    _record(args, "fund.capital_call", inputs, params, out)
    return 0


def cmd_portfolio(args) -> int:
    inputs = {"kpis": args.kpis}
    params = {"bs_tolerance": args.bs_tolerance, "min_runway_months": args.min_runway}
    out = OPS["portfolio.check"](inputs, params)
    rows = [[f["severity"], f["company"], f["period"], f["rule"], f["detail"]] for f in out["findings"]]
    text = (_table(rows, ["Sev", "Company", "Period", "Rule", "Detail"]) if rows else "No findings.")
    text += f"\n\n{out['errors']} error(s), {out['warnings']} warning(s)"
    _emit(args, out, text)
    _record(args, "portfolio.check", inputs, params, out)
    return 1 if out["errors"] and args.strict else 0


def cmd_audit_verify(args) -> int:
    problems = audit.verify(args.ledger, check_inputs=args.inputs)
    n = len(audit.read(args.ledger))
    if problems:
        print("\n".join(problems))
        print(f"\nFAILED: {len(problems)} problem(s) across {n} entries")
        return 1
    print(f"OK: {n} entries, hash chain intact" + (", inputs unchanged" if args.inputs else ""))
    return 0


def cmd_audit_replay(args) -> int:
    entries = audit.read(args.ledger)
    e = next((x for x in entries if x["seq"] == args.seq), None)
    if e is None:
        print(f"no entry #{args.seq}")
        return 1
    inputs = {k: v["path"] for k, v in e["inputs"].items()}
    for k, v in e["inputs"].items():
        if audit.sha256_file(v["path"]) != v["sha256"]:
            print(f"input {k} has changed since entry #{args.seq}; replay would not be like-for-like")
            return 1
    fresh = json.loads(json.dumps(OPS[e["op"]](inputs, e["params"])))
    if fresh == e["outputs"]:
        print(f"REPRODUCED: entry #{args.seq} ({e['op']}) matches exactly")
        return 0
    diffs = [k for k in set(fresh) | set(e["outputs"]) if fresh.get(k) != e["outputs"].get(k)]
    print(f"MISMATCH in entry #{args.seq}: fields {sorted(diffs)} differ (recorded with v{e['version']})")
    return 1


def cmd_memo(args) -> int:
    entries = [e for e in audit.read(args.ledger) if e["op"] == "409a.backsolve"]
    if not entries:
        print("no 409A entries in ledger; run `capstack 409a` first")
        return 1
    e = next((x for x in entries if x["seq"] == args.seq), None) if args.seq else entries[-1]
    facts = memo.facts_from_entry(e)
    text, source = memo.ai_memo(facts) if args.ai else (memo.template_memo(facts), "template")
    print(text)
    print(f"\n[source: {source}]", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="capstack", description="Auditable private-capital operations toolkit")
    p.add_argument("--version", action="version", version=f"capstack {__version__}")
    p.add_argument("--ledger", default=DEFAULT_LEDGER, help="audit ledger path (default: %(default)s)")
    p.add_argument("--no-ledger", action="store_true", help="do not record this run")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    # same flags accepted after the subcommand too
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ledger", default=argparse.SUPPRESS)
    common.add_argument("--no-ledger", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("captable", parents=[common], help="validate and summarise a cap table")
    s.add_argument("cap_table")
    s.set_defaults(fn=cmd_captable)

    s = sub.add_parser("409a", parents=[common], help="OPM backsolve and common-stock FMV")
    s.add_argument("cap_table")
    s.add_argument("--anchor", required=True, help="security priced in the latest round")
    s.add_argument("--price", type=float, required=True, help="round price per share")
    s.add_argument("--years", type=float, required=True, help="expected term to liquidity")
    s.add_argument("--vol", type=float, required=True, help="equity volatility, e.g. 0.55")
    s.add_argument("--rate", type=float, required=True, help="risk-free rate matching term, e.g. 0.039")
    s.add_argument("--dlom", choices=["finnerty", "chaffe", "none"], default="finnerty")
    s.set_defaults(fn=cmd_409a)

    s = sub.add_parser("qsbs", parents=[common], help="Section 1202 QSBS screen")
    s.add_argument("file")
    s.add_argument("--as-of", help="evaluation date for unsold lots (YYYY-MM-DD)")
    s.set_defaults(fn=cmd_qsbs)

    s = sub.add_parser("capital-call", parents=[common], help="allocate a capital call across LPs")
    s.add_argument("lps")
    s.add_argument("--amount", required=True)
    s.add_argument("--basis", choices=["commitment", "unfunded"], default="commitment")
    s.add_argument("--excuse", help="comma-separated LPs excused from this call")
    s.add_argument("--out", help="write LP notices CSV here")
    s.set_defaults(fn=cmd_call)

    s = sub.add_parser("portfolio", parents=[common], help="check portfolio-company KPI submissions")
    s.add_argument("kpis")
    s.add_argument("--bs-tolerance", type=float, default=0.005)
    s.add_argument("--min-runway", type=float, default=6.0)
    s.add_argument("--strict", action="store_true", help="exit 1 if any ERROR")
    s.set_defaults(fn=cmd_portfolio)

    a = sub.add_parser("audit", parents=[common], help="verify or replay the ledger")
    asub = a.add_subparsers(dest="audit_cmd", required=True)
    s = asub.add_parser("verify", parents=[common])
    s.add_argument("--inputs", action="store_true", help="also confirm input files are unchanged")
    s.set_defaults(fn=cmd_audit_verify)
    s = asub.add_parser("replay", parents=[common])
    s.add_argument("seq", type=int)
    s.set_defaults(fn=cmd_audit_replay)

    s = sub.add_parser("memo", parents=[common], help="draft a 409A summary memo from the ledger")
    s.add_argument("--seq", type=int)
    s.add_argument("--ai", action="store_true", help="AI prose with number guard (needs ANTHROPIC_API_KEY)")
    s.set_defaults(fn=cmd_memo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
