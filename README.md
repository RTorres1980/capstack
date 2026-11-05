# capstack

**Auditable private-capital operations from the command line.**

`capstack` automates four recurring workflows in private-markets operations and records every result in a tamper-evident ledger, so any number in a deliverable can be traced to its exact inputs and reproduced on demand.

| Workflow | Command | What it does |
|---|---|---|
| 409A valuation | `capstack 409a` | OPM backsolve to the latest round, exact breakpoint tranches, Finnerty or Chaffe DLOM, concluded common FMV |
| QSBS attestation | `capstack qsbs` | Section 1202 screen under both the pre- and post-July 4, 2025 (OBBBA) rules, with PASS / FAIL / REVIEW evidence per lot |
| Fund implementation | `capstack capital-call` | Pro-rata capital calls by commitment or unfunded, excused-LP reallocation, notices that tie to the cent |
| Portfolio data collection | `capstack portfolio` | Validates KPI packages: balance-sheet identity, runway, $ vs $000s slips, robust-z outliers |
| Audit | `capstack audit verify` / `replay` | Proves the ledger is untouched and re-runs any entry to reproduce its outputs exactly |
| Reporting | `capstack memo [--ai]` | Drafts a plain-English 409A summary; optional AI prose is checked figure-by-figure against the math |

No runtime dependencies. Python 3.10+.

```bash
pip install -e ".[dev]"
pytest -q
```

---

## What's new here

**1. Every computation is a workpaper.** Each run appends a JSON line holding the operation, SHA-256 fingerprints of the input files, the parameters, the outputs and the hash of the previous entry. Edit, reorder or delete an entry and `audit verify` names the line. Change an input file and `audit verify --inputs` catches it. `audit replay N` recomputes entry N from the original inputs and confirms an exact match, which turns "how did we get this number?" from an email thread into one command.

**2. AI writes, the math decides.** `capstack memo --ai` asks an LLM to make the valuation summary readable for a founder, then extracts every figure from the draft and checks it against the computed facts. A draft that introduces any number the engine did not produce is discarded and the deterministic template is used, and the output says why. Language models help with communication without ever becoming a source of numbers.

**3. Breakpoints are derived, not typed in.** The waterfall engine handles stacked and pari passu preferences, rational conversion of non-participating preferred, and option exercise. OPM breakpoints are solved from that waterfall, so a cap-table change can't leave a hand-entered breakpoint stale.

---

## Walkthrough

### 409A: OPM backsolve

```bash
capstack 409a examples/cap_table.csv --anchor "Series A" --price 2.50 --years 3 --vol 0.55 --rate 0.039
```

```
Implied total equity value: $27,542,595

Security             Value  Per share
Common         $11,404,372    $1.4255
Series Seed     $3,786,734    $1.5147
Series A       $10,000,000    $2.5000
Options @0.30   $1,498,477    $1.2487
Options @0.65     $853,012    $1.0663

Breakpoint tranches
$0 - $10,000,000            Series A 100.0%
$10,000,000 - $12,000,000   Series Seed 100.0%
$12,000,000 - $14,400,000   Common 100.0%
$14,400,000 - $17,620,000   Common 87.0%, Options @0.30 13.0%
$17,620,000 - $19,120,000   Common 80.0%, Options @0.30 12.0%, Options @0.65 8.0%
$19,120,000 - $40,370,000   Common 64.0%, Series Seed 20.0%, Options 16.0%
$40,370,000 - ∞             all classes as-converted

DLOM (finnerty): 20.05%
Concluded common FMV: $1.1397/sh

[ledger] entry #1 b50f33fca7c9
```

Method: each class is valued as a strip of Black-Scholes calls on total equity struck at the breakpoints, following the AICPA guide *Valuation of Privately-Held-Company Equity Securities Issued as Compensation*. Total equity is backsolved so the anchor round prices at its issue price.

### QSBS screen

```bash
capstack qsbs examples/qsbs.json --as-of 2026-11-05
```

Applies the gross-assets test ($50M or $75M by issuance date), the 80% active-business test, excluded fields, original issuance and holding period (5 years, or the 3/4/5-year 50/75/100% tiers for post-OBBBA stock), then the per-issuer cap (greater of $10M / $15M or 10x basis). Facts it cannot verify from data, such as redemptions, are marked REVIEW rather than assumed. It is a screening aid, not tax advice.

### Capital call

```bash
capstack capital-call examples/lps.csv --amount 4250000 --excuse "Uinta Fund of Funds" --out notices.csv
```

Allocations use the largest-remainder method on cents, so notices always sum to the call amount, and any call above an LP's unfunded commitment is refused.

### Portfolio KPI checks

```bash
capstack portfolio examples/kpis.csv --strict
```

```
ERROR  Lumen Health Data    2025Q2  balance_sheet    assets 6,100,000 vs liabilities+equity 5,800,000 (4.9% off)
WARN   Lumen Health Data    2026Q1  runway           1.6 months of runway at current burn
ERROR  Saltflat Robotics    2025Q4  unit_scale       revenue is 1/953x the 2025Q3 figure - likely $ vs $000s
WARN   Copperline Payments  2026Q1  revenue_outlier  QoQ revenue change +202.1% (robust z >+50 vs company history)
```

A quarter that fails the scale check is not used as the baseline for the next quarter, so one bad submission doesn't create a second false alarm. `--strict` exits non-zero on errors, so the check can gate a pipeline.

### Audit

```bash
capstack audit verify --inputs   # OK: 4 entries, hash chain intact, inputs unchanged
capstack audit replay 1          # REPRODUCED: entry #1 (409a.backsolve) matches exactly
capstack memo                    # founder-readable summary citing ledger entry b50f33fca7c9
```

---

## Layout

```
src/capstack/
  captable.py   cap table model, CSV loader, validation
  waterfall.py  exit waterfall, conversion logic, breakpoint solver
  opm.py        Black-Scholes, OPM allocation, backsolve, DLOM models
  qsbs.py       Section 1202 screening (pre- and post-OBBBA)
  fund.py       capital-call allocation and notices
  portfolio.py  KPI submission checks
  audit.py      hash-chained ledger, verification
  engine.py     pure compute functions used by CLI and replay
  memo.py       template memo and number-guarded AI drafting
tests/          19 tests, including hand-checked breakpoints and tamper detection
examples/       sample cap table, LP roster, KPI packages, QSBS lots
```

## Limits

Models non-participating preferred, common and options. Participating preferred, caps, cumulative dividends and warrants with non-standard terms are not yet handled. Inputs such as volatility, term and risk-free rate are user-supplied. Outputs are analytical aids, not valuation opinions or tax advice.

## License

MIT © 2026 Robert Torres
